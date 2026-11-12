#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.infra.resolve import InfraResolveError, resolve_infra_refs
from mas.ctl.workspace.config import WorkspaceConfig
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.spec.parser import parse_agent_spec
from mas.runtime.spec.runtime_engine import (
    merge_runtime_engine_layers,
    normalize_runtime_engine_spec,
)


def test_normalize_runtime_engine_spec():
    flat = normalize_runtime_engine_spec(
        {
            "engine": {"queue_depth": 64, "max_auto_steps": 128},
            "stream": True,
        }
    )
    assert flat == {
        "engine_queue_depth": 64,
        "max_auto_steps": 128,
        "stream": True,
    }


def test_resolve_runtime_engine_via_runtime_refs(tmp_path: Path):
    (tmp_path / "runtime.yaml").write_text(
        """
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: test-runtime
spec:
  engine:
    queue_depth: 48
    max_auto_steps: 100
""".strip(),
        encoding="utf-8",
    )
    infra = resolve_infra_refs(
        ["standard:openai"],
        anchor=tmp_path,
        runtime_refs=["runtime.yaml"],
    )
    assert infra.runtime_engine["engine_queue_depth"] == 48
    assert infra.runtime_engine["max_auto_steps"] == 100
    assert infra.runtime_refs == ["runtime.yaml"]


def test_runtime_engine_rejected_in_infra_refs(tmp_path: Path):
    (tmp_path / "runtime.yaml").write_text(
        """
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: test-runtime
spec:
  engine:
    queue_depth: 48
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(InfraResolveError):
        resolve_infra_refs(["runtime.yaml"], anchor=tmp_path)


def test_standard_runtime_default_bundle(tmp_path: Path):
    repo = Path(__file__).resolve().parents[2]
    infra = resolve_infra_refs(
        ["standard:openai"],
        anchor=repo,
        runtime_refs=["standard:runtime-default"],
    )
    assert infra.runtime_engine.get("engine_queue_depth") == 32
    assert infra.runtime_engine.get("max_auto_steps") is None


def test_runtime_engine_applies_to_kernel():
    base = KernelConfig()
    kernel, _ = parse_agent_spec(
        {},
        runtime_engine={"engine_queue_depth": 48, "max_auto_steps": 100},
    )
    assert kernel.engine_queue_depth == 48
    assert kernel.max_auto_steps == 100
    assert kernel.engine_queue_depth != base.engine_queue_depth


def test_merge_runtime_engine_layers_deep_merges_cache():
    merged = merge_runtime_engine_layers(
        {"cache": {"read": True, "write": False}},
        {"cache": {"write": True}, "stream": True},
    )
    assert merged == {
        "cache": {"read": True, "write": True},
        "stream": True,
    }


def test_workspace_runtime_refs_merge(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "runtime_refs:\n  - runtime-extra.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "runtime-extra.yaml").write_text(
        """
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: extra
spec:
  engine:
    queue_depth: 77
""".strip(),
        encoding="utf-8",
    )
    ws = WorkspaceConfig.load(tmp_path)
    infra = resolve_infra_refs(
        ["standard:openai"],
        anchor=tmp_path,
        workspace=ws,
    )
    assert infra.runtime_engine.get("engine_queue_depth") == 77


def test_runtime_refs_deep_merge_cache_in_resolve(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        """
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: a
spec:
  cache:
    read: true
    write: false
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        """
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: b
spec:
  cache:
    write: true
""".strip(),
        encoding="utf-8",
    )
    infra = resolve_infra_refs(
        ["standard:openai"],
        anchor=tmp_path,
        runtime_refs=["a.yaml", "b.yaml"],
    )
    assert infra.runtime_engine["cache"] == {"read": True, "write": True}


def _runtime_file(tmp_path: Path, engine_block: str) -> None:
    (tmp_path / "runtime.yaml").write_text(
        "apiVersion: infra/v1\nkind: RuntimeEngine\nmetadata:\n  name: r\nspec:\n  engine:\n" + engine_block,
        encoding="utf-8",
    )


def test_tool_placement_comes_from_the_runtime_engine_manifest(tmp_path: Path):
    from mas.runtime.spec.runtime_engine import apply_runtime_engine_to_kernel

    _runtime_file(tmp_path, "    tool_worker: inline\n    tool_concurrency: 6\n")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path, runtime_refs=["runtime.yaml"])
    cfg = apply_runtime_engine_to_kernel(KernelConfig(), infra.runtime_engine)
    assert (cfg.tool_worker, cfg.tool_concurrency) == ("inline", 6)
    default = KernelConfig()
    assert (default.tool_worker, default.tool_concurrency) == ("thread", None)


def test_a_bad_tool_worker_fails_when_the_manifest_loads(tmp_path: Path):
    _runtime_file(tmp_path, "    tool_worker: process\n")
    with pytest.raises(Exception, match="tool_worker"):
        resolve_infra_refs(["standard:openai"], anchor=tmp_path, runtime_refs=["runtime.yaml"])

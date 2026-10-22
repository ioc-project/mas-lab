#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shipped plugins own their params schema; unknown ids stay open."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate import validate_file


def _write(tmp_path: Path, name: str, document: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _agent(context_manager: dict) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "probe"},
        "spec": {"description": "probe agent", "context_manager": context_manager},
    }


def _middleware(middleware: str, params: dict) -> dict:
    return {
        "apiVersion": "infra/v1",
        "kind": "InfraMiddleware",
        "metadata": {"name": "probe"},
        "spec": {"middleware": middleware, "applies_to": ["LLM_CALL"], "params": params},
    }


@pytest.mark.parametrize(
    "middleware,params",
    [
        ("llm_cache", {"allow_write": True, "cache_path": "cache.json"}),
        ("llm_scheduler", {"max_in_flight": 4, "state_path": "sched.json"}),
        ("fault_inject", {"rate": 0.1, "status_codes": [503]}),
    ],
)
def test_shipped_middleware_accepts_own_params(tmp_path: Path, middleware: str, params: dict) -> None:
    pytest.importorskip("jsonschema")
    path = _write(tmp_path, "mw.yaml", _middleware(middleware, params))
    result = validate_file(path, kind="infra", strict=True, resolve_refs=True)
    assert result.ok, result.issues


@pytest.mark.parametrize(
    "middleware,params",
    [
        ("llm_cache", {"max_in_flight": 2}),
        ("llm_scheduler", {"allow_write": True}),
        ("fault_inject", {"state_path": "sched.json"}),
    ],
)
def test_shipped_middleware_rejects_sibling_plugin_params(
    tmp_path: Path, middleware: str, params: dict
) -> None:
    pytest.importorskip("jsonschema")
    path = _write(tmp_path, "mw.yaml", _middleware(middleware, params))
    result = validate_file(path, kind="infra", strict=True, resolve_refs=True)
    assert not result.ok


def test_unknown_middleware_keeps_custom_params(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write(tmp_path, "mw.yaml", _middleware("acme_pace", {"tokens_per_sec": 12}))
    result = validate_file(path, kind="infra", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_summarising_rejects_stack_param(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write(
        tmp_path,
        "agent.yaml",
        _agent({"type": "summarising", "params": {"keep_turns": 4, "max_messages": 20}}),
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok


def test_stack_rejects_summarizer_param(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write(
        tmp_path,
        "agent.yaml",
        _agent({"type": "stack", "params": {"summarizer": "drop"}}),
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok


def test_sliding_window_rejects_hysteresis(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write(
        tmp_path,
        "agent.yaml",
        _agent({"type": "sliding-window", "params": {"keep_turns": 3, "hysteresis_ratio": 0.2}}),
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok


def test_summarising_accepts_own_params(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write(
        tmp_path,
        "agent.yaml",
        _agent(
            {
                "type": "summarising",
                "params": {"keep_turns": 4, "summarizer": "drop", "working_memory_messages": 8},
            }
        ),
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_unknown_context_manager_keeps_custom_params(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write(
        tmp_path,
        "agent.yaml",
        _agent({"type": "acme_ring", "params": {"ring_size": 32, "working_memory_messages": 6}}),
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues

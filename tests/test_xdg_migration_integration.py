#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""End-to-end checks for XDG defaults: benchmark writes and user config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from ci_llm import mas_infra_refs_for_ci, require_ci_cache
from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_EXPERIMENT = REPO_ROOT / "tests/fixtures/lab-smoke/experiment.yaml"


def _pin_xdg(fake_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(fake_home / ".cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))


def _clear_path_overrides(monkeypatch) -> None:
    for name in (
        "MAS_TRACE_CACHE",
        "MAS_DATA_ROOT",
        "MAS_LAB_DATA",
        "MAS_LABS_ROOT",
        "MAS_RUNS_ROOT",
        "MAS_DATA_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.timeout(300)
def test_fresh_workspace_config_yaml_writes_under_xdg_paths(tmp_path, monkeypatch):
    """Workspace with only config.yaml; benchmark run uses XDG cache/data defaults."""
    pytest.importorskip("mas.lab.benchmark.worker")
    from mas.lab import paths as lab_paths
    from mas.lab.benchmark.worker import run_benchmark_sync

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _pin_xdg(fake_home, monkeypatch)
    _clear_path_overrides(monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_CONFIG_FILENAME).write_text(
        "# minimal workspace — defaults resolve via XDG\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MAS_MCE_OFFLINE", "1")
    monkeypatch.setenv("MAS_INFRA_REFS", mas_infra_refs_for_ci())
    require_ci_cache()

    output_dir = tmp_path / "benchmark-out"

    ok = run_benchmark_sync(
        SMOKE_EXPERIMENT,
        force=True,
        single_run=True,
        max_runs=1,
        output_dir=output_dir,
        trace_cache_dir=None,
    )
    assert ok, "benchmark run failed"

    canonical_trace = lab_paths.trace_cache().resolve()
    assert canonical_trace.is_dir(), "expected XDG trace cache directory"
    assert list(canonical_trace.iterdir()), "expected trace cache entries under XDG"
    assert canonical_trace == (fake_home / ".cache" / "mas" / "traces").resolve()


def test_user_config_at_xdg_resolves_paths(tmp_path, monkeypatch):
    """User config at $XDG_CONFIG_HOME/mas/config.yaml resolves labs_dir correctly."""
    from mas.lab import paths as lab_paths
    from mas.runtime.workspace_config import find_workspace_file

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _pin_xdg(fake_home, monkeypatch)
    _clear_path_overrides(monkeypatch)

    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)

    custom_labs = fake_home / "my-labs"
    custom_labs.mkdir()
    user_cfg = fake_home / ".config" / "mas" / "config.yaml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text(
        f"paths:\n  labs_dir: {custom_labs}\n",
        encoding="utf-8",
    )

    assert find_workspace_file(nowhere) == user_cfg.resolve()

    resolved = lab_paths.resolve_path("labs_dir")
    assert resolved.path == custom_labs.resolve()
    assert resolved.source == WORKSPACE_CONFIG_FILENAME

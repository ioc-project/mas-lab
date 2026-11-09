#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Golden-path: experiment run produces events.jsonl and post-run pipeline artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SMOKE_EXP = REPO_ROOT / "tests/fixtures/lab-smoke/experiment.yaml"
_CI_CACHE = REPO_ROOT / "tests/fixtures/llm-cache/ci.llm-cache.json"
_CI_REPLAY = REPO_ROOT / "tests/fixtures/llm-cache/ci-replay.yaml"


def _ci_cache_ready() -> bool:
    if not _CI_CACHE.is_file():
        return False
    try:
        return bool(json.loads(_CI_CACHE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return False


@pytest.fixture
def smoke_env(tmp_path, monkeypatch):
    out = tmp_path / "benchmark-out"
    trace_cache = tmp_path / "trace-cache"
    trace_cache.mkdir()
    mas_home = tmp_path / "mas-home"
    mas_home.mkdir()

    xdg_config = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_state = tmp_path / "xdg-state"
    for d in (xdg_config, xdg_data, xdg_cache, xdg_state):
        d.mkdir(parents=True)
    (xdg_config / "mas").mkdir(parents=True)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "infra_refs:\n  - standard:openai\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAS_HOME", str(mas_home))
    monkeypatch.setenv("MAS_TRACE_CACHE", str(trace_cache))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv(
        "MAS_INFRA_REFS",
        f"standard:openai,{_CI_REPLAY.resolve()}",
    )
    if not _ci_cache_ready():
        raise AssertionError(
            "tests/fixtures/llm-cache/ci.llm-cache.json is empty. "
            "Record it against a live provider: python scripts/record_ci_llm_cache.py"
        )
    return out, trace_cache


@pytest.mark.timeout(120)
def test_lab_smoke_events_and_pipeline_trace_stats(smoke_env) -> None:
    """Run lab-smoke end-to-end: benchmark → events.jsonl → extract_trace_stats CSV."""
    from mas.lab.benchmark.golden.cache_backup import find_events_in_tree
    from mas.lab.benchmark.worker import run_benchmark_sync

    output_dir, trace_cache_dir = smoke_env
    ok = run_benchmark_sync(
        SMOKE_EXP,
        force=True,
        single_run=True,
        max_runs=1,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
    )
    assert ok

    events_paths = find_events_in_tree(output_dir) or find_events_in_tree(trace_cache_dir)
    assert events_paths, "expected events.jsonl from benchmark run"
    assert events_paths[0].stat().st_size > 0

    stats_candidates = list(output_dir.rglob("trace_stats.csv"))
    assert stats_candidates, "extract_trace_stats pipeline step should write trace_stats.csv"
    assert stats_candidates[0].stat().st_size > 0

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Golden-run events.jsonl parity tests (llm_cache replay, isolated cache)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ci_llm import mas_infra_refs_for_ci, require_ci_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_EXP = REPO_ROOT / "tests/fixtures/lab-smoke/experiment.yaml"
GOLDEN_DIR = REPO_ROOT / "tests/fixtures/golden-runs/lab-smoke"
_SAMPLE_WS = REPO_ROOT / "library-samples" / "sample-workspace"


@pytest.fixture
def golden_env(tmp_path, monkeypatch):
    out = tmp_path / "benchmark-out"
    trace_cache = tmp_path / "trace-cache"
    trace_cache.mkdir()
    mas_home = tmp_path / "mas-home"
    mas_home.mkdir()
    monkeypatch.setenv("MAS_HOME", str(mas_home))
    monkeypatch.setenv("MAS_TRACE_CACHE", str(trace_cache))
    # Isolate from personal ~/.config/mas/config.yaml (e.g. team:llm-proxy)
    # so golden-run parity tests are portable across developer machines and CI.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("MAS_LLM_CACHE", str(tmp_path / "llm_cache.json"))
    monkeypatch.setenv("MAS_INFRA_REFS", mas_infra_refs_for_ci())
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(_SAMPLE_WS))
    require_ci_cache()
    return out, trace_cache


@pytest.mark.timeout(120)
def test_golden_events_match_committed_snapshot(golden_env) -> None:
    """Run lab-smoke and compare normalized events.jsonl to golden backup."""
    from mas.lab.benchmark.golden.cache_backup import find_events_in_tree
    from mas.lab.benchmark.golden.events import (
        compare_events_files,
        events_fingerprint,
        normalize_events_file,
    )
    from mas.lab.benchmark.worker import run_benchmark_sync

    if not (GOLDEN_DIR / "events.normalized.jsonl").is_file():
        raise AssertionError("golden snapshot missing — run: python scripts/capture_golden_run.py --labs lab-smoke")

    output_dir, trace_cache_dir = golden_env
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
    assert events_paths, "expected events.jsonl from run"
    actual = events_paths[0]

    match, diff = compare_events_files(actual, GOLDEN_DIR / "events.jsonl")
    assert match, diff or "events.jsonl differs from golden"

    expected_fp = (GOLDEN_DIR / "events.sha256").read_text(encoding="utf-8").strip()
    actual_fp = events_fingerprint(normalize_events_file(actual))
    assert actual_fp == expected_fp, (
        f"events fingerprint changed (regenerate: python scripts/capture_golden_run.py --labs lab-smoke)\n"
        f"  expected: {expected_fp}\n"
        f"  actual:   {actual_fp}"
    )

    # Committed cache backup must exist for offline comparison
    cache_backup = GOLDEN_DIR / "cache-backup" / "lab-smoke"
    assert cache_backup.is_dir(), "missing cache-backup/lab-smoke — recapture golden run"
    assert (cache_backup / "manifest.json").is_file()

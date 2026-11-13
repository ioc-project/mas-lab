#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Golden-run parity for every lab in tests/fixtures/golden-runs/labs.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ci_llm import mas_infra_refs_for_ci, require_ci_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "tests/fixtures/golden-runs/labs.yaml"
GOLDEN_ROOT = REPO_ROOT / "tests/fixtures/golden-runs"
_SAMPLE_WS = REPO_ROOT / "library-samples" / "sample-workspace"


def _labs_from_manifest() -> list[tuple[str, Path]]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, Path]] = []
    for entry in data.get("labs") or data.get("paper_labs") or []:
        label = str(entry["label"])
        exp = (REPO_ROOT / entry["experiment"]).resolve()
        out.append((label, exp))
    return out


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
    # Isolate the llm_cache file from the developer's XDG cache.
    monkeypatch.setenv("MAS_LLM_CACHE", str(tmp_path / "llm_cache.json"))
    monkeypatch.setenv("MAS_INFRA_REFS", mas_infra_refs_for_ci())
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(_SAMPLE_WS))
    require_ci_cache()
    return out, trace_cache


@pytest.mark.parametrize(
    "label,experiment",
    _labs_from_manifest(),
    ids=[label for label, _ in _labs_from_manifest()],
)
@pytest.mark.timeout(180)
def test_golden_lab_events_parity(label: str, experiment: Path, golden_env) -> None:
    """Run capture experiment and compare events.jsonl to committed golden snapshot."""
    from mas.lab.benchmark.golden.cache_backup import find_events_in_tree
    from mas.lab.benchmark.golden.events import (
        compare_events_files,
        events_fingerprint,
        normalize_events_file,
    )
    from mas.lab.benchmark.worker import run_benchmark_sync

    golden_dir = GOLDEN_ROOT / label
    golden_events = golden_dir / "events.jsonl"
    golden_fp_path = golden_dir / "events.sha256"

    if not golden_fp_path.is_file() or not golden_events.is_file():
        pytest.fail(f"golden fixture missing for {label} — run:\n  python scripts/capture_golden_run.py --labs {label}")

    assert experiment.is_file(), f"capture experiment missing: {experiment}"

    output_dir, trace_cache_dir = golden_env
    ok = run_benchmark_sync(
        experiment,
        force=True,
        single_run=True,
        max_runs=1,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
    )
    assert ok, f"benchmark failed for golden lab {label}"

    events_paths = find_events_in_tree(output_dir) or find_events_in_tree(trace_cache_dir)
    assert events_paths, f"expected events.jsonl from run for {label}"
    actual = events_paths[0]

    match, diff = compare_events_files(actual, golden_events)
    assert match, diff or f"events.jsonl differs from golden for {label}"

    expected_fp = golden_fp_path.read_text(encoding="utf-8").strip()
    actual_fp = events_fingerprint(normalize_events_file(actual))
    assert actual_fp == expected_fp, (
        f"events fingerprint changed for {label} "
        f"(regenerate: python scripts/capture_golden_run.py --labs {label})\n"
        f"  expected: {expected_fp}\n"
        f"  actual:   {actual_fp}"
    )

    cache_backup = golden_dir / "cache-backup" / label
    assert cache_backup.is_dir(), f"missing cache-backup/{label} for {label}"
    assert (cache_backup / "manifest.json").is_file()

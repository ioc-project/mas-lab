#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Per-lab trace-cache invalidation: delete one cache entry, re-run, regenerate artefacts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ci_llm import mas_infra_refs_for_ci, require_ci_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "tests/fixtures/cache-rerun/labs.yaml"
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
def isolated_env(tmp_path, monkeypatch):
    out = tmp_path / "benchmark-out"
    trace_cache = tmp_path / "trace-cache"
    trace_cache.mkdir()
    mas_home = tmp_path / "mas-home"
    mas_home.mkdir()
    monkeypatch.setenv("MAS_HOME", str(mas_home))
    monkeypatch.setenv("MAS_TRACE_CACHE", str(trace_cache))
    monkeypatch.setenv("MAS_INFRA_REFS", mas_infra_refs_for_ci())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("MAS_LLM_CACHE", str(tmp_path / "llm_cache.json"))
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(_SAMPLE_WS))
    require_ci_cache()
    return out, trace_cache


def _pipeline_artifacts(output_dir: Path) -> list[Path]:
    """Known post-pipeline outputs from golden capture experiments."""
    candidates = [
        output_dir / "results" / "trace_stats.csv",
        output_dir / "results" / "ci_summary.csv",
        output_dir / "results" / "figure-01-overhead-quality.png",
        output_dir / "results" / "figure-02-overhead-quality.png",
    ]
    return [p for p in candidates if p.is_file()]


def _first_cache_entry(trace_cache: Path) -> Path | None:
    for child in sorted(trace_cache.iterdir()):
        if child.is_dir() and (child / "traces" / "events.jsonl").exists():
            return child
    return None


@pytest.mark.parametrize(
    "label,experiment",
    _labs_from_manifest(),
    ids=[label for label, _ in _labs_from_manifest()],
)
@pytest.mark.timeout(300)
def test_lab_rerun_after_cache_entry_removed(
    label: str,
    experiment: Path,
    isolated_env,
) -> None:
    """Delete one trace-cache entry; benchmark re-executes and post-pipeline regenerates."""
    from mas.lab.benchmark.golden.cache_backup import find_events_in_tree
    from mas.lab.benchmark.worker import run_benchmark_sync

    output_dir, trace_cache_dir = isolated_env
    assert experiment.is_file(), f"missing experiment: {experiment}"

    assert run_benchmark_sync(
        experiment,
        force=True,
        single_run=True,
        max_runs=1,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
    ), f"initial benchmark failed for {label}"

    artifacts_before = _pipeline_artifacts(output_dir)
    assert artifacts_before, f"expected pipeline artefacts for {label}: {output_dir / 'results'}"

    events_before = find_events_in_tree(output_dir) or find_events_in_tree(trace_cache_dir)
    assert events_before, f"expected events.jsonl for {label}"

    cache_entry = _first_cache_entry(trace_cache_dir)
    assert cache_entry is not None, f"no trace-cache entry under {trace_cache_dir}"

    import shutil

    shutil.rmtree(cache_entry)

    for art in artifacts_before:
        art.unlink()

    for ev in events_before:
        if ev.is_file():
            ev.unlink()

    assert run_benchmark_sync(
        experiment,
        force=False,
        single_run=True,
        max_runs=1,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
    ), f"re-run after cache delete failed for {label}"

    artifacts_after = _pipeline_artifacts(output_dir)
    assert artifacts_after, f"pipeline artefacts not regenerated for {label}"

    events_after = find_events_in_tree(output_dir) or find_events_in_tree(trace_cache_dir)
    assert events_after and any(p.stat().st_size > 0 for p in events_after), f"events.jsonl not regenerated for {label}"

    new_cache = _first_cache_entry(trace_cache_dir)
    assert new_cache is not None and new_cache.exists(), f"trace-cache entry not recreated for {label}"

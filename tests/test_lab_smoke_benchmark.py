#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Per-lab end-to-end smoke: one cached/replay run + post-pipeline, isolated trace cache."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ci_llm import mas_infra_refs_for_ci, require_ci_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
MAS_LAB = Path(sys.executable).parent / "mas-lab"
MANIFEST = REPO_ROOT / "tests/fixtures/lab-smoke/labs.yaml"
_DEFAULT_GLOBAL_CACHE = Path.home() / ".cache" / "mas" / "traces"
_SAMPLE_WS = REPO_ROOT / "library-samples" / "sample-workspace"


def _labs_from_manifest() -> list[tuple[str, Path, list[str]]]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, Path, list[str]]] = []
    for entry in data.get("labs") or []:
        label = str(entry["label"])
        exp = (REPO_ROOT / entry["experiment"]).resolve()
        artifacts = [str(a) for a in entry.get("artifacts") or []]
        out.append((label, exp, artifacts))
    return out


@pytest.fixture
def smoke_env(tmp_path, monkeypatch):
    """Isolated benchmark output + trace-cache; must not write to global trace cache."""
    out = tmp_path / "benchmark-out"
    trace_cache = tmp_path / "trace-cache"
    trace_cache.mkdir()
    mas_home = tmp_path / "mas-home"
    mas_home.mkdir()
    monkeypatch.setenv("MAS_HOME", str(mas_home))
    monkeypatch.setenv("MAS_TRACE_CACHE", str(trace_cache))
    monkeypatch.setenv("MAS_MCE_OFFLINE", "1")
    monkeypatch.setenv("MAS_INFRA_REFS", mas_infra_refs_for_ci())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("MAS_LLM_CACHE", str(tmp_path / "llm_cache.json"))
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(_SAMPLE_WS))
    require_ci_cache()
    preexisting = (
        {p.name for p in _DEFAULT_GLOBAL_CACHE.iterdir()}
        if _DEFAULT_GLOBAL_CACHE.exists()
        else set()
    )
    return out, trace_cache, preexisting


def _assert_isolated_cache(trace_cache_dir: Path, preexisting: set[str]) -> None:
    if not _DEFAULT_GLOBAL_CACHE.exists():
        return
    for entry in trace_cache_dir.iterdir():
        if entry.name in preexisting:
            continue
        assert not (_DEFAULT_GLOBAL_CACHE / entry.name).exists(), (
            "smoke run wrote to global trace-cache — set emulation.runtime.cache: "
            "disabled and MAS_TRACE_CACHE to an isolated tmp directory"
        )


@pytest.mark.parametrize(
    "label,experiment,artifacts",
    _labs_from_manifest(),
    ids=[label for label, _, _ in _labs_from_manifest()],
)
@pytest.mark.timeout(300)
def test_lab_smoke_one_run_and_pipeline(
    label: str,
    experiment: Path,
    artifacts: list[str],
    smoke_env,
) -> None:
    """Each paper lab: 1 run, post-pipeline artefacts, no global cache pollution."""
    pytest.importorskip("mas.lab.benchmark.worker")
    from mas.lab.benchmark.golden.cache_backup import find_events_in_tree
    from mas.lab.benchmark.worker import run_benchmark_sync

    output_dir, trace_cache_dir, preexisting = smoke_env
    assert experiment.is_file(), f"missing smoke experiment for {label}: {experiment}"

    ok = run_benchmark_sync(
        experiment,
        force=True,
        single_run=True,
        max_runs=1,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
    )
    assert ok, f"benchmark smoke failed for {label}"

    results_csv = output_dir / "results.csv"
    assert results_csv.is_file(), f"missing results.csv for {label}"
    rows = results_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) >= 2, f"expected result row for {label}"
    assert "ok" in rows[1].lower() or ",ok," in rows[1], rows[1]

    events = find_events_in_tree(output_dir) or find_events_in_tree(trace_cache_dir)
    assert events, f"expected events.jsonl for {label}"
    assert any(p.stat().st_size > 0 for p in events)

    for rel in artifacts:
        path = output_dir / rel
        assert path.is_file(), f"missing pipeline artefact {rel} for {label}"
        body = path.read_text(encoding="utf-8").strip()
        assert len(body.splitlines()) >= 2, (
            f"pipeline artefact {rel} for {label} should have header + data"
        )

    _assert_isolated_cache(trace_cache_dir, preexisting)


@pytest.mark.parametrize(
    "label,experiment,artifacts",
    _labs_from_manifest(),
    ids=[f"dry-run-{label}" for label, _, _ in _labs_from_manifest()],
)
def test_lab_smoke_experiment_dry_run(
    label: str,
    experiment: Path,
    artifacts: list[str],
) -> None:
    """Smoke experiment YAML must pass mas-lab benchmark run --dry-run."""
    if not MAS_LAB.is_file():
        pytest.skip("mas-lab CLI not in venv")
    assert experiment.is_file()
    result = subprocess.run(
        [str(MAS_LAB), "benchmark", "run", str(experiment), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"dry-run failed for smoke {label}:\n{output}"
    )
    assert "Configuration valid" in output, (
        f"dry-run did not validate smoke {label}:\n{output}"
    )

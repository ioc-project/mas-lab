#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.engine import BenchmarkRunOptions, _is_mas_experiment_yaml, run_benchmark


def test_benchmark_options_preserve_cli_overrides() -> None:
    options = BenchmarkRunOptions.from_spec({"overrides": ["experiment:experiment.run.n_runs=1"]})

    assert options.overrides == ["experiment:experiment.run.n_runs=1"]


@pytest.mark.asyncio
async def test_run_benchmark_missing_file_returns_false(tmp_path: Path) -> None:
    ok = await run_benchmark(tmp_path / "missing.yaml")
    assert ok is False


def test_is_mas_experiment_yaml_detects_application(tmp_path: Path) -> None:
    path = tmp_path / "exp.yaml"
    path.write_text("experiment:\n  application:\n    app: demo\n", encoding="utf-8")
    assert _is_mas_experiment_yaml(path)


def test_is_mas_experiment_yaml_detects_applications(tmp_path: Path) -> None:
    path = tmp_path / "exp.yaml"
    path.write_text("experiment:\n  applications:\n    - app: demo\n", encoding="utf-8")
    assert _is_mas_experiment_yaml(path)


def test_is_mas_experiment_yaml_applications_win_over_mas_key(tmp_path: Path) -> None:
    path = tmp_path / "exp.yaml"
    path.write_text(
        "experiment:\n  mas: leftover\n  applications:\n    - app: demo\n",
        encoding="utf-8",
    )
    assert _is_mas_experiment_yaml(path)


def test_is_mas_experiment_yaml_detects_legacy_mas_key(tmp_path: Path) -> None:
    path = tmp_path / "exp.yaml"
    path.write_text(
        "experiment:\n  mas:\n    manifest: ./mas.yaml\n    configs_dir: ./overlays\n",
        encoding="utf-8",
    )
    assert _is_mas_experiment_yaml(path)


def test_is_mas_experiment_yaml_rejects_non_mas(tmp_path: Path) -> None:
    path = tmp_path / "exp.yaml"
    path.write_text("experiment:\n  pipeline: []\n", encoding="utf-8")
    assert _is_mas_experiment_yaml(path) is False


@pytest.mark.asyncio
async def test_run_benchmark_unsupported_schema_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "exp.yaml"
    path.write_text("experiment:\n  pipeline: []\n", encoding="utf-8")
    ok = await run_benchmark(path, options=BenchmarkRunOptions())
    assert ok is False


@pytest.mark.asyncio
async def test_run_benchmark_resume_path(monkeypatch, tmp_path: Path) -> None:
    from mas.lab.benchmark import schedule as schedule_pkg

    path = tmp_path / "exp.yaml"
    path.write_text("experiment:\n  application:\n    app: demo\n", encoding="utf-8")

    async def _fake_resume(**kwargs):
        assert kwargs["benchmark_id"] == "b1"
        assert kwargs["progress"] is False
        assert kwargs["force_lock"] is True
        return True

    monkeypatch.setattr(schedule_pkg.resume, "resume_mas_benchmark", _fake_resume)
    ok = await run_benchmark(
        path,
        options=BenchmarkRunOptions(benchmark_id="b1", progress=False, force_lock=True),
    )
    assert ok is True


@pytest.mark.asyncio
async def test_run_benchmark_mas_route_uses_options(monkeypatch, tmp_path: Path) -> None:
    from mas.lab.benchmark.schedule import run_batch

    path = tmp_path / "exp.yaml"
    path.write_text("experiment:\n  application:\n    app: demo\n", encoding="utf-8")

    captured = {}

    async def _fake_run_mas_benchmark(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(run_batch, "run_mas_benchmark", _fake_run_mas_benchmark)

    options = BenchmarkRunOptions(
        progress=False,
        dry_run=True,
        max_runs=3,
        limit_scenarios=2,
        scenario_id="react",
        dataset_item="case-2",
        single_run=True,
        flavour_name="local",
        infra_name="mock",
        force=True,
        trace_cache_dir=Path("/tmp/traces"),
        data_cache_dir=Path("/tmp/data"),
        output_dir=Path("/tmp/out"),
        strategy="coverage",
        step_overrides=[{"k": 1}],
        clean_stale=True,
    )
    ok = await run_benchmark(path, options=options)
    assert ok is True
    assert captured["progress"] is False
    assert captured["dry_run"] is True
    assert captured["max_runs"] == 3
    assert captured["limit_scenarios"] == 2
    assert captured["scenario_id"] == "react"
    assert captured["dataset_item"] == "case-2"
    assert captured["single_run"] is True
    assert captured["flavour_name"] == "local"
    assert captured["infra_name"] == "mock"
    assert captured["force"] is True
    assert captured["trace_cache_dir"] == Path("/tmp/traces")
    assert captured["data_cache_dir"] == Path("/tmp/data")
    assert captured["output_dir"] == Path("/tmp/out")
    assert captured["strategy"] == "coverage"
    assert captured["step_overrides"] == [{"k": 1}]
    assert captured["clean_stale"] is True

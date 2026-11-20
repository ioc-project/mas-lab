#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A failed or interrupted batch stops its services and is marked failed; the original error survives."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.metadata import BenchmarkMetadata, BenchmarkStatus
from mas.lab.benchmark.schedule import run_batch as rb
from mas.lab.benchmark.schedule.run_batch import execute, finalize
from mas.lab.benchmark.service_manager import ServiceManager


def _meta(tmp_path: Path) -> BenchmarkMetadata:
    return BenchmarkMetadata.create_new(
        experiment_name="x",
        experiment_description="d",
        experiment_yaml_path="e.yaml",
        total_scenarios=1,
        run_dir=tmp_path,
    )


def _batch(tmp_path: Path, specs=("step",)):
    loaded = SimpleNamespace(
        exp=object(),
        experiment_yaml=tmp_path / "e.yaml",
        pipeline_specs=list(specs),
        infra_name=None,
        step_overrides_dict={},
        scenario_ids=["s"],
        n_runs=1,
    )
    prepared = SimpleNamespace(output_dir=tmp_path, loaded_ids=["s"], mas_meta=_meta(tmp_path))
    return loaded, prepared


def _process_manager(tmp_path: Path) -> ServiceManager:
    path = tmp_path / "services.yaml"
    path.write_text(
        "apiVersion: infra/v1\nkind: ServiceBundle\nmetadata: {name: demo}\nspec:\n  services:\n"
        "    sleeper:\n      backend: process\n      command: [python3, -c, 'import time; time.sleep(60)']\n",
        encoding="utf-8",
    )
    mgr = ServiceManager(path)
    mgr._auto_start_names = {"sleeper"}
    return mgr


def _reaped(pid: int, timeout: float = 5.0) -> bool:
    """True when the child exited (and is reaped here): the pid is gone."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if done:
            return True
        time.sleep(0.05)
    return False


def test_execute_batch_still_stops_services_when_close_is_interrupted(tmp_path: Path, monkeypatch):
    mgr = _process_manager(tmp_path)
    started: list[int] = []

    async def fake(loaded, prepared, exit_stack, **_kw):
        exit_stack.enter_context(mgr)
        started.append(mgr._pids["sleeper"])

        def boom() -> None:
            raise KeyboardInterrupt()

        exit_stack.callback(boom)
        raise RuntimeError("run failed")

    monkeypatch.setattr(execute, "_execute_batch", fake)
    with pytest.raises(RuntimeError, match="run failed"):
        asyncio.run(execute.execute_batch(SimpleNamespace(), SimpleNamespace()))
    assert _reaped(started[0])


@pytest.mark.parametrize("error", [RuntimeError("run failed"), KeyboardInterrupt(), asyncio.CancelledError()])
def test_execute_batch_stops_services_on_any_exit(tmp_path: Path, monkeypatch, error):
    mgr = _process_manager(tmp_path)
    started: list[int] = []

    async def fake(loaded, prepared, exit_stack, **_kw):
        exit_stack.enter_context(mgr)
        started.append(mgr._pids["sleeper"])
        raise error

    monkeypatch.setattr(execute, "_execute_batch", fake)
    with pytest.raises(type(error)):
        asyncio.run(execute.execute_batch(SimpleNamespace(), SimpleNamespace()))
    assert _reaped(started[0])


def test_abort_batch_marks_failed_and_does_not_run_post_steps(tmp_path: Path, monkeypatch):
    """Post steps analyse a complete batch; over partial output they would cost model calls for nothing."""
    _loaded, prepared = _batch(tmp_path)
    phases: list[str] = []

    async def fake_phase(**kw):
        phases.append(kw["phase"])

    monkeypatch.setattr("mas.lab.benchmark.schedule.pipeline.run_pipeline_phase", fake_phase)
    assert finalize.abort_batch(prepared, RuntimeError("boom")) == []
    assert phases == []
    assert prepared.mas_meta.status is BenchmarkStatus.FAILED
    assert "boom" in prepared.mas_meta.metadata["error"]


def test_abort_batch_reports_a_metadata_failure_without_raising(tmp_path: Path, monkeypatch):
    _loaded, prepared = _batch(tmp_path)

    def broken(_path):
        raise OSError("disk full")

    monkeypatch.setattr(prepared.mas_meta, "to_yaml", broken)
    errors = finalize.abort_batch(prepared, KeyboardInterrupt())
    assert errors == ["metadata: disk full"]


def test_finalize_batch_stops_services_when_writing_results_fails(tmp_path: Path, monkeypatch, capsys):
    loaded, prepared = _batch(tmp_path)
    loaded.exp = SimpleNamespace(name="x")
    prepared.csv_path = tmp_path / "results.csv"
    prepared.dataset_items = [{"id": "a"}]
    closed: list[bool] = []
    execution = SimpleNamespace(
        results_rows=[{"status": "ok"}],
        total_ok=1,
        total_fail=0,
        exit_stack=SimpleNamespace(close=lambda: closed.append(True)),
        run_keys=[],
        pipeline_drained=True,
    )

    def boom(*_a, **_k):
        raise OSError("disk")

    monkeypatch.setattr(finalize, "write_results_csv", boom)
    ok = asyncio.run(finalize.finalize_batch(loaded, prepared, execution))
    assert ok is False and closed
    assert prepared.mas_meta.status is BenchmarkStatus.FAILED
    out = capsys.readouterr().out
    assert "writing results failed" in out and "post-pipeline failed" not in out


def test_finalize_batch_marks_failed_when_stopping_services_is_interrupted(tmp_path: Path, monkeypatch):
    loaded, prepared = _batch(tmp_path)
    loaded.exp = SimpleNamespace(name="x")
    prepared.csv_path = tmp_path / "results.csv"
    prepared.dataset_items = [{"id": "a"}]
    closed: list[bool] = []

    def boom() -> None:
        closed.append(True)
        raise KeyboardInterrupt()

    execution = SimpleNamespace(
        results_rows=[{"status": "ok"}],
        total_ok=1,
        total_fail=0,
        exit_stack=SimpleNamespace(close=boom),
        run_keys=[],
        pipeline_drained=True,
    )
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(finalize.finalize_batch(loaded, prepared, execution))
    assert closed and prepared.mas_meta.status is BenchmarkStatus.FAILED


def test_finalize_batch_marks_failed_when_post_is_interrupted(tmp_path: Path, monkeypatch):
    loaded, prepared = _batch(tmp_path)
    loaded.exp = SimpleNamespace(name="x")
    prepared.csv_path = tmp_path / "results.csv"
    prepared.dataset_items = [{"id": "a"}]
    execution = SimpleNamespace(
        results_rows=[{"status": "ok"}],
        total_ok=1,
        total_fail=0,
        exit_stack=SimpleNamespace(close=lambda: None),
        run_keys=[],
        pipeline_drained=False,
    )

    async def interrupting_phase(**_kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr("mas.lab.benchmark.schedule.pipeline.run_pipeline_phase", interrupting_phase)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(finalize.finalize_batch(loaded, prepared, execution))
    assert prepared.mas_meta.status is BenchmarkStatus.FAILED


def test_finalize_batch_names_a_failing_post_pipeline(tmp_path: Path, monkeypatch, capsys):
    loaded, prepared = _batch(tmp_path)
    loaded.exp = SimpleNamespace(name="x")
    prepared.csv_path = tmp_path / "results.csv"
    prepared.dataset_items = [{"id": "a"}]
    execution = SimpleNamespace(
        results_rows=[{"status": "ok"}],
        total_ok=1,
        total_fail=0,
        exit_stack=SimpleNamespace(close=lambda: None),
        run_keys=[],
        pipeline_drained=False,
    )

    async def failing_phase(**_kw):
        raise ValueError("post broke")

    monkeypatch.setattr("mas.lab.benchmark.schedule.pipeline.run_pipeline_phase", failing_phase)
    assert asyncio.run(finalize.finalize_batch(loaded, prepared, execution)) is False
    assert "post-pipeline failed" in capsys.readouterr().out
    assert "post broke" in prepared.mas_meta.metadata["pipeline_error"]


@pytest.mark.parametrize("error", [RuntimeError("run failed"), KeyboardInterrupt()])
def test_run_mas_benchmark_cleans_up_then_reraises_the_original(tmp_path: Path, monkeypatch, error):
    loaded, prepared = _batch(tmp_path)
    aborted: list[BaseException] = []

    async def prepare(*_a, **_kw):
        return prepared

    async def failing_execute(*_a, **_kw):
        raise error

    def abort(_prepared, cause):
        aborted.append(cause)
        return ["metadata: also broken"]

    monkeypatch.setattr(rb, "load_experiment", lambda *_a, **_kw: loaded)
    monkeypatch.setattr(rb, "prepare_batch", prepare)
    monkeypatch.setattr(rb, "execute_batch", failing_execute)
    monkeypatch.setattr(rb, "abort_batch", abort)
    with pytest.raises(type(error)) as caught:
        asyncio.run(rb.run_mas_benchmark(tmp_path / "e.yaml"))
    assert caught.value is error and aborted == [error]


def test_failing_prepare_does_not_run_post(tmp_path: Path, monkeypatch):
    loaded, _prepared = _batch(tmp_path)
    aborted: list[BaseException] = []

    async def failing_prepare(*_a, **_kw):
        raise RuntimeError("pre failed")

    def abort(*_a, **_kw):
        aborted.append(RuntimeError("should not run"))
        return []

    monkeypatch.setattr(rb, "load_experiment", lambda *_a, **_kw: loaded)
    monkeypatch.setattr(rb, "prepare_batch", failing_prepare)
    monkeypatch.setattr(rb, "abort_batch", abort)
    with pytest.raises(RuntimeError, match="pre failed"):
        asyncio.run(rb.run_mas_benchmark(tmp_path / "e.yaml"))
    assert aborted == []


def test_finalize_batch_skips_post_when_writing_results_is_interrupted(tmp_path: Path, monkeypatch):
    loaded, prepared = _batch(tmp_path)
    loaded.exp = SimpleNamespace(name="x")
    prepared.csv_path = tmp_path / "results.csv"
    prepared.dataset_items = [{"id": "a"}]
    phases: list[str] = []

    async def fake_phase(**kw):
        phases.append(kw["phase"])

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt()

    execution = SimpleNamespace(
        results_rows=[{"status": "ok"}],
        total_ok=1,
        total_fail=0,
        exit_stack=SimpleNamespace(close=lambda: None),
        run_keys=[],
        pipeline_drained=False,
    )
    monkeypatch.setattr(finalize, "write_results_csv", interrupted)
    monkeypatch.setattr("mas.lab.benchmark.schedule.pipeline.run_pipeline_phase", fake_phase)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(finalize.finalize_batch(loaded, prepared, execution))
    assert phases == []
    assert prepared.mas_meta.status is BenchmarkStatus.FAILED

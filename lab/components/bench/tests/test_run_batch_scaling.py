#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Open counts and result-row equality for executor.worker_processes."""

from __future__ import annotations

import resource
import threading
import time
from pathlib import Path

import pytest
from scaling_support import (
    WALL_CLOCK_GROUP,
    WriteOpenCounter,
    event_profile_table,
    isolate_lab_env,
    lab_ports,
    outcome_table,
    prepare_lab,
    results_rows,
    run_scaling_lab,
    stop_spawned_listeners,
    stream_profile,
    write_cpu_bound_dataset,
    write_opens_per_run,
)

# Always-on tests use a few deterministic items. The fixture still has 24 items.
_FAST_LIMIT = 2
_DETERMINISM_LIMIT = 4


@pytest.fixture
def scaling_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    isolate_lab_env(monkeypatch, tmp_path)
    lab = prepare_lab(tmp_path)
    yield lab
    stop_spawned_listeners(*lab_ports(lab))


def test_write_open_counter_counts_jsonl_emitter_os_open(tmp_path: Path) -> None:
    from mas.library.standard.lib.observability.emit import JsonlFileEmitter

    dest = tmp_path / "events.jsonl"
    counter = WriteOpenCounter()
    with counter.patch():
        emitter = JsonlFileEmitter(dest)
        emitter.emit({"kind": "llm_call_end", "n": 1})
        emitter.close()
    assert any(Path(path).name == "events.jsonl" for path, _mode in counter.calls)


@pytest.mark.asyncio
async def test_open_for_write_calls_per_run(scaling_lab: Path, tmp_path: Path) -> None:
    """Count open-for-write calls on one run."""
    counter = WriteOpenCounter()
    with counter.patch():
        output_dir = await run_scaling_lab(scaling_lab, tmp_path, parallel=1, limit=_FAST_LIMIT)
    rows = results_rows(output_dir)
    assert rows, "expected result rows"
    assert all(row["status"] == "ok" for row in rows)
    assert any(Path(path).name == "events.jsonl" for path, _mode in counter.calls), (
        "JsonlFileEmitter os.open must be counted"
    )
    opens = write_opens_per_run(counter, output_dir)
    # Persistent handles: events.jsonl and the stream log open once per run.
    assert opens <= 4, f"expected at most 4 open-for-write calls per run, got {opens}"


@pytest.mark.asyncio
async def test_outcome_table_matches_at_parallel_1_and_4(scaling_lab: Path, tmp_path: Path) -> None:
    """Same status, output and per-call codes at parallel_scenarios 1 and 4."""
    out_p1 = await run_scaling_lab(scaling_lab, tmp_path, parallel=1, limit=_DETERMINISM_LIMIT)
    out_p4 = await run_scaling_lab(scaling_lab, tmp_path, parallel=4, limit=_DETERMINISM_LIMIT)
    table_p1 = outcome_table(out_p1)
    table_p4 = outcome_table(out_p4)
    assert table_p1
    assert table_p1 == table_p4
    assert all(row["status"] == "ok" for row in results_rows(out_p1))
    assert all(row["status"] == "ok" for row in results_rows(out_p4))


@pytest.mark.asyncio
async def test_outcome_table_matches_at_worker_1_and_n(scaling_lab: Path, tmp_path: Path) -> None:
    """Same status, output and per-call codes at worker_processes 1 and N, parallel_scenarios 1 and 4."""
    from mas.lab.benchmark.schedule.run_batch.workers import cpu_count

    workers = min(2, cpu_count())  # two prove the worker path; each extra spawn re-imports the stack
    if workers < 2:
        pytest.skip("need at least 2 CPUs for process workers")
    out_w1_p4 = await run_scaling_lab(
        scaling_lab, tmp_path, parallel=4, limit=_DETERMINISM_LIMIT, worker_processes=1, cache_name="cache-w1"
    )
    out_wn_p4 = await run_scaling_lab(
        scaling_lab, tmp_path, parallel=4, limit=_DETERMINISM_LIMIT, worker_processes=workers, cache_name="cache-wn4"
    )
    out_wn_p1 = await run_scaling_lab(
        scaling_lab, tmp_path, parallel=1, limit=_DETERMINISM_LIMIT, worker_processes=workers, cache_name="cache-wn1"
    )
    table = outcome_table(out_w1_p4)
    assert table
    assert table == outcome_table(out_wn_p4)
    assert table == outcome_table(out_wn_p1)
    profile = event_profile_table(out_w1_p4)
    assert profile and all(count > 0 for *_head, count, _kinds in profile)
    assert profile == event_profile_table(out_wn_p4)
    assert profile == event_profile_table(out_wn_p1)
    streams = stream_profile(tmp_path / "cache-w1")
    assert len(streams) == _DETERMINISM_LIMIT
    assert streams == stream_profile(tmp_path / "cache-wn4")
    assert streams == stream_profile(tmp_path / "cache-wn1")
    assert [row["status"] for row in results_rows(out_wn_p4)] == ["ok"] * len(results_rows(out_wn_p4))
    rows = results_rows(out_wn_p4)
    plan = [(row["scenario"], row["item_id"], row["run"]) for row in rows]
    assert plan == sorted(plan, key=lambda key: (str(key[0]), str(key[1]), int(key[2])))


@pytest.mark.asyncio
async def test_worker_path_publishes_run_started(scaling_lab: Path, tmp_path: Path) -> None:
    from mas.lab.benchmark.schedule.run_batch.workers import cpu_count

    workers = min(2, cpu_count())
    if workers < 2:
        pytest.skip("need at least 2 CPUs for process workers")
    output_dir = await run_scaling_lab(scaling_lab, tmp_path, parallel=1, limit=_FAST_LIMIT, worker_processes=workers)
    rows = results_rows(output_dir)
    assert rows and all(row["status"] == "ok" for row in rows)
    started = 0
    streams = tmp_path / "trace-cache" / "streams"
    if streams.is_dir():
        for path in streams.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if '"kind": "run.started"' in line or '"kind":"run.started"' in line:
                    started += 1
    assert started >= len(rows), f"worker path must publish run.started per run, got {started}"


def _child_pids() -> set[int]:
    import multiprocessing

    return {int(child.pid) for child in multiprocessing.active_children() if child.pid}


@pytest.mark.asyncio
async def test_a_failure_before_dispatch_leaves_no_worker_process(
    scaling_lab: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mas.lab.benchmark.schedule.run_batch.workers import cpu_count

    if cpu_count() < 2:
        pytest.skip("need at least 2 CPUs for process workers")

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("infra refused")

    monkeypatch.setattr("mas.ctl.compose.runner.resolve_workspace_infra", refuse)
    before = _child_pids()
    with pytest.raises(Exception, match="infra refused"):
        await run_scaling_lab(scaling_lab, tmp_path, parallel=2, limit=2, worker_processes=2)
    assert _child_pids() - before == set()


@pytest.mark.asyncio
async def test_a_failure_after_the_pool_started_closes_it(
    scaling_lab: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mas.lab.benchmark.schedule.run_batch import execute, workers

    if workers.cpu_count() < 2:
        pytest.skip("need at least 2 CPUs for process workers")
    made: list[workers.RunWorkerPool] = []

    class Spy(workers.RunWorkerPool):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("result assembly failed")

    monkeypatch.setattr(workers, "RunWorkerPool", Spy)
    monkeypatch.setattr(execute, "ExecutionResult", explode)
    before = _child_pids()
    with pytest.raises(Exception, match="result assembly failed"):
        await run_scaling_lab(scaling_lab, tmp_path, parallel=2, limit=2, worker_processes=2)
    assert len(made) == 1 and made[0]._closed
    assert _child_pids() - before == set()


@pytest.mark.asyncio
async def test_scaling_lab_starts_no_mcp_loop_thread(scaling_lab: Path, tmp_path: Path) -> None:
    """The loopback fixture uses a native work tool; mcp-loop is not on the hot path."""
    seen: list[int] = []
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            seen.append(sum(1 for thread in threading.enumerate() if thread.name == "mcp-loop"))
            time.sleep(0.01)

    sampler = threading.Thread(target=sample, name="sample-mcp-loop", daemon=True)
    sampler.start()
    try:
        output_dir = await run_scaling_lab(scaling_lab, tmp_path, parallel=4, limit=_FAST_LIMIT)
    finally:
        stop.set()
        sampler.join(timeout=2)
    rows = results_rows(output_dir)
    assert rows and all(row["status"] == "ok" for row in rows)
    assert seen, "sampler must run while the lab is in flight"
    assert max(seen) == 0


def test_wall_clock_items_are_marked(scaling_lab: Path) -> None:
    import yaml

    data = yaml.safe_load((scaling_lab / "dataset.yaml").read_text(encoding="utf-8"))
    items = data["spec"]["items"]
    marked = [item for item in items if item.get("group") == WALL_CLOCK_GROUP]
    assert marked, "fixture must mark wall-clock items so equality checks can skip them"
    assert {item["id"] for item in marked} == {23, 24}


def test_fake_model_parses_work_rounds_and_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    path = Path(__file__).resolve().parent / "fixtures" / "scaling_lab" / "servers" / "fake_model.py"
    spec = importlib.util.spec_from_file_location("scaling_fake_model", path)
    assert spec is not None and spec.loader is not None
    fake_model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake_model)
    sleeps: list[float] = []
    # fake_model.time is the shared time module: restore time.sleep when the test ends, or every thread that
    # sleeps afterwards (a library reporter thread, for one) spins instead.
    monkeypatch.setattr(fake_model.time, "sleep", sleeps.append)
    waited = fake_model.script({"messages": [{"role": "user", "content": "wait"}]})
    assert waited["content"] == "waited"
    assert sleeps == [0.05]
    assert fake_model._wait_s("wait") == 0.05
    assert fake_model._wait_s("work 1200000 wait 0.2") == 0.2
    assert fake_model._work_rounds("work 1200000 wait 0.2") == 1_200_000
    work = fake_model.script({"messages": [{"role": "user", "content": "work 7 wait 0"}]})
    assert work["tool_calls"][0]["function"]["arguments"] == '{"n": 7}'


@pytest.mark.perf
@pytest.mark.asyncio
async def test_worker_processes_beat_threads_at_parallel_8(scaling_lab: Path, tmp_path: Path) -> None:
    """worker_processes=4 at parallel_scenarios=8 is at most 0.67x threads and at least 2.5x vs parallel_scenarios=1."""
    from mas.lab.benchmark.schedule.run_batch.workers import cpu_count

    workers = 4
    if cpu_count() < 6:
        pytest.skip("need at least 6 CPUs for the process-worker wall bound")
    write_cpu_bound_dataset(scaling_lab, n_items=72, rounds=1_200_000, sleep_s=0.2)

    async def _wall(*, parallel: int, worker_processes: int) -> float:
        start = time.perf_counter()
        output_dir = await run_scaling_lab(
            scaling_lab,
            tmp_path,
            parallel=parallel,
            worker_processes=worker_processes,
            timeout=300,
        )
        wall = time.perf_counter() - start
        rows = results_rows(output_dir)
        assert len(rows) >= 72
        assert sum(1 for row in rows if row["status"] == "ok") >= 72
        return wall

    wall_p1 = await _wall(parallel=1, worker_processes=1)
    wall_threads = await _wall(parallel=8, worker_processes=1)
    wall_workers = await _wall(parallel=8, worker_processes=workers)
    print()
    print(f"parallel_scenarios=1 threads {wall_p1:.3f}s")
    print(f"parallel_scenarios=8 threads {wall_threads:.3f}s")
    print(f"parallel_scenarios=8 workers={workers} {wall_workers:.3f}s")
    assert wall_workers <= 0.67 * wall_threads, (
        f"worker_processes={workers} at parallel_scenarios=8 was {wall_workers:.3f}s, "
        f"need at most 0.67x of threads {wall_threads:.3f}s"
    )
    assert wall_p1 / wall_workers >= 2.5, (
        f"worker_processes={workers} at parallel_scenarios=8 was {wall_workers:.3f}s, "
        f"need at least 2.5x vs parallel_scenarios=1 {wall_p1:.3f}s"
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_scaling_wall_and_cpu_table(scaling_lab: Path, tmp_path: Path) -> None:
    """Print wall time, process CPU and cores for parallel_scenarios 1, 2, 4, 8."""
    print()
    print(f"{'par':>4} {'wall_s':>10} {'cpu_s':>10} {'cores':>8} {'speedup':>8}")
    baseline = None
    for parallel in (1, 2, 4, 8):
        usage0 = resource.getrusage(resource.RUSAGE_SELF)
        wall0 = time.perf_counter()
        output_dir = await run_scaling_lab(scaling_lab, tmp_path, parallel=parallel)
        wall = time.perf_counter() - wall0
        usage1 = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (usage1.ru_utime + usage1.ru_stime) - (usage0.ru_utime + usage0.ru_stime)
        cores = cpu / wall if wall else 0.0
        if baseline is None:
            baseline = wall
        speedup = (baseline / wall) if wall else 0.0
        print(f"{parallel:>4} {wall:10.3f} {cpu:10.3f} {cores:8.2f} {speedup:8.2f}x")
        rows = results_rows(output_dir)
        assert len(rows) >= 22
        assert sum(1 for row in rows if row["status"] == "ok") >= 22

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Process workers for lab runs: spawn, plain jobs, worker safety, retirement, death, orphans."""

from __future__ import annotations

import ast
import os
import queue
import signal
import subprocess
import sys
import textwrap
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mas.lab.benchmark.pipeline.core import PipelineStep
from mas.lab.benchmark.schedule.run_batch.workers import (
    RunWorkerPool,
    cpu_count,
    plain,
    require_cores,
    require_worker_safe,
    run_input_payload,
    validate_worker_max_runs,
    validate_worker_processes,
)
from mas.lab.inputs import RunInput
from mas.lab.lab.config import ExperimentScheduleSpec, MASExperimentConfig

# A job whose run fails inside the worker with a runner error: the worker answered, it did not die.
_DEATH_ERRORS = {"worker died", "worker timed out", "worker pool closed"}


def _job(job_id: str, **extra: object) -> dict:
    return {"job_id": job_id, "spec_path": "/tmp/no.yaml", "output_dir": "/tmp", "run_input": {"user": []}, **extra}


def _kill_in_flight(pool: RunWorkerPool, *, timeout_s: float = 5.0) -> int:
    """Kill the worker that has already taken a job. Return that process id."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with pool._lock:
            victims = [worker for worker in pool._workers if worker.runs >= 1 and worker.alive and worker.proc.pid]
        if victims:
            pid = int(victims[0].proc.pid)
            victims[0].proc.kill()
            return pid
        time.sleep(0.02)
    raise AssertionError("no in-flight worker to kill")


class _Undeclared(PipelineStep):
    async def execute(self, ctx):  # noqa: ANN001
        return None


class _Safe(PipelineStep):
    worker_safe = True

    async def execute(self, ctx):  # noqa: ANN001
        return None


class _SafeChild(_Safe):
    pass


class _Unsafe(PipelineStep):
    worker_safe = False

    async def execute(self, ctx):  # noqa: ANN001
        return None


def test_schedule_defaults_to_one_worker_process() -> None:
    spec = ExperimentScheduleSpec.from_dict({})
    assert spec.worker_processes == 1
    assert spec.worker_max_runs is None


def test_schedule_parses_worker_fields() -> None:
    spec = ExperimentScheduleSpec.from_dict({"worker_processes": 1, "worker_max_runs": 7})
    assert spec.worker_processes == 1
    assert spec.worker_max_runs == 7


def test_a_worker_count_above_this_machines_cores_still_parses() -> None:
    """An experiment that loads on a big machine must load on a small one; the run decides."""
    spec = ExperimentScheduleSpec.from_dict({"worker_processes": cpu_count() + 100})
    assert spec.worker_processes == cpu_count() + 100


def test_the_run_refuses_a_worker_count_above_the_cores_and_names_them() -> None:
    too_many = cpu_count() + 1
    with pytest.raises(ValueError, match=rf"{too_many}.*{cpu_count()} CPU cores"):
        require_cores(too_many)
    with pytest.raises(ValueError, match="CPU cores"):
        RunWorkerPool(too_many)
    assert require_cores(cpu_count()) == cpu_count()


@pytest.mark.parametrize("bad", [0, -1, 1.5, "2", True, None])
def test_the_schedule_parse_checks_worker_processes_structure(bad: object) -> None:
    with pytest.raises(ValueError, match="worker_processes"):
        ExperimentScheduleSpec.from_dict({"worker_processes": bad})
    with pytest.raises(ValueError, match="worker_processes"):
        validate_worker_processes(bad)


@pytest.mark.parametrize("bad", [0, -3, 2.5, "x", False])
def test_the_schedule_parse_checks_worker_max_runs_structure(bad: object) -> None:
    with pytest.raises(ValueError, match="worker_max_runs"):
        ExperimentScheduleSpec.from_dict({"worker_max_runs": bad})
    assert validate_worker_max_runs(None) is None


def test_workers_module_reads_no_environment_variables() -> None:
    text = Path(__file__).resolve().parents[1] / "src/mas/lab/benchmark/schedule/run_batch/workers.py"
    source = text.read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "environ[" not in source


def test_run_input_payload_carries_every_run_input_field() -> None:
    run_input = RunInput(
        user=[{"role": "user", "content": "hi"}],
        hitl=[{"role": "hitl", "content": "ok"}],
        memory_seeds=[{"source": "a", "content": "b"}],
        tool_fixtures={"by_tool": {"*": {"x": 1}}},
        checkpoint_load="ckpt",
        checkpoint_save=True,
        session_id="s1",
        expectations={"k": "v"},
    )
    payload = run_input_payload(run_input)
    assert RunInput(**payload) == run_input


def test_job_payload_is_plain_data_that_rebuilds_the_run_input() -> None:
    payload = run_input_payload(RunInput(user=[{"role": "user", "content": "hi"}], checkpoint_load=Path("/tmp/c.json")))
    assert payload["checkpoint_load"] == "/tmp/c.json"
    assert payload["turns"] == [{"role": "user", "content": "hi"}]
    assert type(payload["user"][0]) is dict


def test_pool_uses_spawn() -> None:
    pool = RunWorkerPool(1, timeout_s=5)
    try:
        assert pool.start_method == "spawn"
        assert pool._ctx.get_start_method() == "spawn"
    finally:
        pool.close()


def test_killed_worker_returns_one_error_and_pool_continues() -> None:
    pool = RunWorkerPool(1, timeout_s=8)
    first: list[dict] = []
    try:

        def _hold() -> None:
            first.append(pool.run_job(_job("die", pause_s=8.0)))

        holder = threading.Thread(target=_hold)
        holder.start()
        pid = _kill_in_flight(pool)
        holder.join(timeout=10)
        assert first
        assert first[0]["status"] == "error"
        assert first[0]["error"] == "worker died"
        assert first[0]["content"] == "worker died"
        second = pool.run_job(_job("after"))
        assert second["status"] == "error"
        assert second["error"] not in _DEATH_ERRORS
        assert pool.pids != [pid]
    finally:
        pool.close()


def test_pool_close_stops_a_replacement_worker() -> None:
    pool = RunWorkerPool(1, timeout_s=8)
    done: list[dict] = []
    try:

        def _hold() -> None:
            done.append(pool.run_job(_job("die", pause_s=8.0)))

        holder = threading.Thread(target=_hold)
        holder.start()
        pid = _kill_in_flight(pool)
        holder.join(timeout=10)
        replacement = pool._free.get()
        pool._free.put(replacement)
        assert id(replacement) in {id(worker) for worker in pool._workers}
        assert replacement.alive
        assert replacement.proc.pid != pid
    finally:
        pool.close()
    assert not replacement.alive


def test_timed_out_worker_error_has_content() -> None:
    pool = RunWorkerPool(1, timeout_s=0.2)
    try:
        result = pool.run_job(_job("slow", pause_s=5.0))
        assert result["status"] == "error"
        assert result["error"] == "worker timed out"
        assert result["content"] == "worker timed out"
    finally:
        pool.close()


def test_waiting_for_a_free_worker_does_not_count_against_the_run_timeout() -> None:
    """One worker, three jobs of 0.7s at once, 1s timeout each: each runs alone, none times out."""
    pool = RunWorkerPool(1, timeout_s=3.0)
    try:
        assert pool.run_job(_job("warm"))["error"] not in _DEATH_ERRORS
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda n: pool.run_job(_job(f"q{n}", pause_s=0.7), timeout_s=1.0), range(3)))
    finally:
        pool.close()
    assert [r["error"] for r in results if r["error"] in _DEATH_ERRORS] == []
    assert len(results) == 3


@pytest.mark.parametrize(("max_runs", "jobs"), [(1, 30), (3, 12)])
def test_a_worker_at_its_run_limit_is_retired_before_the_next_job_reaches_it(max_runs: int, jobs: int) -> None:
    pool = RunWorkerPool(1, max_runs=max_runs, timeout_s=20)
    try:
        first = pool.pids
        results = [pool.run_job(_job(f"seq{n}")) for n in range(jobs)]
        last = pool.pids
    finally:
        pool.close()
    assert [r["error"] for r in results if r["error"] in _DEATH_ERRORS] == []
    assert first != last, "the worker was never recycled"


def test_a_result_is_returned_only_after_the_jobs_events_were_delivered() -> None:
    delivered: list[float] = []

    class SlowEvents(RunWorkerPool):
        def _drain_events(self) -> None:  # a sink that is slow to see the end of a job's events
            while True:
                item = self._events.get()
                if item is None:
                    return
                kind, payload = item
                if kind == "done":
                    time.sleep(0.6)
                    with self._lock:
                        marker = self._done.get(str(payload))
                    if marker is not None:
                        delivered.append(time.monotonic())
                        marker.set()

    pool = SlowEvents(1, timeout_s=20)
    try:
        pool.run_job(_job("warm"))
        delivered.clear()
        result = pool.run_job(_job("late"))
        returned = time.monotonic()
    finally:
        pool.close()
    assert result["error"] not in _DEATH_ERRORS
    assert len(delivered) == 1 and delivered[0] <= returned


def test_a_job_without_job_id_is_an_error() -> None:
    pool = RunWorkerPool(1, timeout_s=20)
    try:
        result = pool.run_job({"spec_path": "/tmp/no.yaml", "output_dir": "/tmp", "run_input": {"user": []}})
    finally:
        pool.close()
    assert result["error"] == "job has no job_id"
    assert result["content"] == "job has no job_id"


def test_a_duplicate_in_flight_job_id_is_one_error() -> None:
    pool = RunWorkerPool(1, timeout_s=20)
    first: list[dict] = []
    try:

        def _hold() -> None:
            first.append(pool.run_job(_job("same", pause_s=1.5)))

        holder = threading.Thread(target=_hold)
        holder.start()
        deadline = time.monotonic() + 2.0
        while "same" not in pool._done and time.monotonic() < deadline:
            time.sleep(0.02)
        second = pool.run_job(_job("same"))
        holder.join()
    finally:
        pool.close()
    assert second["error"] == "duplicate job_id"
    assert second["content"] == "duplicate job_id"
    assert first and first[0]["error"] not in _DEATH_ERRORS | {"duplicate job_id"}


def test_undelivered_worker_events_are_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.benchmark.schedule.run_batch import workers as workers_mod

    monkeypatch.setattr(workers_mod, "_EVENT_BARRIER_S", 0.05)

    class DropEvents(RunWorkerPool):
        def _drain_events(self) -> None:
            while True:
                item = self._events.get()
                if item is None:
                    return

    pool = DropEvents(1, timeout_s=20)
    try:
        result = pool.run_job(_job("late"))
    finally:
        pool.close()
    assert result["error"] == "worker events not delivered"
    assert result["content"] == "worker events not delivered"


def test_a_retired_worker_process_is_gone() -> None:
    pool = RunWorkerPool(1, max_runs=1, timeout_s=20)
    try:
        [old] = pool.pids
        pool.run_job(_job("one"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(old, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        with pytest.raises(ProcessLookupError):
            os.kill(old, 0)
        assert old not in pool.pids
    finally:
        pool.close()


_ORPHAN_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from mas.lab.benchmark.schedule.run_batch.workers import RunWorkerPool

    if __name__ == "__main__":
        pool = RunWorkerPool(2, timeout_s=30)
        print(" ".join(str(p) for p in pool.pids), flush=True)
        time.sleep(120)
    """
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_workers_exit_when_their_parent_is_killed(tmp_path: Path) -> None:
    script = tmp_path / "owner.py"
    script.write_text(_ORPHAN_SCRIPT, encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    children: list[int] = []
    try:
        assert parent.stdout is not None
        children = [int(token) for token in parent.stdout.readline().split()]
        assert len(children) == 2 and all(_alive(pid) for pid in children)
        parent.send_signal(signal.SIGKILL)
        parent.wait(timeout=10)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and any(_alive(pid) for pid in children):
            time.sleep(0.1)
        assert [pid for pid in children if _alive(pid)] == [], "workers outlived their killed parent"
    finally:
        for pid in children:
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)
        if parent.poll() is None:
            parent.kill()


def test_a_pool_that_fails_to_start_leaves_no_child(monkeypatch: pytest.MonkeyPatch) -> None:
    import multiprocessing

    from mas.lab.benchmark.schedule.run_batch import workers

    real = workers._Worker
    started: list[object] = []

    def flaky(ctx: object, events: object) -> object:
        if len(started) == 1:
            raise OSError("cannot spawn")
        started.append(real(ctx, events))
        return started[-1]

    monkeypatch.setattr(workers, "_Worker", flaky)
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(OSError, match="cannot spawn"):
        RunWorkerPool(2)
    assert {p.pid for p in multiprocessing.active_children()} - before == set()


def test_a_failed_replacement_does_not_hide_a_finished_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.benchmark.schedule.run_batch import workers

    real = workers._Worker
    spawned = {"n": 0}

    def flaky(ctx: object, events: object) -> object:
        spawned["n"] += 1
        if spawned["n"] == 2:
            raise OSError("cannot spawn replacement")
        return real(ctx, events)

    monkeypatch.setattr(workers, "_Worker", flaky)
    pool = RunWorkerPool(1, max_runs=1, timeout_s=20)
    try:
        [old] = pool.pids
        first = pool.run_job(_job("one"))
        assert first["error"] not in _DEATH_ERRORS
        os.kill(old, 0)
        assert old in pool.pids
        second = pool.run_job(_job("two"))
        assert second["error"] not in _DEATH_ERRORS
    finally:
        pool.close()


def test_plain_keeps_json_values_and_turns_paths_into_strings() -> None:
    value = {"a": 1, "b": True, "c": None, "d": [1.5, "x", ("t", Path("/tmp/x"))], "e": {"f": Path("/tmp/y")}}
    assert plain(value) == {"a": 1, "b": True, "c": None, "d": [1.5, "x", ["t", "/tmp/x"]], "e": {"f": "/tmp/y"}}


@pytest.mark.parametrize(
    ("value", "where"),
    [
        ({"obj": object()}, "job.obj"),
        ({"a": {1: "x"}}, "job.a"),
        ({"a": [1, {"s": {1, 2}}]}, r"job.a\[1\].s"),
        ({"when": __import__("datetime").datetime(2026, 1, 1)}, "job.when"),
        ({"raw": b"bytes"}, "job.raw"),
    ],
)
def test_plain_refuses_a_value_that_is_not_json_and_names_where_it_sits(value: object, where: str) -> None:
    with pytest.raises(ValueError, match=where):
        plain(value)


def test_shared_worker_cache_is_not_cleared_after_a_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml
    from mas.lab.benchmark.schedule.run_batch.workers import _run_job
    from mas.runtime.spec.batch_cache import BatchCache

    path = tmp_path / "doc.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    cache = BatchCache()
    cache.load_yaml(path)
    loads = {"n": 0}
    real = yaml.safe_load

    def wrapped(text: str) -> object:
        loads["n"] += 1
        return real(text)

    monkeypatch.setattr("yaml.safe_load", wrapped)
    with pytest.raises(Exception, match="not registered"):
        _run_job(
            {
                "job_id": "miss",
                "spec_path": str(tmp_path / "missing.yaml"),
                "output_dir": str(tmp_path),
                "run_input": {"user": []},
            },
            queue.Queue(),
            batch_cache=cache,
        )
    before = loads["n"]
    cache.load_yaml(path)
    assert loads["n"] == before
    assert len(cache) == 1


def test_a_step_that_does_not_declare_worker_safe_is_refused() -> None:
    assert _Undeclared.declared_worker_safe() is None
    with pytest.raises(RuntimeError, match=r"does not declare worker_safe"):
        require_worker_safe([{"name": "bare", "type": f"{__name__}:_Undeclared"}], base_dir=Path("."))


def test_a_step_that_declares_worker_safe_false_is_refused() -> None:
    with pytest.raises(RuntimeError, match=r"declares worker_safe = False"):
        require_worker_safe([{"name": "no", "type": f"{__name__}:_Unsafe"}], base_dir=Path("."))


def test_a_step_that_declares_worker_safe_is_accepted_and_a_subclass_inherits_it() -> None:
    require_worker_safe(
        [{"name": "yes", "type": f"{__name__}:_Safe"}, {"name": "kid", "type": f"{__name__}:_SafeChild"}],
        base_dir=Path("."),
    )
    assert _SafeChild.declared_worker_safe() is True


_ENV_WRITE_METHODS = {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_sys_modules(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _process_patches(source: str) -> list[str]:
    """Line tags for writes to os.environ / sys.modules (reads are allowed)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if _is_os_environ(node.value) or _is_sys_modules(node.value):
                hits.append(f"L{node.lineno}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if _is_os_environ(target) or _is_sys_modules(target):
                    hits.append(f"L{node.lineno}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _ENV_WRITE_METHODS and _is_os_environ(node.func.value):
                hits.append(f"L{node.lineno}")
            elif (
                node.func.attr in {"putenv", "unsetenv"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                hits.append(f"L{node.lineno}")
    return hits


def _step_source(manifest: Path, module: str) -> Path:
    parts = module.split(".")
    rel = Path(*parts).with_suffix(".py")
    stripped = Path(*parts[1:]).with_suffix(".py") if len(parts) > 1 else rel
    bases = (
        manifest.parent / "src" / rel,
        manifest.parent.parent / "src" / rel,
        manifest.parent / rel,
        manifest.parent / stripped,
        manifest.parent.parent / stripped,
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in bases:
        if not candidate.is_file():
            continue
        key = candidate.resolve()
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    assert found, f"no source for {module} from {manifest} (tried {bases})"
    return found[0]


def test_every_shipped_library_step_declares_worker_safe() -> None:
    import yaml
    from mas.lab.benchmark.pipeline.core import list_steps, step_worker_safety

    root = Path(__file__).resolve().parents[4]
    skip_parts = {".venv", "node_modules", ".git"}
    missing_attr: list[str] = []
    patches: list[str] = []
    names: list[str] = []
    for manifest in sorted(root.rglob("library.yaml")):
        if skip_parts & set(manifest.parts):
            continue
        plugins = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("plugins") or []
        for row in plugins:
            if row.get("type") != "step":
                continue
            name = str(row["name"])
            names.append(name)
            rel = f"{manifest.relative_to(root)}:{name}"
            if "worker_safe" not in (row.get("attributes") or {}):
                missing_attr.append(rel)
            module = str(row.get("module") or "")
            if module:
                source = _step_source(manifest, module)
                hits = _process_patches(source.read_text(encoding="utf-8"))
                if hits:
                    patches.append(f"{rel} {source.relative_to(root)} {hits}")
    assert "extract_trace_stats" in names
    assert missing_attr == []
    assert patches == []
    known = set(list_steps())
    registered_false = [
        name for name in names if name in known and step_worker_safety(name, base_dir=Path(".")) is not True
    ]
    assert registered_false == []
    assert step_worker_safety("extract_trace_stats", base_dir=Path(".")) is True


def test_experiment_yaml_worker_processes(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: workers\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "  scenarios:\n"
        "    - id: s1\n"
        "  run:\n"
        "    n_runs: 1\n"
        "  schedule:\n"
        "    worker_processes: 1\n",
        encoding="utf-8",
    )
    cfg = MASExperimentConfig.from_yaml(path)
    assert cfg.schedule.worker_processes == 1

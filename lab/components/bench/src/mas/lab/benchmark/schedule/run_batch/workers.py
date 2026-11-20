#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Process workers for lab MAS runs. Spawn only; jobs are plain dicts."""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from mas.runtime.aio import new_thread

logger = logging.getLogger(__name__)

_EVENT_BATCH = 8
_SENTINEL = None
_PARENT_POLL_S = 1.0
_EVENT_BARRIER_S = 10.0


def cpu_count() -> int:
    return os.cpu_count() or 1


def validate_worker_processes(n: Any) -> int:
    """Structure only: an integer of at least 1. The machine is checked at run time."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"schedule.worker_processes must be an integer >= 1, got {n!r}")
    return n


def validate_worker_max_runs(n: Any) -> int | None:
    """Structure only: omitted, or an integer of at least 1."""
    if n is None:
        return None
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"schedule.worker_max_runs must be an integer >= 1, got {n!r}")
    return n


def require_cores(n: int) -> int:
    """Refuse a worker count above this machine's core count (checked when a batch starts)."""
    cpus = cpu_count()
    if n > cpus:
        raise ValueError(
            f"schedule.worker_processes={n} is above the {cpus} CPU cores of this machine; "
            f"lower it to {cpus} or fewer, or run on a larger machine"
        )
    return n


def plain(value: Any, path: str = "job") -> Any:
    """Strict JSON-friendly copy of a job payload. Paths become strings.

    Anything else that is not a JSON value (a non-string key, a set, bytes, a
    datetime, a plugin object) raises ``ValueError`` naming where it sits.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: key {key!r} is a {type(key).__name__}, not a string")
            out[key] = plain(item, f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [plain(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    raise ValueError(f"{path}: {type(value).__name__} is not a JSON value and cannot be sent to a worker")


def _error_result(message: str) -> dict[str, Any]:
    return {
        "content": message,
        "status": "error",
        "usage": {},
        "agent_id": "",
        "session_id": "",
        "error": message,
    }


def run_input_payload(run_input: Any) -> dict[str, Any]:
    """The plain fields of a ``RunInput`` (a dataclass) for a job."""
    return plain(asdict(run_input), "run_input")


def require_worker_safe(specs: list[Any], *, base_dir: Path) -> None:
    """Refuse a pipeline step that has not declared it is safe to run beside process workers.

    Pipeline steps run in the parent process; a worker only runs the MAS.
    A step that patches the parent's process state (a replaced function, an
    environment change) would not reach the workers, so each step type must say
    so: ``worker_safe`` on its registry entry or on its class. Undeclared and
    ``False`` are both refused.
    """
    from mas.lab.benchmark.pipeline.core import step_worker_safety
    from mas.lab.benchmark.schedule.pipeline_resolve import spec_to_step_dict

    for spec in specs or ():
        data = spec if isinstance(spec, dict) else spec_to_step_dict(spec)
        step_type = str(data["type"])
        name = data.get("name") or step_type
        safety = step_worker_safety(step_type, base_dir=base_dir)
        if safety is None:
            raise RuntimeError(
                f"pipeline step {name!r} (type {step_type!r}) does not declare worker_safe; "
                "schedule.worker_processes > 1 refuses it. Declare worker_safe = True on the step class "
                "(or worker_safe: true under attributes in its library entry) when it does not patch "
                "process state that MAS runs depend on"
            )
        if not safety:
            raise RuntimeError(
                f"pipeline step {name!r} (type {step_type!r}) declares worker_safe = False; "
                "schedule.worker_processes > 1 refuses it"
            )


def _run_job(job: dict[str, Any], events: Any, batch_cache: Any | None = None) -> dict[str, Any]:
    pause_s = float(job.get("pause_s") or 0)
    if pause_s > 0:
        time.sleep(pause_s)
    from mas.lab.benchmark.pipeline.channel import reset_stream_id, set_stream_id
    from mas.lab.emulation_resolver import resolve_emulation_plugins
    from mas.lab.inputs import RunInput
    from mas.lab.lab.config import EmulationSpec
    from mas.lab.runners.context import RunContext
    from mas.lab.runners.invoke import invoke_runner
    from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream
    from mas.runtime.spec.batch_cache import BatchCache, bind_batch_cache, reset_batch_cache

    scenario_id = str(job.get("scenario_id") or "")
    stream_id = str(job.get("stream_id") or "")
    batch: list[tuple[str, dict[str, Any]]] = []

    def flush() -> None:
        if not batch:
            return
        events.put(("batch", list(batch)))
        batch.clear()

    def publish(event: dict[str, Any]) -> None:
        record = event
        if scenario_id and isinstance(record, dict) and not record.get("scenario"):
            record = {**record, "scenario": scenario_id}
        if not isinstance(record, dict) or record.get("source") == "transition":
            return
        batch.append((stream_id, record))
        if len(batch) >= _EVENT_BATCH:
            flush()

    class _ScenarioStream(EventStream):
        def publish(self, event: dict) -> None:
            publish(event)
            super().publish(event)

    stream = _ScenarioStream()
    token = set_event_stream(stream)
    own_cache = batch_cache is None
    cache = batch_cache if batch_cache is not None else BatchCache()
    cache_token = bind_batch_cache(cache)
    sid_token = set_stream_id(stream_id) if stream_id else None
    try:
        run_input = RunInput(**dict(job.get("run_input") or {}))
        emulation_raw = job.get("emulation")
        emulation = EmulationSpec.from_dict(emulation_raw) if isinstance(emulation_raw, dict) else None
        plugins = resolve_emulation_plugins(emulation, faults_config=job.get("faults"))
        extra = dict(job.get("extra") or {})
        root = extra.get("workspace_root")
        if isinstance(root, str):
            extra["workspace_root"] = Path(root)
        ctx = RunContext(
            prompt=str(job.get("prompt") or ""),
            config=dict(job.get("config") or {}),
            spec_path=Path(str(job["spec_path"])),
            output_dir=Path(str(job["output_dir"])),
            runner_id=str(job.get("runner_id") or ""),
            run_input=run_input,
            flavour=job.get("flavour"),
            run_seed=int(job.get("run_seed") or 0),
            overlay_refs=list(job.get("overlay_refs") or []),
            overlays_dir=Path(job["overlays_dir"]) if job.get("overlays_dir") else None,
            overlay_base_dir=Path(job["overlay_base_dir"]) if job.get("overlay_base_dir") else None,
            infra_refs=list(job.get("infra_refs") or []),
            session_id=job.get("session_id"),
            emulation_plugins=plugins,
            batch_cache=cache,
            extra=extra,
        )
        result = invoke_runner(ctx)
        return {
            "content": result.content,
            "status": result.status,
            "usage": result.metadata.get("usage", {}),
            "agent_id": result.metadata.get("agent_id", ""),
            "session_id": result.metadata.get("session_id", ""),
        }
    finally:
        flush()
        if sid_token is not None:
            reset_stream_id(sid_token)
        reset_event_stream(token)
        reset_batch_cache(cache_token)
        if own_cache:
            cache.clear()


def worker_main(jobs: Any, results: Any, events: Any) -> None:
    """Entry for a spawn worker. Rebuilds context; never unpickles a RunContext.

    Exits when it is told to, and also when its parent process is gone: a
    parent killed without cleanup must not leave workers behind.
    """
    from mas.runtime.spec.batch_cache import BatchCache

    parent = multiprocessing.parent_process()
    cache = BatchCache()
    while True:
        try:
            job = jobs.get(timeout=_PARENT_POLL_S)
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                return
            continue
        if job is _SENTINEL:
            return
        job_id = job.get("job_id")
        try:
            payload = _run_job(job, events, batch_cache=cache)
            reply = {"ok": True, "job_id": job_id, "result": payload}
        except Exception as exc:
            reply = {"ok": False, "job_id": job_id, "result": _error_result(str(exc)), "error": str(exc)}
        # The marker follows this job's last event batch on the same queue.
        events.put(("done", job_id))
        results.put(reply)


class _Worker:
    def __init__(self, ctx: Any, events: Any) -> None:
        self.jobs = ctx.Queue()
        self.results = ctx.Queue()
        self.runs = 0
        self.proc = ctx.Process(target=worker_main, args=(self.jobs, self.results, events), daemon=True)
        self.proc.start()

    @property
    def alive(self) -> bool:
        return bool(self.proc.is_alive())

    def terminate(self) -> None:
        if self.alive:
            self.proc.terminate()
        self.proc.join(timeout=5)
        if self.alive:
            self.proc.kill()
            self.proc.join(timeout=2)


class RunWorkerPool:
    """N spawn children. Lease one worker per MAS run; restart a dead or retired child.

    A worker that has taken ``max_runs`` jobs is retired by the parent before it
    goes back to the free queue, so the next job never meets a worker that is
    about to leave.
    """

    start_method = "spawn"

    def __init__(
        self,
        n: int,
        *,
        max_runs: int = 0,
        on_event=None,
        timeout_s: float = 300.0,
    ) -> None:
        self._n = require_cores(validate_worker_processes(n))
        self._max_runs = max(0, int(max_runs or 0))
        self._timeout_s = float(timeout_s)
        self._on_event = on_event
        self._ctx = multiprocessing.get_context(self.start_method)
        self._events = self._ctx.Queue()
        self._free: queue.Queue[_Worker] = queue.Queue()
        self._workers: list[_Worker] = []
        self._closed = False
        self._lock = threading.Lock()
        self._done: dict[str, threading.Event] = {}
        try:
            for _ in range(self._n):
                worker = _Worker(self._ctx, self._events)
                self._workers.append(worker)
                self._free.put(worker)
        except BaseException:
            self._stop_workers()
            raise
        self._event_thread = new_thread(self._drain_events, name="lab-worker-events", daemon=True)
        self._event_thread.start()

    @property
    def pids(self) -> list[int]:
        """Process ids of the current workers."""
        with self._lock:
            return [int(worker.proc.pid) for worker in self._workers if worker.proc.pid]

    def _drain_events(self) -> None:
        while True:
            item = self._events.get()
            if item is _SENTINEL:
                return
            try:
                kind, payload = item
            except (TypeError, ValueError):
                continue
            if kind == "done":
                with self._lock:
                    marker = self._done.get(str(payload))
                if marker is not None:
                    marker.set()
                continue
            if kind != "batch" or not self._on_event:
                continue
            for stream_id, record in payload:
                try:
                    self._on_event(record, stream_id=stream_id)
                except Exception:
                    logger.debug("worker event sink failed", exc_info=True)

    def _replace(self, old: _Worker) -> _Worker:
        fresh = _Worker(self._ctx, self._events)
        old.terminate()
        with self._lock:
            self._workers = [fresh if worker is old else worker for worker in self._workers]
            if fresh not in self._workers:
                self._workers.append(fresh)
        return fresh

    def _retired(self, worker: _Worker) -> bool:
        return self._max_runs > 0 and worker.runs >= self._max_runs

    @contextmanager
    def _lease(self) -> Iterator[_Worker]:
        worker = self._free.get()
        if worker is _SENTINEL or self._closed:
            if worker is not _SENTINEL:
                worker.terminate()
            raise RuntimeError("worker pool closed")
        if not worker.alive:
            worker = self._replace(worker)
        try:
            yield worker
        finally:
            if self._closed:
                worker.terminate()
            else:
                try:
                    if not worker.alive or self._retired(worker):
                        worker = self._replace(worker)
                except Exception:
                    logger.exception("failed to replace a worker")
                    if not worker.alive:
                        raise
                self._free.put(worker)

    def run_job(self, job: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        """Block until the worker returns a result dict. A dead worker is one error.

        The timeout starts when a worker is leased, not while this call waits for a free one.
        """
        if self._closed:
            return _error_result("worker pool closed")
        wait = self._timeout_s if timeout_s is None else float(timeout_s)
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return _error_result("job has no job_id")
        marker = threading.Event()
        with self._lock:
            if job_id in self._done:
                return _error_result("duplicate job_id")
            self._done[job_id] = marker
        try:
            with self._lease() as worker:
                deadline = time.monotonic() + wait
                worker.runs += 1
                worker.jobs.put(job)
                msg = None
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        msg = worker.results.get(timeout=min(0.2, remaining))
                        break
                    except queue.Empty:
                        if not worker.alive:
                            return _error_result("worker died")
                if msg is None:
                    worker.terminate()
                    return _error_result("worker timed out")
                # Every event of this job reaches the sink before the result is returned.
                if not marker.wait(timeout=_EVENT_BARRIER_S):
                    return _error_result("worker events not delivered")
                if not msg.get("ok"):
                    result = dict(msg.get("result") or {})
                    result.setdefault("status", "error")
                    err = str(msg.get("error") or result.get("error") or "worker failed")
                    result["error"] = err
                    if not result.get("content"):
                        result["content"] = err
                    return result
                return dict(msg.get("result") or {})
        except RuntimeError as exc:
            if self._closed or "closed" in str(exc).lower():
                return _error_result(str(exc) or "worker pool closed")
            raise
        finally:
            with self._lock:
                self._done.pop(job_id, None)

    def _stop_workers(self) -> None:
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            try:
                worker.jobs.put(_SENTINEL)
            except Exception:
                pass
            worker.terminate()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            workers = list(self._workers)
        for _ in range(len(workers) + 1):
            try:
                self._free.put_nowait(_SENTINEL)
            except Exception:
                break
        self._stop_workers()
        try:
            self._events.put(_SENTINEL)
        except Exception:
            pass
        self._event_thread.join(timeout=2)

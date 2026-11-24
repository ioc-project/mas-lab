#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab API a pipeline step may call: schedule a batch, read it, stop it, ask for next runs.

Authenticated like any other control call (controller RPC / HTTP). A budget owned
by the pipeline bounds how many batches it may start, so a runaway loop stops.

A batch runs wherever its runner puts it: the controller when one is running in
this process, otherwise a child ``mas-lab benchmark run``. Either way the batch
writes an ordinary results tree, which is all the rest of this module reads.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mas.lab.benchmark.execution.roles import PlannedRun
from mas.lab.benchmark.execution.wire import read_results, run_run_dir
from mas.runtime.secrets import child_env

DEFAULT_MAX_SCHEDULED_BATCHES = 8
MAX_BATCH_DEPTH = 3
"""Most batches that may start one another (a batch's own pipeline scheduling another ...)."""
DEPTH_MARKER = ".mas-batch-depth"
TERMINAL = frozenset({"complete", "error", "stopped"})

_LOCK = threading.Lock()
_BATCHES: dict[str, "BatchHandle"] = {}
_RUNNER: Callable[["BatchHandle"], Any] | None = None


class BatchBudget:
    """How many batches one owner may start. Guarded by the module lock."""

    def __init__(self, max_scheduled_batches: int = DEFAULT_MAX_SCHEDULED_BATCHES) -> None:
        self.max_scheduled_batches = max(1, int(max_scheduled_batches))
        self.started = 0

    def lower(self, cap: int) -> None:
        with _LOCK:
            self.max_scheduled_batches = min(self.max_scheduled_batches, max(1, int(cap)))

    def claim(self) -> None:
        with _LOCK:
            if self.started >= self.max_scheduled_batches:
                raise ValueError(
                    f"pipeline scheduled {self.started} batches (max {self.max_scheduled_batches}); refusing another"
                )
            self.started += 1


def pipeline_budget(pipeline: Any) -> BatchBudget:
    """The one budget of a pipeline run, kept on the pipeline so every step of it shares it."""
    with _LOCK:
        budget = getattr(pipeline, "_batch_budget", None)
        if budget is None:
            cap = getattr(getattr(pipeline, "config", None), "max_scheduled_batches", None)
            budget = BatchBudget(int(cap or DEFAULT_MAX_SCHEDULED_BATCHES))
            pipeline._batch_budget = budget
        return budget


def set_batch_runner(runner: Callable[["BatchHandle"], Any] | None) -> None:
    """Tests inject a fake. Production uses the controller or a child process."""
    global _RUNNER
    _RUNNER = runner


def reset_batches() -> None:
    """Drop in-memory handles. Tests call this so batches do not leak."""
    with _LOCK:
        for handle in _BATCHES.values():
            handle.kill()
        _BATCHES.clear()
    set_batch_runner(None)


@dataclass
class BatchHandle:
    """One scheduled experiment batch."""

    batch_id: str
    experiment_yaml: Path
    output_dir: Path
    worker_id: str = ""
    status: str = "queued"
    error: str = ""
    runs: list[PlannedRun] = field(default_factory=list)
    dir_names: dict[str, str] = field(default_factory=dict)
    process: Any = None
    stderr_file: Any = None
    plan_file: Path | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "experiment_yaml": str(self.experiment_yaml),
            "output_dir": str(self.output_dir),
            "worker_id": self.worker_id,
            "status": self.status,
            "error": self.error,
            "n_runs": len(self.runs),
        }

    def results(self) -> dict[str, Any]:
        snap = self.snapshot()
        snap["incomplete"] = [run.id for run in incomplete_runs(self)]
        return snap

    def run_dir(self, run: PlannedRun) -> Path:
        return run_run_dir(self.output_dir, run, self.dir_names)

    def kill(self) -> None:
        proc = self.process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def stop(self) -> dict[str, Any]:
        api = _controller() if self.worker_id else None
        if api is not None:
            api.cancel_worker(self.worker_id)
        self.kill()
        if self.status not in TERMINAL:
            self.status = "stopped"
        return self.snapshot()


def _controller():
    """The controller API running in this process, or ``None``."""
    from mas.lab.controller.api import peek_controller_api

    return peek_controller_api()


def _spawn_child(handle: BatchHandle) -> None:
    """Run the batch as a child ``mas-lab benchmark run`` writing into ``handle.output_dir``."""
    code = "import sys; from mas.lab.cli import app; app()"
    handle.stderr_file = tempfile.TemporaryFile()
    handle.process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            "benchmark",
            "run",
            str(handle.experiment_yaml),
            "-o",
            str(handle.output_dir),
            "--no-progress",
            *(["--run-plan", str(handle.plan_file)] if handle.plan_file else []),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=handle.stderr_file,
        env=child_env(lab=True),
    )
    handle.status = "running"


def _default_runner(handle: BatchHandle) -> None:
    api = _controller()
    if api is None:
        _spawn_child(handle)
        return
    spec = {"experiment_yaml": str(handle.experiment_yaml), "output_dir": str(handle.output_dir)}
    if handle.plan_file:
        spec["plan_file"] = str(handle.plan_file)
    result = api.submit_experiment(spec)
    handle.worker_id = str(result.get("worker_id") or result.get("job_id") or "")
    handle.status = "running"


def batch_depth(directory: Path | str | None) -> int:
    """How many batches deep *directory* sits: the nearest ancestor marker, else 0."""
    if directory is None:
        return 0
    path = Path(directory).expanduser().resolve()
    for candidate in (path, *path.parents):
        marker = candidate / DEPTH_MARKER
        if marker.is_file():
            try:
                return max(0, int(marker.read_text(encoding="utf-8").strip()))
            except (OSError, ValueError):
                return MAX_BATCH_DEPTH  # an unreadable marker is not a reason to allow more
    return 0


def schedule_batch(
    experiment_yaml: Path | str,
    *,
    budget: BatchBudget | None = None,
    output_dir: Path | str | None = None,
    plan_file: Path | str | None = None,
    lineage_dir: Path | str | None = None,
    max_scheduled_batches: int | None = None,
    runner: Callable[[BatchHandle], Any] | None = None,
) -> BatchHandle:
    """Schedule a new experiment batch. ``budget`` is the pipeline's; ``None`` means the caller bounds it.

    ``lineage_dir`` is where the caller's own results live: a batch started from inside a
    batch sits one level deeper, and more than ``MAX_BATCH_DEPTH`` levels is refused (each
    child has its own budget, so the budget alone cannot stop a batch that schedules itself).

    The experiment is loaded and planned first, so a bad file costs no budget. A batch that
    fails to start is returned with ``status == "error"``.
    """
    path = Path(experiment_yaml).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"experiment yaml not found: {path}")
    depth = batch_depth(lineage_dir)
    if depth >= MAX_BATCH_DEPTH:
        raise ValueError(f"batches started from batches are {depth} deep (max {MAX_BATCH_DEPTH}); refusing another")
    from mas.lab.benchmark.execution.planner import plan_experiment

    loaded, runs = plan_experiment(path)
    if plan_file is not None:
        from mas.lab.benchmark.execution.wire import read_plan, select_runs

        runs, _ = select_runs(runs, read_plan(Path(plan_file)))
    if budget is not None:
        if max_scheduled_batches is not None:
            budget.lower(max_scheduled_batches)
        budget.claim()
    batch_id = uuid.uuid4().hex[:12]
    out = Path(output_dir).expanduser().resolve() if output_dir else path.parent / "output-batches" / batch_id
    handle = BatchHandle(
        batch_id=batch_id,
        experiment_yaml=path,
        output_dir=out,
        runs=runs,
        dir_names={s.id: s.output_dir_name for s in loaded.exp.scenarios},
        plan_file=Path(plan_file).resolve() if plan_file else None,
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / DEPTH_MARKER).write_text(str(depth + 1), encoding="utf-8")
    with _LOCK:
        _BATCHES[batch_id] = handle
    try:
        (runner or _RUNNER or _default_runner)(handle)
    except Exception as exc:
        handle.status = "error"
        handle.error = str(exc)
    return handle


def get_batch(batch_id: str) -> BatchHandle | None:
    with _LOCK:
        return _BATCHES.get(batch_id)


def stop_batch(batch_id: str) -> dict[str, Any]:
    handle = get_batch(batch_id)
    if handle is None:
        raise KeyError(f"unknown batch {batch_id!r}")
    return handle.stop()


def batch_status(batch_id: str) -> dict[str, Any]:
    handle = get_batch(batch_id)
    if handle is None:
        raise KeyError(f"unknown batch {batch_id!r}")
    _refresh(handle)
    return handle.results()


def _refresh(handle: BatchHandle) -> None:
    if handle.status in TERMINAL:
        return
    proc = handle.process
    if proc is not None:
        code = proc.poll()
        if code is None:
            return
        if code == 0:
            handle.status = "complete"
        else:
            handle.status = "error"
            tail = ""
            if handle.stderr_file is not None:
                handle.stderr_file.seek(0)
                tail = handle.stderr_file.read()[-800:].decode("utf-8", "replace")
            handle.error = f"batch process exited {code}: {tail}".strip()
        return
    if not handle.worker_id:
        return
    api = _controller()
    if api is None:
        return
    detail = api.get_worker(handle.worker_id)
    if not detail:
        return
    status = str(detail.get("status") or handle.status)
    if status in {"completed", "complete", "ok"}:
        handle.status = "complete"
    elif status in {"failed", "error"}:
        handle.status = "error"
        handle.error = str(detail.get("error") or handle.error)
    elif status in {"cancelled", "canceled"}:
        handle.status = "stopped"
    else:
        handle.status = status


def incomplete_runs(handle: BatchHandle) -> list[PlannedRun]:
    """Runs without a complete result under the batch's output tree."""
    results = read_results(handle.output_dir, handle.runs, handle.dir_names)
    return [run for run in handle.runs if not results[run.id].complete]


def next_runs(
    *,
    handle: BatchHandle | None = None,
    runs: list[PlannedRun] | None = None,
    scores: dict[str, float] | None = None,
    strategy: str = "remaining",
    keep_fraction: float = 0.5,
    keep_n: int | None = None,
) -> list[PlannedRun]:
    """The next runs, from an explicit pool or from *handle*.

    ``remaining`` returns the pool (a batch's incomplete runs). ``successive_halving`` keeps the best-scoring
    scenarios of the pool; ``score_driven`` keeps those at or above the median score.
    ``scores`` must name every scenario in the pool: guessing a score for a missing
    one would silently rank it.
    """
    name = str(strategy or "remaining").strip().lower()
    remaining = name in {"remaining", "incomplete", ""}
    if runs is not None:
        pool = list(runs)
    elif handle is not None:
        # Ranking needs every scenario of the batch, not only the ones still unfinished.
        pool = incomplete_runs(handle) if remaining else list(handle.runs)
    else:
        raise ValueError("next_runs needs runs or a batch")
    if remaining:
        return pool
    if name not in {"successive_halving", "score_driven", "score"}:
        raise ValueError(f"unknown next_runs strategy {strategy!r}")
    grouped: dict[str, list[PlannedRun]] = {}
    for run in pool:
        grouped.setdefault(run.scenario_id, []).append(run)
    if not grouped:
        return []
    missing = sorted(sid for sid in grouped if sid not in (scores or {}))
    if missing:
        raise ValueError(f"no score for scenario(s) {missing}; scores cover {sorted(scores or {})}")
    ranking = {sid: float(scores[sid]) for sid in grouped}
    ordered = sorted(grouped, key=lambda sid: (-ranking[sid], sid))
    if name == "successive_halving":
        if keep_n is not None:
            if int(keep_n) < 1:
                raise ValueError("keep_n must be >= 1")
            n_keep = min(int(keep_n), len(ordered))
        else:
            frac = float(keep_fraction)
            if not 0.0 < frac <= 1.0:
                raise ValueError("keep_fraction must be in (0, 1]")
            n_keep = max(1, int(round(len(ordered) * frac)))
        kept = set(ordered[:n_keep])
    else:
        values = sorted(ranking.values())
        median = values[len(values) // 2]
        kept = {sid for sid, score in ranking.items() if score >= median}
    return [run for run in pool if run.scenario_id in kept]

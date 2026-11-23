#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Worker implementations."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from mas.lab.controller.io_capture import capture_worker_io
from mas.lab.controller.registry import WorkerRegistry
from mas.lab.controller.worker_model import WorkerKind, WorkerRecord, WorkerStatus
from mas.runtime.aio import new_thread

logger = logging.getLogger(__name__)


class WorkerRunner:
    """Runs worker callables in background threads."""

    def __init__(self, registry: WorkerRegistry) -> None:
        self.registry = registry
        self._cancel_flags: dict[str, threading.Event] = {}

    def submit(self, record: WorkerRecord, fn: Callable[[], Any]) -> None:
        cancel = threading.Event()
        self._cancel_flags[record.id] = cancel

        def _run() -> None:
            record.status = WorkerStatus.RUNNING
            record.started_at = time.time()
            record.pid = threading.get_ident()
            try:
                if cancel.is_set():
                    record.status = WorkerStatus.CANCELLED
                    return
                result = fn()
                if cancel.is_set():
                    record.status = WorkerStatus.CANCELLED
                elif result is False:
                    record.status = WorkerStatus.FAILED
                    record.exit_code = 1
                    record.error = "worker returned failure"
                else:
                    record.status = WorkerStatus.COMPLETED
                    record.exit_code = 0
                    record.result = result
            except Exception as exc:
                logger.exception("Worker %s failed", record.id)
                record.status = WorkerStatus.FAILED
                record.exit_code = 1
                record.error = str(exc)
                record.append_stderr(str(exc))
            finally:
                record.finished_at = time.time()
                self._cancel_flags.pop(record.id, None)

        new_thread(target=_run, daemon=True, name=f"worker-{record.id}").start()

    def cancel(self, worker_id: str) -> bool:
        event = self._cancel_flags.get(worker_id)
        if event is not None:
            event.set()
        return self.registry.cancel(worker_id)


def run_benchmark_worker(registry: WorkerRegistry, runner: "WorkerRunner", spec: dict) -> WorkerRecord:
    experiment_yaml = Path(spec["experiment_yaml"])
    cmd_parts = ["mas-lab", "benchmark", "run", str(experiment_yaml)]
    for flag in ("force", "dry_run", "single_run", "force_lock"):
        if spec.get(flag):
            cmd_parts.append(f"--{flag.replace('_', '-')}")
    # The CLI's environment travels in the spec for the fixtures; the record is shown to clients and must not.
    record = registry.create(
        WorkerKind.BENCHMARK,
        spec={key: value for key, value in spec.items() if key != "fixture_env"},
        command=" ".join(cmd_parts),
        endpoint="/api/benchmark/run",
    )

    def _execute() -> bool:
        import traceback

        from mas.lab.benchmark.engine import BenchmarkRunOptions, run_benchmark

        with capture_worker_io(record):
            try:
                ok = asyncio.run(
                    run_benchmark(
                        experiment_yaml=experiment_yaml,
                        options=BenchmarkRunOptions.from_spec(spec),
                    )
                )
            except Exception:
                # Print the full traceback into the captured stream, then re-raise
                # so the CLI shows the real cause, not just "benchmark run failed".
                traceback.print_exc()
                raise
            if not ok:
                raise RuntimeError("benchmark run failed — see the logged error above for the cause")
        return ok

    runner.submit(record, _execute)
    return record


def _run_spec_with_demo_ui(spec: dict) -> dict:
    """The run spec the benchmark engine gets: the UI's choices as explicit keys.

    Nothing is written to ``os.environ``. ``workspace_infra_refs`` is the list that
    replaces the workspace's (empty: ignore the process's ``MAS_INFRA_REFS`` and
    use the workspace list; absent: leave the CLI's override alone, see
    ``keep_process_infra``). ``llm_cache_path`` is the default judge cache of a
    UI live job; the CLI's own ``MAS_LLM_CACHE`` still wins over it.
    """
    live = {**spec, "progress": False}
    refs = [str(item) for item in (spec.get("infra_refs") or []) if item]
    if refs:
        live["workspace_infra_refs"] = refs
    elif not spec.get("keep_process_infra"):
        live["workspace_infra_refs"] = []
    cwd = spec.get("cwd")
    if cwd and not spec.get("llm_cache_path"):
        live["llm_cache_path"] = str(Path(cwd) / "infra" / ".cache" / "eval.llm-cache.json")
    return live


def run_experiment_worker(registry: WorkerRegistry, runner: "WorkerRunner", spec: dict) -> WorkerRecord:
    """In-process experiment worker: same engine as a benchmark, live ingest, no stdout dump."""
    experiment_yaml = Path(spec["experiment_yaml"])
    record = registry.create(
        WorkerKind.EXPERIMENT,
        spec=spec,
        command=spec.get("command") or f"mas-lab experiment run {experiment_yaml}",
        endpoint=spec.get("endpoint", "/api/experiment/run"),
    )

    def _execute() -> bool:
        import traceback

        from mas.lab.benchmark.engine import BenchmarkRunOptions, run_benchmark

        live_spec = _run_spec_with_demo_ui(spec)
        cwd = spec.get("cwd")
        prev_cwd = os.getcwd()
        with capture_worker_io(record):
            try:
                if cwd:
                    os.chdir(str(cwd))
                ok = asyncio.run(
                    run_benchmark(
                        experiment_yaml=experiment_yaml,
                        options=BenchmarkRunOptions.from_spec(live_spec),
                    )
                )
            except Exception:
                traceback.print_exc()
                raise
            finally:
                os.chdir(prev_cwd)
            if not ok:
                raise RuntimeError("experiment run failed — see the logged error above for the cause")
        return ok

    runner.submit(record, _execute)
    return record


def run_application_worker(registry: WorkerRegistry, runner: "WorkerRunner", spec: dict) -> WorkerRecord:
    record = registry.create(
        WorkerKind.APPLICATION,
        spec=spec,
        command=spec.get("command", "application run"),
        endpoint=spec.get("endpoint", "/api/run"),
    )

    def _execute() -> dict:
        from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID, normalize_runner_id
        from mas.lab.runners.factory import RunnerFactory

        runner_id = normalize_runner_id(spec.get("runner_id", DEFAULT_LAB_RUNNER_ID))
        app_runner = RunnerFactory.get(runner_id)
        output_dir = Path(spec.get("output_dir") or tempfile.mkdtemp(prefix="mas-lab-run-"))
        output_dir.mkdir(parents=True, exist_ok=True)
        result = app_runner.run(
            spec["prompt"],
            config=spec.get("config") or {},
            spec_path=Path(spec["spec_path"]),
            flavour=spec.get("flavour"),
            output_dir=output_dir,
            turns=spec.get("turns"),
            session_id=spec.get("session_id"),
            memory_seeds=spec.get("memory_seeds"),
            run_seed=spec.get("run_seed", 0),
            emulation_plugins=spec.get("emulation_plugins"),
        )
        return {
            "content": result.content,
            "status": result.status,
            "error": result.error,
            "output_dir": str(output_dir),
            "metadata": result.metadata,
        }

    runner.submit(record, _execute)
    return record


def run_pipeline_worker(registry: WorkerRegistry, runner: "WorkerRunner", spec: dict) -> WorkerRecord:
    record = registry.create(
        WorkerKind.PIPELINE,
        spec=spec,
        command=spec.get("command", "pipeline run"),
        endpoint="/api/pipeline/run",
    )

    def _execute() -> Any:
        import asyncio
        from pathlib import Path

        from mas.lab.benchmark.pipeline import Pipeline, PipelineExecutor
        from mas.lab.benchmark.pipeline.channel import install_channel
        from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
        from mas.lab.benchmark.pipeline.config import PipelineEngineSpec
        from mas.lab.lab.config import discover_lab_context, inject_lab_libraries

        pipeline_yaml = Path(spec["pipeline_yaml"])
        inject_lab_libraries(discover_lab_context(pipeline_yaml.resolve()))
        _load_lab_custom_steps(pipeline_yaml.resolve())
        try:
            install_channel(PipelineEngineSpec())
        except Exception:
            pass
        pipeline = Pipeline.from_yaml(pipeline_yaml)
        executor = PipelineExecutor(
            pipeline,
            output_dir=Path(spec["output_dir"]) if spec.get("output_dir") else None,
        )
        result = asyncio.run(
            executor.run(
                steps=spec.get("only"),
                force_rerun=spec.get("force"),
                dry_run=spec.get("dry_run", False),
                parallel=spec.get("parallel", True),
                template_vars=spec.get("variables") or {},
            )
        )
        if not result.success:
            raise RuntimeError(f"pipeline failed — {result.summary()}")
        return {"success": True, "summary": result.summary()}

    runner.submit(record, _execute)
    return record

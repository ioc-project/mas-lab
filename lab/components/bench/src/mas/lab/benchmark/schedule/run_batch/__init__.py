#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""MAS batch scheduler — plan runs, invoke runtime plugins, post-pipeline."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.schedule.run_batch.execute import execute_batch
from mas.lab.benchmark.schedule.run_batch.finalize import abort_batch, finalize_batch
from mas.lab.benchmark.schedule.run_batch.load import load_experiment, print_dry_run
from mas.lab.benchmark.schedule.run_batch.prepare import prepare_batch

logger = logging.getLogger(__name__)

_ACTIVE = [0]  # batches running in this process
_ACTIVE_LOCK = threading.Lock()


async def _run_mas_benchmark(
    experiment_yaml: Path,
    progress: bool = True,
    overrides: Optional[list[str]] = None,
    dry_run: bool = False,
    max_runs: Optional[int] = None,
    limit_scenarios: Optional[int] = None,
    scenario_id: Optional[str] = None,
    dataset_item: Optional[str] = None,
    plan_file: Optional[Path] = None,
    single_run: bool = False,
    flavour_name: Optional[str] = None,
    controls: Optional[list[str]] = None,
    infra_name: Optional[str] = None,
    force: bool = False,
    trace_cache_dir: Optional[Path] = None,
    data_cache_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    scheduler: Optional[str] = None,
    step_overrides: Optional[list] = None,
    pipeline_attachments: Optional[list] = None,
    experiment_overlays: Optional[list] = None,
    clean_stale: Optional[bool] = None,
    pipeline_engine: Optional[str] = None,
    fixture_env: Optional[dict[str, str]] = None,
    demo_ui: Optional[dict] = None,
) -> bool:
    """Run a MAS batch benchmark from a *MASExperimentConfig* YAML.

    Makefile-like idempotency (when ``force=False``):
    - Runs that already have a non-empty ``traces/events.jsonl`` are **skipped**.
    - Re-running after an interruption continues only the missing runs.
    - A run is re-executed only if its trace file is absent or empty.

    Use ``force=True`` (``--force`` on the CLI) to wipe all prior outputs and
    start fresh.

    ``demo_ui`` carries what the demo UI bound for this run: ``ingest_url``, ``ref``,
    ``scenario_infra_refs`` and ``scenario_overlays``. Nothing is read from the environment.

    Returns True on success (exit code 0).
    """
    loaded = load_experiment(
        experiment_yaml,
        overrides=overrides,
        max_runs=max_runs,
        limit_scenarios=limit_scenarios,
        scenario_id=scenario_id,
        dataset_item=dataset_item,
        plan_file=plan_file,
        single_run=single_run,
        flavour_name=flavour_name,
        controls=controls,
        infra_name=infra_name,
        trace_cache_dir=trace_cache_dir,
        step_overrides=step_overrides,
        pipeline_attachments=pipeline_attachments,
        experiment_overlays=experiment_overlays,
    )
    if loaded is None:
        return False

    if pipeline_engine:
        emu = getattr(loaded.exp, "bench_emulation", None)
        pipe = getattr(emu, "pipeline", None) if emu is not None else None
        if pipe is not None and hasattr(pipe, "engine"):
            pipe.engine = str(pipeline_engine)

    if dry_run:
        from mas.lab.benchmark.stale_cleanup import maybe_handle_stale_outputs

        _out = output_dir.expanduser().resolve() if output_dir else loaded.exp.output_dir
        maybe_handle_stale_outputs(
            _out,
            loaded.scenario_ids,
            loaded.experiment_yaml,
            clean_stale=clean_stale,
            dry_run=True,
            trace_cache_dir=loaded.trace_cache_dir,
        )
        print_dry_run(loaded, scheduler=scheduler)
        return True

    prepared = None
    terminated = False
    import atexit
    import signal

    def _stop_fixtures(*, force: bool, last: bool = True) -> None:
        from mas.lab.benchmark.service_manager import stop_all_shared

        registry = getattr(prepared, "fixture_registry", None)
        if registry is not None:
            try:
                registry.close_all(force=force)
            except Exception:
                logger.warning("fixture shutdown failed", exc_info=True)
        if last:  # a batch that ends while another runs in this process must not stop that one's services
            stop_all_shared(include_keep_running=force)

    def _at_exit() -> None:
        _stop_fixtures(force=False)

    atexit.register(_at_exit)

    def _on_term(_signum: int, _frame: object) -> None:
        # No cleanup here: a handler runs between any two bytecodes and could re-enter a lock the
        # interrupted code holds. The ``finally`` below does the work, with ``force`` set.
        nonlocal terminated
        terminated = True
        raise KeyboardInterrupt

    prev_term = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, OSError):
        prev_term = None

    from contextlib import ExitStack

    from mas.runtime.engine.llm_scheduler import default_user

    run_scope = ExitStack()  # the scheduler's fairness key for this run only
    run_scope.enter_context(default_user(getattr(loaded.exp, "name", "") or "experiment"))
    with _ACTIVE_LOCK:
        _ACTIVE[0] += 1
    try:
        prepared = await prepare_batch(
            loaded,
            output_dir=output_dir,
            force=force,
            data_cache_dir=data_cache_dir,
            clean_stale=clean_stale,
            base_env=fixture_env,
            demo_ui=demo_ui,
        )
        try:
            execution = await execute_batch(
                loaded,
                prepared,
                progress=progress,
                force=force,
                scheduler=scheduler,
            )
        except BaseException as exc:
            abort_batch(prepared, exc)
            raise
        return await finalize_batch(
            loaded,
            prepared,
            execution,
            progress=progress,
            data_cache_dir=data_cache_dir,
        )
    finally:
        if prev_term is not None:
            try:
                signal.signal(signal.SIGTERM, prev_term)
            except (ValueError, OSError):
                pass
        with _ACTIVE_LOCK:
            _ACTIVE[0] -= 1
            last = _ACTIVE[0] == 0
        _stop_fixtures(force=terminated, last=last)
        run_scope.close()
        atexit.unregister(_at_exit)  # one handler per batch, not one per call for the life of the process


async def run_mas_benchmark(experiment_yaml: Path, *, demo_ui: Optional[dict] = None, **kwargs: Any) -> bool:
    """Run a MAS batch benchmark; see :func:`_run_mas_benchmark` for the options."""
    from mas.lab.benchmark.pipeline.ui_bridge import DemoUiBinding, bind_demo_ui

    binding = None
    if demo_ui and (demo_ui.get("ingest_url") or demo_ui.get("ref")):
        binding = DemoUiBinding(ingest_url=str(demo_ui.get("ingest_url") or ""), ref=dict(demo_ui.get("ref") or {}))
    from contextlib import ExitStack

    from mas.ctl.workspace.config import bind_infra_refs
    from mas.ctl.workspace.user_connections import load_user_connections
    from mas.lab.connections import bind_user_connections, reset_user_connections
    from mas.runtime.engine.llm_cache import bind_paths

    ui = demo_ui or {}
    with ExitStack() as stack:
        stack.callback(reset_user_connections, bind_user_connections(load_user_connections()))
        stack.enter_context(bind_demo_ui(binding))
        stack.enter_context(bind_infra_refs(ui.get("workspace_infra_refs")))
        stack.enter_context(bind_paths(default=ui.get("llm_cache_path")))
        return await _run_mas_benchmark(experiment_yaml, demo_ui=demo_ui, **kwargs)


__all__ = ["run_mas_benchmark"]

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""MAS batch scheduler — plan runs, invoke runtime plugins, post-pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mas.lab.benchmark.schedule.run_batch.execute import execute_batch
from mas.lab.benchmark.schedule.run_batch.finalize import finalize_batch
from mas.lab.benchmark.schedule.run_batch.load import load_experiment, print_dry_run
from mas.lab.benchmark.schedule.run_batch.prepare import prepare_batch


async def run_mas_benchmark(
    experiment_yaml: Path,
    progress: bool = True,
    overrides: Optional[list[str]] = None,
    dry_run: bool = False,
    max_runs: Optional[int] = None,
    limit_scenarios: Optional[int] = None,
    scenario_id: Optional[str] = None,
    dataset_item: Optional[str] = None,
    single_run: bool = False,
    flavour_name: Optional[str] = None,
    infra_name: Optional[str] = None,
    force: bool = False,
    trace_cache_dir: Optional[Path] = None,
    data_cache_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    strategy: Optional[str] = None,
    step_overrides: Optional[list] = None,
    pipeline_attachments: Optional[list] = None,
    experiment_overlays: Optional[list] = None,
    clean_stale: Optional[bool] = None,
    pipeline_engine: Optional[str] = None,
) -> bool:
    """Run a MAS batch benchmark from a *MASExperimentConfig* YAML.

    Makefile-like idempotency (when ``force=False``):
    - Runs that already have a non-empty ``traces/events.jsonl`` are **skipped**.
    - Re-running after an interruption continues only the missing runs.
    - A run is re-executed only if its trace file is absent or empty.

    Use ``force=True`` (``--force`` on the CLI) to wipe all prior outputs and
    start fresh.

    Returns True on success (exit code 0).
    """
    loaded = load_experiment(
        experiment_yaml,
        overrides=overrides,
        max_runs=max_runs,
        limit_scenarios=limit_scenarios,
        scenario_id=scenario_id,
        dataset_item=dataset_item,
        single_run=single_run,
        flavour_name=flavour_name,
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
        print_dry_run(loaded)
        return True

    prepared = await prepare_batch(
        loaded,
        output_dir=output_dir,
        force=force,
        data_cache_dir=data_cache_dir,
        clean_stale=clean_stale,
    )
    execution = await execute_batch(
        loaded,
        prepared,
        progress=progress,
        force=force,
        strategy=strategy,
    )
    return await finalize_batch(
        loaded,
        prepared,
        execution,
        progress=progress,
        data_cache_dir=data_cache_dir,
    )


__all__ = ["run_mas_benchmark"]

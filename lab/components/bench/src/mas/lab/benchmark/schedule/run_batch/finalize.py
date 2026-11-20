#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Finalize benchmark results — CSV, metadata, post-pipeline."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.schedule.pipeline import PipelineExecutionError
from mas.lab.benchmark.schedule.run_batch.execute import ExecutionResult, close_exit_stack
from mas.lab.benchmark.schedule.run_batch.load import LoadedExperiment
from mas.lab.benchmark.schedule.run_batch.prepare import PreparedBatch

logger = logging.getLogger(__name__)


def write_results_csv(csv_path: Path, results_rows: list) -> None:
    """Write results.csv from execution rows."""
    if not results_rows:
        return
    fieldnames = [
        "run_id",
        "scenario",
        "item_id",
        "source_item_id",
        "run",
        "experiment_id",
        "session_id",
        "checkpoint_id",
        "forked_from_checkpoint",
        "group",
        "target_agents",
        "prompt",
        "status",
        "output",
        "output_length",
        "trace_path",
        "elapsed_ms",
        "error",
        "attempts",
    ]
    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results_rows)
    logger.info(f"Saved results: {csv_path}")


def write_session_mappings(output_dir: Path, results_rows: list[dict[str, Any]]) -> None:
    """Write session and checkpoint lineage for every generated run."""
    target = output_dir / "session_mappings.jsonl"
    with target.open("w", encoding="utf-8") as stream:
        for row in results_rows:
            mapping = {
                "experiment_id": row.get("experiment_id", ""),
                "scenario_id": row.get("scenario", ""),
                "item_id": row.get("source_item_id", row.get("item_id", "")),
                "run_idx": row.get("run", ""),
                "session_id": row.get("session_id", ""),
                "checkpoint_id": row.get("checkpoint_id", ""),
                "forked_from_checkpoint": row.get("forked_from_checkpoint", ""),
            }
            stream.write(json.dumps(mapping, ensure_ascii=False) + "\n")


def update_metadata(
    mas_meta: Any,
    prepared: PreparedBatch,
    loaded: LoadedExperiment,
    execution: ExecutionResult,
    *,
    success: bool,
    pipeline_error: str = "",
) -> None:
    """Update metadata.yaml with final counts and terminal status."""
    mas_meta.total_scenarios = len(prepared.loaded_ids)
    mas_meta.completed_scenarios = execution.total_ok
    mas_meta.failed_scenarios = execution.total_fail
    mas_meta.n_scenarios = len(prepared.loaded_ids)
    # Declared scenarios x items; an item's ``scenarios`` list leaves pairs out of the plan, not out of this count
    # (``show`` and ``list`` derive items per scenario from it).
    mas_meta.n_tests = len(prepared.loaded_ids) * len(prepared.dataset_items)
    mas_meta.n_runs_per_test = loaded.n_runs
    if success:
        mas_meta.mark_completed()
        mas_meta.metadata.pop("error", None)
        mas_meta.metadata.pop("pipeline_error", None)
    else:
        mas_meta.mark_failed(pipeline_error or "benchmark failed")
    if pipeline_error:
        mas_meta.metadata["pipeline_error"] = pipeline_error
    mas_meta.to_yaml(prepared.output_dir / "metadata.yaml")


def print_summary(
    loaded: LoadedExperiment,
    prepared: PreparedBatch,
    execution: ExecutionResult,
) -> None:
    """Print benchmark completion summary."""
    print()
    print("=" * 70)
    print(f"MAS BENCHMARK COMPLETE — {loaded.exp.name}")
    print("=" * 70)
    print(f"  Total  : {len(execution.results_rows)} executions")
    print(f"  OK     : {execution.total_ok}")
    print(f"  Errors : {execution.total_fail}")
    if execution.results_rows:
        print(f"  Results: {prepared.csv_path}")
    print("=" * 70)


_FATAL = (GeneratorExit, SystemExit)
_INTERRUPTS = (KeyboardInterrupt, asyncio.CancelledError)


def _note_failure(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".strip(": ")


class _Outcome:
    """What went wrong while finishing a batch. Nothing here stops the remaining cleanup."""

    def __init__(self) -> None:
        self.ok = True
        self.error = ""
        self.what = ""
        self.pending: BaseException | None = None

    def fail(self, exc: BaseException, what: str, *, text: str | None = None) -> None:
        """Record *exc*; ``GeneratorExit`` / ``SystemExit`` still propagate, an interrupt is re-raised last."""
        if isinstance(exc, _FATAL):
            raise exc
        logger.error("%s failed: %s", what, exc, exc_info=not isinstance(exc, PipelineExecutionError))
        self.ok = False
        if not self.error:
            self.what = what
            self.error = text if text is not None else _note_failure(exc)
        if isinstance(exc, _INTERRUPTS) and self.pending is None:
            self.pending = exc

    def close_services(self, stack: Any) -> None:
        """Stop the batch's services (idempotent: a closed stack does nothing)."""
        exc = close_exit_stack(stack)
        if exc is not None:
            self.fail(exc, "stopping services")


async def finalize_batch(
    loaded: LoadedExperiment,
    prepared: PreparedBatch,
    execution: ExecutionResult,
    *,
    progress: bool = True,
    data_cache_dir: Optional[Path] = None,
) -> bool:
    """Write CSV, stop services, run post-pipeline, update metadata, return success.

    Every stage runs even when an earlier one failed; the first failure names the batch's
    error, and an interrupt is re-raised once the metadata is written.
    """
    outcome = _Outcome()
    runs_ok = False
    try:
        try:
            write_results_csv(prepared.csv_path, execution.results_rows)
            write_session_mappings(prepared.output_dir, execution.results_rows)
            print_summary(loaded, prepared, execution)
        except BaseException as exc:
            outcome.fail(exc, "writing results")
        outcome.close_services(execution.exit_stack)
        # An interrupt while writing the results ends the batch: the post steps do not run over it.
        if loaded.pipeline_specs and not execution.pipeline_drained and outcome.pending is None:
            try:
                from mas.lab.benchmark.pipeline.expand import planned_nodes
                from mas.lab.benchmark.schedule.pipeline import run_pipeline_phase

                keys = list(execution.run_keys or [])
                nodes = planned_nodes(keys, prepared.output_dir) if keys else None
                await run_pipeline_phase(
                    phase="post",
                    exp=loaded.exp,
                    experiment_yaml=loaded.experiment_yaml,
                    output_dir=prepared.output_dir,
                    specs=loaded.pipeline_specs,
                    scenario_ids=prepared.loaded_ids,
                    infra_name=loaded.infra_name,
                    step_overrides=loaded.step_overrides_dict,
                    progress=progress,
                    data_cache_dir=data_cache_dir,
                    nodes=nodes,
                )
            except PipelineExecutionError as exc:
                outcome.fail(exc, "post-pipeline", text=str(exc))
            except BaseException as exc:
                outcome.fail(exc, "post-pipeline")
    finally:
        # Reached with services still running only when a fatal exception skipped the first stop.
        outcome.close_services(execution.exit_stack)
        runs_ok = execution.total_fail == 0 and bool(execution.results_rows)
        if not execution.results_rows:
            print("Benchmark failed: no run was executed (check scenarios and dataset items).")
            logger.error("Benchmark executed no runs")
        if not outcome.ok:
            print(
                f"{outcome.what} failed (executions {execution.total_ok} ok / "
                f"{execution.total_fail} error): {outcome.error}"
            )
        try:
            update_metadata(
                prepared.mas_meta,
                prepared,
                loaded,
                execution,
                success=runs_ok and outcome.ok,
                pipeline_error=outcome.error if not outcome.ok else "",
            )
        except Exception:
            logger.error("writing metadata after the batch failed", exc_info=True)
    if outcome.pending is not None:
        raise outcome.pending
    return runs_ok and outcome.ok


def abort_batch(prepared: PreparedBatch, cause: BaseException) -> list[str]:
    """Mark a batch that raised or was interrupted as failed; return the errors met doing so.

    The services are already stopped by ``execute_batch``. The ``post`` steps do not run: they
    analyse a complete batch, and over the partial output of an aborted one they would cost
    time and model calls to produce results that look finished. Nothing is raised, so the
    caller can re-raise *cause* (a ``KeyboardInterrupt`` stays one).
    """
    try:
        prepared.mas_meta.mark_failed(_note_failure(cause))
        prepared.mas_meta.to_yaml(prepared.output_dir / "metadata.yaml")
    except Exception as exc:
        logger.error("cleanup after %s failed: metadata: %s", type(cause).__name__, exc)
        return [f"metadata: {exc}"]
    return []

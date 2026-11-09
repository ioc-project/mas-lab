#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark execution worker — programmatic API (CLI-free).

This module is the single authoritative entry point for running benchmarks
in-process.  Both the CLI and the UI server delegate here; neither owns the
execution logic.

Separation of concerns
----------------------
* ``engine.py``   — routes experiments with ``application`` to MAS benchmark runner
* ``schedule/run_batch.py`` — MAS scheduler: plan runs, cache, post-pipeline
* ``engine.py`` — routes all experiments to MAS benchmark path
* ``cli/commands/benchmark.py`` — *CLI shell*: Click → controller daemon
* ``ui/server.py`` — *HTTP server*: accepts POST params, calls worker via ``_create_job``

Example (in-process call from server or tests)::

    from mas.lab.benchmark.worker import run_benchmark_sync

    success = run_benchmark_sync(
        experiment_yaml="path/to/experiment.yaml",
        flavour_name="local",
        force=True,
    )

Adding log streaming
--------------------
Pass ``log_sink`` to capture log lines emitted during the run::

    lines: list[str] = []
    success = run_benchmark_sync(..., log_sink=lines.append)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

__all__ = [
    "run_benchmark_sync",
    "run_benchmark_async",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_benchmark_sync(
    experiment_yaml: str | Path,
    *,
    overrides: Optional[list[str]] = None,
    flavour_name: str = "local",
    controls: Optional[list[str]] = None,
    force: bool = False,
    benchmark_id: Optional[str] = None,
    dry_run: bool = False,
    max_runs: Optional[int] = None,
    limit_scenarios: Optional[int] = None,
    scenario_id: Optional[str] = None,
    dataset_item: Optional[str] = None,
    sample_scenarios: Optional[int] = None,
    single_run: bool = False,
    output_dir: Optional[str | Path] = None,
    trace_cache_dir: Optional[str | Path] = None,
    data_cache_dir: Optional[str | Path] = None,
    force_lock: bool = False,
    strategy: Optional[str] = None,
    infra_name: Optional[str] = None,
    step_overrides: Optional[list] = None,
    pipeline_attachments: Optional[list] = None,
    experiment_overlays: Optional[list] = None,
    log_sink: Optional[Callable[[str], None]] = None,
    clean_stale: Optional[bool] = None,
) -> bool:
    """Execute a benchmark synchronously (blocking, no interactive progress bar).

    Safe to call from any thread that does not already own an asyncio event
    loop (e.g. from a ``threading.Thread`` started by ``_create_job``).

    Parameters
    ----------
    experiment_yaml:
        Path to the experiment YAML file.
    flavour_name:
        Runtime flavour (default ``"local"``).
    force:
        Always create a new run, ignoring completed/interrupted prior runs.
    benchmark_id:
        Resume a specific run by ID (skips auto-detect).
    dry_run:
        Validate config and show what would run without executing scenarios.
    max_runs:
        Override ``n_runs`` from the YAML.
    limit_scenarios:
        Limit to the first N scenarios.
    sample_scenarios:
        Randomly sample N scenarios.
    single_run:
        Shortcut for ``limit_scenarios=1``.
    output_dir:
        Custom output directory (default: :func:`mas.lab.paths.labs_root`).
    trace_cache_dir:
        Override trace-cache directory.
    force_lock:
        Force-break an existing run lock.
    strategy:
        Execution ordering: ``"coverage"`` (breadth-first) or ``"depth"``
        (depth-first).  Overrides YAML ``schedule.ordering`` (legacy
        ``execution.strategy`` still accepted during dual-read).
    log_sink:
        Optional callable that receives log lines (str) emitted during the
        run — useful for routing output to the UI job log buffer.

    Returns
    -------
    bool
        ``True`` on success, ``False`` on failure.
    """
    _sink_handler = _attach_log_sink(log_sink) if log_sink else None
    try:
        from mas.lab.benchmark.engine import BenchmarkRunOptions  # noqa: PLC0415

        opts = BenchmarkRunOptions(
            progress=False,
            overrides=overrides or [],
            flavour_name=flavour_name,
            controls=controls or [],
            force=force,
            benchmark_id=benchmark_id,
            dry_run=dry_run,
            max_runs=max_runs,
            limit_scenarios=limit_scenarios,
            scenario_id=scenario_id,
            dataset_item=dataset_item,
            sample_scenarios=sample_scenarios,
            single_run=single_run,
            output_dir=Path(output_dir) if output_dir else None,
            trace_cache_dir=Path(trace_cache_dir) if trace_cache_dir else None,
            data_cache_dir=Path(data_cache_dir) if data_cache_dir else None,
            force_lock=force_lock,
            strategy=strategy,
            infra_name=infra_name,
            step_overrides=step_overrides or [],
            pipeline_attachments=pipeline_attachments or [],
            experiment_overlays=experiment_overlays or [],
            clean_stale=clean_stale,
        )
        return asyncio.run(run_benchmark_async(experiment_yaml, options=opts))
    finally:
        if _sink_handler is not None:
            logging.getLogger().removeHandler(_sink_handler)


async def run_benchmark_async(
    experiment_yaml: str | Path,
    *,
    options: "BenchmarkRunOptions | None" = None,  # noqa: F821
) -> bool:
    """Async variant — use when already inside an asyncio event loop.

    Accepts a :class:`~mas.lab.benchmark.engine.BenchmarkRunOptions` directly;
    pass ``options=BenchmarkRunOptions(...)`` to override any field.
    """
    from mas.lab.benchmark.engine import BenchmarkRunOptions, run_benchmark  # noqa: PLC0415

    return await run_benchmark(
        experiment_yaml=Path(experiment_yaml),
        options=options or BenchmarkRunOptions(),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _SinkHandler(logging.Handler):
    """Logging handler that forwards records to a callable sink."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink(self.format(record))
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).debug("log sink failed", exc_info=True)


def _attach_log_sink(sink: Callable[[str], None]) -> _SinkHandler:
    """Attach a log sink to the root logger and return the handler."""
    handler = _SinkHandler(sink)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler

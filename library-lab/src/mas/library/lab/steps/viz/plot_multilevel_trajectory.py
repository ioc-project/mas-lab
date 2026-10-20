#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""PlotMultilevelTrajectoryStep — pipeline step for the multilevel trajectory diagram.

Wraps :class:`~mas.lab.plots.multilevel_trajectory_processor.MultilevelTrajectoryPlotter`
so it can be declared declaratively in a ``pipeline/v1`` YAML manifest.

Config keys::

    log_path : str
        Path to a ``events.jsonl`` trace file.
        If omitted the step resolves ``trace_path`` or ``events_path``
        from a dependency step's output data (e.g. ``extract_trajectories``).
    format : str
        Output format: ``html`` (default, interactive D3) or ``svg`` (static).
    title : str
        Diagram title (default: "MAS Multilevel Trajectory").
    width_mode : str
        Column-width strategy: ``fixed`` | ``proportional`` | ``log`` (default).
    show_user_actors : bool
        Render person-icon user nodes (default: true).
    filename : str
        Output filename without extension (default: ``multilevel_trajectory``).

Example pipeline YAML::

    - name: multilevel-item1
      type: plot_multilevel_trajectory
      depends_on: [extract-run1]
      config:
        format: html
        title: "Trip Planner — Multilevel Trajectory (item 1)"
        width_mode: log
        filename: multilevel-item1
"""

import logging
from pathlib import Path

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_named_file
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.library.lab.steps._io import pipeline_config_dir, resolve_pipeline_relative

logger = logging.getLogger(__name__)


def _discover_primary_trace(ctx) -> str | None:
    """Find a representative events.jsonl when no per-run scope is available.

    Standalone aggregate pipelines (``mas-lab benchmark pipeline run``) have no
    single "current run", so ``resolve_run_events`` can't help.  Prefer the first
    ``trace_path`` recorded in the benchmark ``results.csv``; fall back to
    scanning the output tree for a ``traces/events.jsonl``.
    """
    import csv

    base = getattr(ctx, "output_dir", None)
    if not base:
        return None
    base = Path(base)
    results = base / "results.csv"
    if results.is_file():
        try:
            for row in csv.DictReader(results.open(encoding="utf-8")):
                tp = (row.get("trace_path") or "").strip()
                if tp and Path(tp).expanduser().is_file():
                    return str(Path(tp).expanduser())
        except Exception:
            pass
    for cand in sorted(base.glob("**/traces/events.jsonl")):
        if cand.is_file():
            return str(cand)
    return None


def _load_annotations(config: dict, pipeline_dir: Path | None) -> dict | None:
    """Resolve plot annotations from config or a YAML file."""
    ann: dict = {}
    ann_path = config.get("annotations")
    if ann_path:
        import yaml

        p = Path(ann_path).expanduser()
        if not p.is_absolute() and pipeline_dir:
            p = (pipeline_dir / p).resolve()
        if p.exists():
            ann = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        else:
            logger.warning("annotations file not found: %s", p)

    for key in ("highlight_agents", "pins"):
        if config.get(key) is not None:
            ann[key] = config[key]
    return ann or None


class PlotMultilevelTrajectoryStep(PipelineStep):
    """Generate a multilevel MAS trajectory diagram from a trace."""

    type = "plot_multilevel_trajectory"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.lab.plots.trajectory import load_trace
        from mas.lab.plots.multilevel_trajectory import plot_multilevel_trajectory

        config = self.config
        fmt = str(config.get("format", "html")).lower()
        title = str(config.get("title", "MAS Multilevel Trajectory"))
        width_mode = str(config.get("width_mode", "log"))
        show_user_actors = bool(config.get("show_user_actors", True))
        show_provenance = bool(config.get("show_provenance", True))

        annotations = _load_annotations(config, pipeline_config_dir(ctx))

        from mas.lab.benchmark.pipeline.run_artifacts import (
            resolve_run_events,
            resolve_run_artifact,
        )

        log_path: str | None = config.get("log_path") or config.get("events_path")
        if not log_path:
            resolved = resolve_run_events(ctx, config)
            if resolved is not None:
                log_path = str(resolved)

        if not log_path:
            for dep_name in self.depends_on:
                dep_out = ctx.step_outputs.get(dep_name)
                if dep_out:
                    for key in ("trace_path", "events_path"):
                        if dep_out.data.get(key):
                            log_path = dep_out.data[key]
                            logger.info(
                                "Step '%s': using %s from dependency '%s': %s",
                                self.name, key, dep_name, log_path,
                            )
                            break
                if log_path:
                    break

        if not log_path:
            # Fallback for the standalone aggregate pipeline (no per-run scope and
            # no dependency trace): plot the primary run discovered under the
            # benchmark output, or the first trace_path in its results.csv.
            log_path = _discover_primary_trace(ctx)
            if log_path:
                logger.info("Step '%s': using discovered primary trace: %s",
                            self.name, log_path)

        if not log_path:
            from mas.lab.benchmark.pipeline.run_artifacts import missing_run_events_message

            raise ValueError(
                f"Step '{self.name}': no log_path in config, no 'trace_path' in "
                "dependency outputs, and no trace discoverable under the run output. "
                + missing_run_events_message(self.name, ctx)
            )

        resolved_path = resolve_pipeline_relative(ctx, log_path)

        # --- Load & render ---
        events = load_trace(resolved_path)
        logger.info("Step '%s': loaded %d events from %s", self.name, len(events), resolved_path)

        rendered = plot_multilevel_trajectory(
            events,
            fmt=fmt,
            title=title,
            width_mode=width_mode,
            show_user_actors=show_user_actors,
            show_provenance=show_provenance,
            annotations=annotations,
        )

        artifact_key = str(config.get("artifact", "trajectory_native"))
        out_file = resolve_run_artifact(ctx, artifact_key, config)
        if not (ctx.scope_context.scenario and ctx.scope_context.test and ctx.scope_context.run):
            ext = ".html" if fmt == "html" else ".svg"
            out_file = resolve_step_named_file(
                ctx,
                self.name,
                config,
                "trajectory-native",
                default_subdir="trajectories",
                suffix=ext,
                mkdir=False,
            )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(rendered, encoding="utf-8")

        logger.info(
            "Step '%s': wrote %s (%d events, %d chars)",
            self.name, out_file, len(events), len(rendered),
        )

        return StepOutput(
            data={
                "trajectory_diagram": rendered,
                "format": fmt,
                "trajectory_path": str(out_file),
            },
            files=[out_file],
            metadata={
                "format": fmt,
                "events": len(events),
                "output_file": str(out_file),
                "title": title,
            },
        )

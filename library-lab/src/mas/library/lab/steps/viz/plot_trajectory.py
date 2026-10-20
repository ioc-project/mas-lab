#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""PlotTrajectoryStep — pipeline step that renders an agent trajectory diagram.

Config keys::

    log_path: str      Path to a .jsonl trace file OR a run_id string.
                       If omitted, the step looks for trace files produced by
                       a dependency step (key "trace_path" in StepOutput.data).
    format: str        Output format: mermaid (default), table, html.
    include_prompts: bool  Show full task text on diagram edges (default: true).
    filename: str      Output filename without extension (default: "trajectory").

Example pipeline YAML::

    - name: traj
      type: plot_trajectory
      depends_on: [run]
      config:
        format: html
        include_prompts: true
"""

import logging

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_named_file
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.library.lab.steps._io import path_from_config_or_deps

logger = logging.getLogger(__name__)


class PlotTrajectoryStep(PipelineStep):
    """Generate an agent trajectory diagram from a MAS trace."""

    type = "plot_trajectory"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        """Render trajectory diagram.

        Config:
            log_path (str): Path or run_id. Falls back to ``trace_path`` in
                dependency output.
            format (str): mermaid | table | html  (default: mermaid)
            include_prompts (bool): Show task text on edges (default: true)
            filename (str): Base name for output file (default: trajectory)
        """
        from mas.lab.plots.trajectory import load_trace, plot_trajectory

        config = self.config
        fmt = config.get("format", "mermaid").lower()
        include_prompts = config.get("include_prompts", True)
        highlight_agents   = config.get("highlight_agents")  or None
        highlight_keywords = config.get("highlight_keywords") or None
        resolved_path = path_from_config_or_deps(
            self,
            ctx,
            config,
            ("log_path",),
            ("kg_path", "normalized_trace_path", "trace_path"),
        )

        events = load_trace(resolved_path)
        logger.info("Step '%s': loaded %d events from %s", self.name, len(events), resolved_path)

        diagram = plot_trajectory(
            events,
            fmt=fmt,
            include_prompts=include_prompts,
            highlight_agents=highlight_agents,
            highlight_keywords=highlight_keywords,
        )

        # --- Write output file ---
        ext = {"mermaid": ".mmd", "table": ".txt", "html": ".html", "svg": ".svg"}.get(fmt, ".txt")
        out_file = resolve_step_named_file(
            ctx, self.name, config, "trajectory", default_subdir="trajectories", suffix=ext
        )
        out_file.write_text(diagram, encoding="utf-8")

        logger.info("Step '%s': wrote %s (%d events, %d chars)",
                    self.name, out_file, len(events), len(diagram))

        return StepOutput(
            data={"trajectory_diagram": diagram, "format": fmt},
            files=[out_file],
            metadata={
                "format": fmt,
                "events": len(events),
                "output_file": str(out_file),
            },
        )

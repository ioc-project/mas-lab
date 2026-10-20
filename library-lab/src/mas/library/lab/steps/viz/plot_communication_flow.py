#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""PlotCommunicationFlowStep — pipeline step for the communication-flow diagram.

Wraps :func:`~mas.lab.plots.communication_flow.plot_communication_flow`
so it can be declared declaratively in a ``pipeline/v1`` YAML manifest.

Config keys::

    log_path : str
        Path to a ``events.jsonl`` trace file.
        If omitted the step resolves ``kg_path`` or ``trace_path``
        from a dependency step's output data.
    format : str
        Output format: ``html`` (default, interactive D3) or ``mermaid``.
    title : str
        Diagram title (default: "MAS Agent Communication Flow").
    filename : str
        Output filename without extension (default: ``communication_flow``).

Example pipeline YAML::

    - name: flow-item1
      type: plot_communication_flow
      depends_on: [normalize-item1]
      config:
        format: html
        title: "Trip Planner — Agent Communication Flow (item 1)"
        filename: flow-item1
"""

import logging

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_named_file
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.library.lab.steps._io import path_from_config_or_deps

logger = logging.getLogger(__name__)


class PlotCommunicationFlowStep(PipelineStep):
    """Generate an agent communication-flow diagram from a trace."""

    type = "plot_communication_flow"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.lab.plots.trajectory import load_trace
        from mas.lab.plots.communication_flow import plot_communication_flow

        config = self.config
        fmt = str(config.get("format", "html")).lower()
        title = str(config.get("title", "MAS Agent Communication Flow"))
        resolved_path = path_from_config_or_deps(
            self,
            ctx,
            config,
            ("log_path",),
            ("kg_path", "normalized_trace_path", "trace_path"),
        )

        events = load_trace(resolved_path)
        logger.info("Step '%s': loaded %d events from %s", self.name, len(events), resolved_path)

        rendered = plot_communication_flow(events, fmt=fmt, title=title)

        ext = ".html" if fmt == "html" else ".mermaid"
        out_file = resolve_step_named_file(
            ctx, self.name, config, "communication_flow", default_subdir="flows", suffix=ext
        )
        filename = out_file.stem
        out_file.write_text(rendered, encoding="utf-8")

        logger.info("Step '%s': wrote %s (%d chars)", self.name, out_file, len(rendered))

        return StepOutput(
            data={"communication_flow": rendered, "format": fmt, "filename": filename},
            files=[out_file],
            metadata={
                "format": fmt,
                "events": len(events),
                "output_file": str(out_file),
                "title": title,
            },
        )

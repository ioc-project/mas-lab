#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""PlotMessageGraphStep — pipeline step that renders a whole-run message-flow diagram.

Generates a swimlane SVG or HTML (``message-graph.svg`` / ``.html``) from a
``kg.json`` trace, showing agent lanes, iteration bands, delegation / return
edges, and tool-call indicators.

Config keys::

    kg_path   : str   Path to kg.json.  If omitted, resolved from a dependency
                      step that exposes a ``kg_path`` key in its output.
    format    : str   ``svg`` (default) or ``html``.
    filename  : str   Output filename without extension (default: "message-graph").
    title     : str   Diagram title (default: run_id from kg metadata).

Example pipeline YAML::

    - name: normalize
      type: normalize_events
      config:
        log_path: "<trace_cache>/<hash>/traces/events.jsonl"

    - name: message-graph
      type: plot_message_graph
      depends_on: [normalize]
      config:
        format: html
        filename: message-graph
        title: "C4 Baseline — Message Graph"
"""

import logging

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_named_file
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.library.lab.steps._io import path_from_config_or_deps

logger = logging.getLogger(__name__)


class PlotMessageGraphStep(PipelineStep):
    """Render a whole-run agent message-flow swimlane diagram from kg.json."""

    type = "plot_message_graph"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        """Render the message-graph SVG.

        Config:
            kg_path  (str): Path to kg.json or a run_id. Falls back to
                ``kg_path`` in dependency output.
            filename (str): Base name for the output file (default: message-graph).
            title    (str): Diagram title (default: run_id from kg metadata).
        """
        from mas.lab.plots.kg_adapter import load_kg
        from mas.lab.plots.message_graph import plot_message_graph

        config   = self.config
        fmt      = config.get("format", "svg").lower()
        title    = config.get("title", "")
        resolved_path = path_from_config_or_deps(
            self, ctx, config, ("kg_path",), ("kg_path",)
        )

        # ── load & render ────────────────────────────────────────────────
        kg = load_kg(resolved_path)
        logger.info(
            "Step '%s': loaded kg.json from %s (%d nodes, %d edges)",
            self.name,
            resolved_path,
            len(kg.get("nodes", [])),
            len(kg.get("edges", [])),
        )

        if not title:
            title = kg.get("run_id") or kg.get("meta", {}).get("run_id") or ""

        content = plot_message_graph(kg, title=title, fmt=fmt)

        # ── write output ─────────────────────────────────────────────────
        ext = ".html" if fmt == "html" else ".svg"
        out_file = resolve_step_named_file(
            ctx, self.name, config, "message-graph", default_subdir="plots", suffix=ext
        )
        out_file.write_text(content, encoding="utf-8")

        logger.info(
            "Step '%s': wrote %s (%d chars)",
            self.name, out_file, len(content),
        )

        return StepOutput(
            data={"message_graph": str(out_file), "format": fmt},
            files=[out_file],
        )

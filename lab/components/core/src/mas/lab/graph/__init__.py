#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""Processor: multilevel trajectory KG plotter.

Registered as ``multilevel_trajectory_kg_plotter`` — the KG-backed
counterpart of ``multilevel_trajectory_plotter``
(mas.lab.processors.multilevel_trajectory_plotter). Renders the same
Session → MAS → Agent → Call → Thinking swim-lane diagram, but reads
already-collapsed call records from a Knowledge Graph (``kg.json``) instead
of raw ``events.jsonl`` start/end pairs.

Config / kwargs
---------------
format : str
    ``"html"`` (default) or ``"svg"``.
output : Path, optional
    Destination file path.
title : str, optional
    Diagram title.
"""

from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from mas.lab.artifacts import KnowledgeGraph, PlotFile  # noqa: E402
from mas.lab.processor import Processor, register  # noqa: E402

_FORMATS = ("html", "svg")


@register
class MultilevelTrajectoryKGPlotter(Processor):
    """Render a KnowledgeGraph into a multilevel swim-lane PlotFile.

    KG-backed counterpart of ``MultilevelTrajectoryPlotter`` — same
    rendering (stacked Session/MAS/Agent/Call/Thinking swim lanes, shared
    state circles, colored transitions, JS hover tooltips), sourced from
    ``kg.json`` call records instead of raw event start/end pairs.

    Config / kwargs
    ---------------
    format : str
        ``"html"`` (default) or ``"svg"``.
    output : Path, optional
        Destination file path.
    title : str, optional
        Diagram title (default: «MAS Multilevel Trajectory»).
    """

    name        = "multilevel_trajectory_kg_plotter"
    input_kind  = "knowledge_graph"
    output_kind = "plot_file"
    description = "KnowledgeGraph → multilevel swim-lane PlotFile (Session/MAS/Agent/Call)"
    priority    = 5   # same tier as the native (events.jsonl) plotter

    def process(
        self,
        artifact: KnowledgeGraph,
        format: str = "html",
        output: "Path | str | None" = None,
        title: str = "MAS Multilevel Trajectory",
        **kwargs: Any,
    ) -> PlotFile:
        from mas.lab.plots.multilevel_trajectory.plot import plot_multilevel_trajectory_from_kg

        fmt = format.lower()
        source = artifact.path if artifact.path else artifact.data
        content = plot_multilevel_trajectory_from_kg(source, fmt=fmt, title=title)

        ext = fmt
        if output is None and artifact.path:
            run_id = getattr(artifact, "run_id", None) or artifact.path.stem
            output = artifact.path.parent / f"{run_id}_multilevel_kg.{ext}"

        if output is not None:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
            return PlotFile(path=out_path, format=ext)

        return PlotFile(data=content, format=ext)

    def cli_options(self):
        return [
            {
                "param_decls": ["--format", "-f"],
                "type": "choice",
                "choices": _FORMATS,
                "default": "html",
                "show_default": True,
                "help": "Output format.",
            },
            {
                "param_decls": ["--title"],
                "default": "MAS Multilevel Trajectory",
                "help": "Diagram title.",
            },
        ]

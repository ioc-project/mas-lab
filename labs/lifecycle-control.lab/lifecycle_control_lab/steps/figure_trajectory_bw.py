#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline figure step (migrated from lab script)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

from ._helpers import execute_trace_figure

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

def render(output_path: Path, trace_baseline_path: Path, trace_guardrail_path: Path) -> None:
    import pathlib, re, inspect, math

    # ---------------------------------------------------------------------------
    # B&W colour map — dark hex → print-ready greyscale
    # ---------------------------------------------------------------------------
    BW: dict[str, str] = {
        # Backgrounds
        "#0f172a": "#ffffff",  # main bg        → white
        "#1e293b": "#f4f4f4",  # lane bg even   → near-white
        "#172032": "#eaeaea",  # lane bg odd    → light grey
        "#2d0000": "#f4f4f4",  # denied state   → light grey
        # Text / structural
        "#94a3b8": "#444444",  # lane labels    → dark grey
        "#f1f5f9": "#000000",  # titles         → black
        "#334155": "#aaaaaa",  # separators     → grey
        "#475569": "#555555",  # state borders  → medium grey
        "#64748b": "#777777",  # arrows/proc    → medium grey
        "#4c9be8": "#777777",
        # Level accents (session/mas/agent all same grey strip)
        "#0d3b4a": "#888888",
        "#155e75": "#888888",
        "#0e7490": "#888888",
        # Call-type colours → distinct greyscale
        "#ea580c": "#111111",  # LLM  → near-black (most important)
        "#16a34a": "#555555",  # Tool → dark grey
        "#2563eb": "#777777",  # Memory
        "#7c3aed": "#999999",  # RAG
        "#0284c7": "#666666",  # HITL
        "#dc2626": "#222222",  # denied/MITM   → near-black (distinct)
        "#ef4444": "#333333",  # denied stroke
        "#4c1d95": "#bbbbbb",  # thinking
        # Badge / state nodes
        "#f87171": "#000000",  # badge fill → black
        "#eab308": "#999999",  # user actor → grey
        "#ffffff": "#ffffff",  # keep white
    }

    def bw(svg: str) -> str:
        """Replace hex colour values in SVG with B&W equivalents."""
        def _sub(m: re.Match) -> str:
            return BW.get(m.group(0).lower(), m.group(0))
        return re.sub(r"#[0-9a-fA-F]{6}", _sub, svg)

    # ---------------------------------------------------------------------------
    # Patch the multilevel_trajectory renderer
    # ---------------------------------------------------------------------------
    import mas.lab.plots.multilevel_trajectory as M  # noqa: E402

    # 1. Compact x-positions: reduce column width so figure fits on paper
    _orig_xpos = M._compute_x_positions
    src = inspect.getsource(_orig_xpos)
    src = src.replace(
        "normal_total = max(1400.0, n_normal * 160.0) if n_normal else 0.0",
        "normal_total = max(350.0,  n_normal *  50.0) if n_normal else 0.0",
    )
    src = src.replace("_INSTANT_COL_W = 130.0", "_INSTANT_COL_W = 55.0")
    _ns: dict = {"math": math}
    exec(compile(src, "<patched_xpos>", "exec"), _ns)
    M._compute_x_positions = _ns["_compute_x_positions"]

    # 2. Compact layout constants (same proportions, smaller)
    M._LABEL_W  = 72
    M._PAD_L    = 40
    M._PAD_R    = 40
    M._LANE_H   = 52
    M._LANE_MID = 26
    M._TITLE_H  = 36
    M._LEGEND_H = 26
    M._PAD_BOT  = 12
    M._TRANS_H  = 20
    M._STATE_H  = 20
    M._STATE_W  = 26
    M._BADGE_R  = 8

    from mas.lab.plots.multilevel_trajectory import (  # noqa: E402
        plot_multilevel_trajectory,
        load_trace,
    )

    def gen_panel(trace_path: pathlib.Path, title: str) -> str:
        """Generate one B&W SVG panel from a trace file."""
        trace = load_trace(trace_path)
        svg = plot_multilevel_trajectory(
            trace, fmt="svg", title=title,
            width_mode="fixed", show_user_actors=False,
        )
        svg = bw(svg)
        # Reduce font size
        svg = re.sub(r'font-size="1[0-9]"', 'font-size="9"', svg)
        return svg

    def parse_svg(svg: str) -> tuple[float, float, str]:
        """Return (width, height, inner_content)."""
        m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
        assert m, "No viewBox found"
        w, h = float(m.group(1)), float(m.group(2))
        inner = re.sub(r"<svg[^>]+>", "", svg).replace("</svg>", "").strip()
        return w, h, inner

    def combine(inner_b: str, wb: float, hb: float,
                inner_g: str, wg: float, hg: float) -> str:
        """Place two panels side-by-side in a single SVG."""
        TARGET_W = 470.0
        GAP      = 18.0
        TITLE_H  = 18.0

        sb = TARGET_W / wb
        sg = TARGET_W / wg
        ph = max(hb * sb, hg * sg)

        total_w = TARGET_W * 2 + GAP
        total_h = ph + TITLE_H

        dx = TARGET_W + GAP / 2

        def panel_group(inner: str, tx: float, scale: float, title: str) -> str:
            mx = tx + TARGET_W / 2
            parts = []
            parts.append(
                '<text'
                f' x="{mx:.0f}" y="{TITLE_H - 4:.0f}"'
                ' text-anchor="middle" font-size="10" font-weight="bold"'
                ' fill="#000000"'
                ' font-family="ui-monospace,SFMono-Regular,Consolas,monospace">'
                f"{title}</text>"
            )
            parts.append(
                f'<g transform="translate({tx:.1f} {TITLE_H:.1f})'
                f' scale({scale:.6f} {scale:.6f})">'
            )
            parts.append(inner)
            parts.append("</g>")
            return "\n".join(parts)

        lines = []
        lines.append(
            f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' width="{total_w:.0f}" height="{total_h:.0f}"'
            f' viewBox="0 0 {total_w:.0f} {total_h:.0f}">'
        )
        lines.append(
            f'<rect width="{total_w:.0f}" height="{total_h:.0f}" fill="#ffffff"/>'
        )
        lines.append(panel_group(inner_b, 0.0, sb, "Baseline"))
        lines.append(
            f'<line x1="{dx:.0f}" y1="{TITLE_H:.0f}"'
            f' x2="{dx:.0f}" y2="{total_h:.0f}"'
            ' stroke="#cccccc" stroke-width="0.8" stroke-dasharray="4,3"/>'
        )
        lines.append(panel_group(inner_g, TARGET_W + GAP, sg, "+Guardrail"))
        lines.append("</svg>")
        return "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)

class FigureTrajectoryBwStep(PipelineStep):
    type = "figure_trajectory_bw"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_trace_figure(ctx, self, render)

register_step_type("figure_trajectory_bw", FigureTrajectoryBwStep)

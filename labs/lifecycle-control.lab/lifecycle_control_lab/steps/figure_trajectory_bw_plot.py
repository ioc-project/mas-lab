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
    BW = {
        "#0f172a": "#ffffff", "#1e293b": "#f5f5f5", "#172032": "#ebebeb",
        "#2d0000": "#f5f5f5", "#94a3b8": "#333333", "#f1f5f9": "#000000",
        "#334155": "#aaaaaa", "#475569": "#555555", "#64748b": "#777777",
        "#0d3b4a": "#888888", "#155e75": "#888888", "#0e7490": "#888888",
        "#ea580c": "#111111", "#16a34a": "#555555", "#2563eb": "#777777",
        "#7c3aed": "#999999", "#dc2626": "#222222", "#ef4444": "#333333",
        "#4c1d95": "#aaaaaa", "#f87171": "#000000", "#eab308": "#999999",
        "#0284c7": "#666666", "#4c9be8": "#777777", "#ffffff": "#ffffff",
    }
    def bw(svg):
        def _sub(m): return BW.get(m.group(0).lower(), m.group(0))
        return re.sub("#[0-9a-fA-F]{6}", _sub, svg)
    import mas.lab.plots.multilevel_trajectory as M
    _orig = M._compute_x_positions
    src = inspect.getsource(_orig)
    src = src.replace(
        "normal_total = max(1400.0, n_normal * 160.0) if n_normal else 0.0",
        "normal_total = max(350.0, n_normal * 50.0)  if n_normal else 0.0")
    src = src.replace("_INSTANT_COL_W = 130.0", "_INSTANT_COL_W = 55.0")
    _ns = {"math": math}
    exec(compile(src, "<p>", "exec"), _ns)
    M._compute_x_positions = _ns["_compute_x_positions"]
    M._LABEL_W=72; M._PAD_L=40; M._PAD_R=40
    M._LANE_H=52; M._LANE_MID=26; M._TITLE_H=36; M._LEGEND_H=26
    M._PAD_BOT=12; M._TRANS_H=20; M._STATE_H=20; M._STATE_W=26; M._BADGE_R=8
    from mas.lab.plots.multilevel_trajectory import plot_multilevel_trajectory, load_trace
    def gen(p, title):
        t = load_trace(p)
        s = plot_multilevel_trajectory(t, fmt="svg", title=title, width_mode="fixed", show_user_actors=False)
        s = bw(s)
        s = re.sub("font-size=\"1[0-9]\"", "font-size=\"9\"", s)
        return s
    print("baseline..."); svg_b = gen(trace_baseline_path, "Baseline")
    print("+guardrail..."); svg_g = gen(trace_guardrail_path, "+Guardrail")
    def parse(svg):
        m = re.search("viewBox=.0 0 ([0-9.]+) ([0-9.]+)", svg)
        w,h = float(m.group(1)), float(m.group(2))
        inner = re.sub("<svg[^>]+>","",svg).replace("</svg>","").strip()
        return w,h,inner
    wb,hb,ib = parse(svg_b); wg,hg,ig = parse(svg_g)
    TW=460.0; GAP=16.0; TH=18.0
    sb=TW/wb; sg=TW/wg
    ph=max(hb*sb,hg*sg); totw=TW*2+GAP; toth=ph+TH
    def wrap(inner, tx, ty, sc, title):
        mx = tx + TW / 2
        return (
            f'<text x="{mx:.0f}" y="{ty + TH - 4:.0f}" text-anchor="middle" '
            f'font-size="10" font-weight="bold" fill="#000000" '
            f'font-family="ui-monospace,SFMono-Regular,Consolas,monospace">{title}</text>\n'
            f'<g transform="translate({tx:.1f} {ty + TH:.1f}) scale({sc:.6f} {sc:.6f})">\n'
            f"{inner}\n"
            f"</g>"
        )

    dx = TW + GAP / 2
    result = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{totw:.0f}" height="{toth:.0f}" '
        f'viewBox="0 0 {totw:.0f} {toth:.0f}">',
        f'<rect width="{totw:.0f}" height="{toth:.0f}" fill="#ffffff"/>',
        wrap(ib, 0.0, 0.0, sb, "Baseline"),
        f'<line x1="{dx:.0f}" y1="{TH}" x2="{dx:.0f}" y2="{toth:.0f}" '
        f'stroke="#cccccc" stroke-width="0.8" stroke-dasharray="4,3"/>',
        wrap(ig, TW + GAP, 0.0, sg, "+Guardrail"),
        "</svg>",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(result), encoding="utf-8")
    print(f"Written {output_path}  ({totw:.0f}x{toth:.0f}px)")
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF

        d = svg2rlg(str(output_path))
        if d:
            renderPDF.drawToFile(d, str(output_path.with_suffix(".pdf")))
            print("PDF written")
    except Exception as e:
        print("PDF:", e)

class FigureTrajectoryBwPlotStep(PipelineStep):
    type = "figure_trajectory_bw_plot"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_trace_figure(ctx, self, render)

register_step_type("figure_trajectory_bw_plot", FigureTrajectoryBwPlotStep)

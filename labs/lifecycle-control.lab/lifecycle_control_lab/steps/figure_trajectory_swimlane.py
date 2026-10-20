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
    import json
    import os
    import pathlib
    import re

    # ---------------------------------------------------------------------------
    # Trace paths
    # ---------------------------------------------------------------------------

    AGENTS = ["moderator", "itinerary_agent", "schedule_agent", "concierge_agent"]
    AGENT_LABEL = {
        "moderator":       "moderator",
        "itinerary_agent": "itinerary",
        "schedule_agent":  "schedule",
        "concierge_agent": "concierge",
    }
    TOOL_ABBREV = {
        "query_graph_database": "qdb",
        "lookup_schedule":      "sch",
        "get_fares":            "far",
    }

    # ---------------------------------------------------------------------------
    # Layout
    # ---------------------------------------------------------------------------
    COL_W   = 10.0    # width of one call column (pt)
    BAR_GAP = 1.5     # horizontal gap between adjacent bars
    BAR_W   = COL_W - BAR_GAP

    LANE_H  = 22.0    # height of one agent lane
    TITLE_H = 10.0    # top title strip
    LEGEND_H = 13.0   # bottom legend strip

    N_INIT     = 3    # moderator initial cols (proc, llm, llm)
    N_PARALLEL = 13   # parallel agent work (max across all agents)
    N_SYNTH    = 3    # moderator synthesis cols (proc, llm, llm)
    N_COLS     = N_INIT + N_PARALLEL + N_SYNTH  # 19

    LABEL_W = 52.0
    PAD_R   = 5.0

    PANEL_W = LABEL_W + N_COLS * COL_W + PAD_R   # ≈ 247pt
    PANEL_H = TITLE_H + len(AGENTS) * LANE_H + LEGEND_H

    # Call-bar heights (centred on track, pt)
    BAR_H = {"llm": 12.0, "tool": 8.5, "proc": 3.5, "blocked": 12.0}

    # B&W colours
    COL_BG_EVEN = "#f6f6f6"
    COL_BG_ODD  = "#eeeeee"
    COL_TRACK   = "#bbbbbb"   # thin baseline
    COL_LABEL   = "#222222"
    COL_LLM     = "#141414"   # near-black fill
    COL_PROC    = "#d0d0d0"
    COL_TOOL    = "#555555"
    COL_BLOCKED_FILL   = "#ffffff"
    COL_BLOCKED_STROKE = "#222222"
    COL_DELEG   = "#888888"   # delegation arrows
    COL_SEP     = "#aaaaaa"

    FONT = "ui-monospace,SFMono-Regular,Consolas,Liberation Mono,monospace"

    # ---------------------------------------------------------------------------
    # Trace extraction
    # ---------------------------------------------------------------------------

    def _load(path: pathlib.Path) -> list[dict]:
        with open(path) as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def extract_calls(events: list[dict]) -> dict[str, list[dict]]:
        """Return per-agent ordered call list from raw events.

        Each entry: {type, tool, abbrev}
        """
        CALL_KINDS = {
            "processing_call_start": "proc",
            "llm_call_start":        "llm",
            "tool_call_start":       "tool",
            "governance_denied":     "blocked",
        }

        events = sorted(events, key=lambda e: e.get("timestamp", 0))
        per_agent: dict[str, list[dict]] = {a: [] for a in AGENTS}
        saw_policy = False
        blocked_candidate: str | None = None

        for e in events:
            kind     = e.get("kind", "")
            agent_id = e.get("agent_id", "")

            if kind == "governance_policy" and e.get("action_taken") == "block":
                saw_policy = True
                blocked_candidate = None

            if saw_policy and kind == "audit" and agent_id in per_agent and blocked_candidate is None:
                blocked_candidate = agent_id

            ctype = CALL_KINDS.get(kind)
            if ctype is None:
                continue

            if ctype == "blocked":
                tgt = blocked_candidate if blocked_candidate in per_agent else None
                if tgt:
                    per_agent[tgt].append({"type": "blocked", "tool": None, "abbrev": "✕"})
                saw_policy = False
                blocked_candidate = None
                continue

            if agent_id not in per_agent:
                continue

            tool   = e.get("tool_name")
            abbrev = TOOL_ABBREV.get(tool or "", tool[:3] if tool else "")
            per_agent[agent_id].append({"type": ctype, "tool": tool, "abbrev": abbrev})

        return per_agent

    # ---------------------------------------------------------------------------
    # Geometry helpers
    # ---------------------------------------------------------------------------

    def col_cx(col: int) -> float:
        """SVG x-centre of column col (0-indexed from left of timeline)."""
        return LABEL_W + col * COL_W + COL_W / 2

    def bar_x(col: int) -> float:
        return LABEL_W + col * COL_W + BAR_GAP / 2

    def lane_top(lane_idx: int) -> float:
        return TITLE_H + lane_idx * LANE_H

    def lane_cy(lane_idx: int) -> float:
        return lane_top(lane_idx) + LANE_H / 2

    def call_col(agent_id: str, call_idx: int) -> int:
        """Map a call index to a global column number."""
        if agent_id == "moderator":
            if call_idx < N_INIT:
                return call_idx
            return N_INIT + N_PARALLEL + (call_idx - N_INIT)
        return N_INIT + call_idx

    # ---------------------------------------------------------------------------
    # SVG primitives
    # ---------------------------------------------------------------------------

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def rect(x: float, y: float, w: float, h: float, fill: str,
             stroke: str = "none", sw: float = 0.6, rx: float = 1.0,
             dash: str = "") -> str:
        stroke_attr = (
            f' stroke="{stroke}" stroke-width="{sw:.1f}"'
            + (f' stroke-dasharray="{dash}"' if dash else "")
            if stroke != "none" else ""
        )
        return (
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"'
            f' fill="{fill}" rx="{rx:.1f}"{stroke_attr}/>'
        )

    def text(x: float, y: float, s: str, size: float, fill: str,
             anchor: str = "middle", weight: str = "normal",
             font: str = FONT) -> str:
        return (
            f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}"'
            f' font-size="{size:.1f}" font-weight="{weight}"'
            f' fill="{fill}" font-family="{font}">{_esc(s)}</text>'
        )

    def line(x1: float, y1: float, x2: float, y2: float, stroke: str,
             sw: float = 0.5, dash: str = "") -> str:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return (
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"{dash_attr}/>'
        )

    def arrow_down(x: float, y1: float, y2: float,
                   stroke: str = COL_DELEG, sw: float = 0.7) -> str:
        """Vertical arrow from (x, y1) down to (x, y2) with arrowhead."""
        ah = 3.0  # arrowhead half-width
        return (
            f'<line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{y2 - ah:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"/>'
            f'<polygon points="{x:.2f},{y2:.2f} {x-ah:.2f},{y2-ah*1.6:.2f} {x+ah:.2f},{y2-ah*1.6:.2f}"'
            f' fill="{stroke}"/>'
        )

    # ---------------------------------------------------------------------------
    # Panel renderer
    # ---------------------------------------------------------------------------

    def render_panel(calls_by_agent: dict[str, list[dict]], title: str) -> str:
        parts: list[str] = []
        W, H = PANEL_W, PANEL_H

        # Background
        parts.append(rect(0, 0, W, H, "#ffffff"))

        # Title
        parts.append(text(W / 2, TITLE_H - 2.5, title, 7.5, COL_LABEL, weight="bold"))

        for li, agent_id in enumerate(AGENTS):
            calls   = calls_by_agent.get(agent_id, [])
            top     = lane_top(li)
            cy      = lane_cy(li)
            bg      = COL_BG_EVEN if li % 2 == 0 else COL_BG_ODD

            # Lane background
            parts.append(rect(0, top, W, LANE_H, bg))

            # Lane separator (top edge)
            if li > 0:
                parts.append(line(0, top, W, top, COL_SEP, sw=0.3))

            # Left accent bar (level indicator)
            parts.append(rect(0, top + 1, 2.0, LANE_H - 2, COL_LABEL, rx=0.5))

            # Agent label (right-aligned in label column)
            lbl = AGENT_LABEL.get(agent_id, agent_id[:8])
            parts.append(text(LABEL_W - 3, cy + 1.8, lbl, 5.0, COL_LABEL, anchor="end"))

            # Draw horizontal track (thin line, full timeline width)
            tx0 = LABEL_W
            tx1 = LABEL_W + N_COLS * COL_W
            if agent_id == "moderator":
                # Dashed idle section during parallel phase
                x_deleg = LABEL_W + N_INIT * COL_W
                x_rtn   = LABEL_W + (N_INIT + N_PARALLEL) * COL_W
                parts.append(line(tx0, cy, x_deleg, cy, COL_TRACK, sw=0.5))
                parts.append(line(x_deleg, cy, x_rtn, cy, COL_TRACK, sw=0.4, dash="2,2"))
                parts.append(line(x_rtn, cy, tx1, cy, COL_TRACK, sw=0.5))
            else:
                # Agents: track from delegation point onward
                x_start = LABEL_W + N_INIT * COL_W
                n       = len(calls)
                x_end   = LABEL_W + (N_INIT + n) * COL_W
                parts.append(line(x_start, cy, x_end, cy, COL_TRACK, sw=0.5))

            # Draw call bars
            for ci, call in enumerate(calls):
                col     = call_col(agent_id, ci)
                ctype   = call["type"]
                bh      = BAR_H[ctype]
                bx      = bar_x(col)
                by      = cy - bh / 2
                abbrev  = call.get("abbrev", "")

                if ctype == "llm":
                    parts.append(rect(bx, by, BAR_W, bh, COL_LLM, rx=1.2))
                    # "L" label in white inside bar
                    parts.append(text(bx + BAR_W / 2, by + bh / 2 + 1.8, "L",
                                       4.0, "#ffffff"))

                elif ctype == "proc":
                    parts.append(rect(bx, by, BAR_W, bh, COL_PROC, rx=0.6))

                elif ctype == "tool":
                    parts.append(rect(bx, by, BAR_W, bh, COL_TOOL, rx=1.0))
                    # Abbreviated label inside bar
                    if abbrev:
                        parts.append(text(bx + BAR_W / 2, by + bh / 2 + 1.8,
                                           abbrev, 4.0, "#ffffff"))
                    # Small underline arrow to show it's a call-out
                    arr_y = by + bh + 1.0
                    ax    = bx + BAR_W / 2
                    parts.append(
                        f'<line x1="{ax:.2f}" y1="{arr_y:.2f}" x2="{ax:.2f}"'
                        f' y2="{arr_y + 2.5:.2f}" stroke="{COL_TOOL}" stroke-width="0.5"/>'
                    )

                elif ctype == "blocked":
                    parts.append(rect(bx, by, BAR_W, bh, COL_BLOCKED_FILL,
                                       stroke=COL_BLOCKED_STROKE, dash="2,1.5", rx=1.2))
                    parts.append(text(bx + BAR_W / 2, by + bh / 2 + 1.8, "✕",
                                       5.0, COL_BLOCKED_STROKE))

        # ---------------------------------------------------------------------------
        # Delegation arrows: moderator → each specialist at x = delegation point
        # ---------------------------------------------------------------------------
        x_deleg = LABEL_W + N_INIT * COL_W  # = col 3 left edge
        xd = x_deleg - COL_W * 0.15         # slightly left of delegation column

        mod_cy = lane_cy(0)
        for li in range(1, len(AGENTS)):
            target_cy = lane_cy(li) - BAR_H["llm"] / 2 - 0.5  # just above agent bar
            parts.append(arrow_down(xd - (li - 1) * 1.8, mod_cy + BAR_H["llm"] / 2 + 0.5,
                                     target_cy))

        # Return arrow: from end of longest agent track (col N_INIT + N_PARALLEL) back up
        x_rtn = LABEL_W + (N_INIT + N_PARALLEL) * COL_W + COL_W * 0.15
        # Upward arrow from each agent that completes (to moderator) — single combined arrow
        # Use longest active agent as the return signal
        last_agent_cy = lane_cy(len(AGENTS) - 1)  # concierge (last, longest)
        mod_arrow_y   = mod_cy + BAR_H["llm"] / 2 + 0.5
        ah = 3.0
        parts.append(
            f'<line x1="{x_rtn:.2f}" y1="{last_agent_cy:.2f}" x2="{x_rtn:.2f}"'
            f' y2="{mod_arrow_y + ah:.2f}" stroke="{COL_DELEG}" stroke-width="0.7"/>'
            f'<polygon points="{x_rtn:.2f},{mod_arrow_y:.2f}'
            f' {x_rtn - ah:.2f},{mod_arrow_y + ah * 1.6:.2f}'
            f' {x_rtn + ah:.2f},{mod_arrow_y + ah * 1.6:.2f}"'
            f' fill="{COL_DELEG}"/>'
        )

        # ---------------------------------------------------------------------------
        # Phase separators (light vertical dashed lines)
        # ---------------------------------------------------------------------------
        for cx in [LABEL_W + N_INIT * COL_W, LABEL_W + (N_INIT + N_PARALLEL) * COL_W]:
            parts.append(line(cx, TITLE_H, cx, PANEL_H - LEGEND_H, COL_SEP, sw=0.4, dash="3,2"))

        # ---------------------------------------------------------------------------
        # Legend
        # ---------------------------------------------------------------------------
        ly    = PANEL_H - LEGEND_H + 4
        items = [
            ("llm",     COL_LLM,   "none", "",     "LLM call"),
            ("tool",    COL_TOOL,  "none", "",     "Tool call"),
            ("proc",    COL_PROC,  "none", "",     "Ctx assembly"),
            ("blocked", "#ffffff", COL_BLOCKED_STROKE, "2,1.5", "Blocked"),
        ]
        lx = LABEL_W + 2
        for ctype, fill, stroke, dash, lbl in items:
            bh = BAR_H[ctype]
            by = ly + (LEGEND_H - 8 - bh) / 2
            parts.append(rect(lx, by, 8, bh, fill,
                               stroke=stroke, sw=0.6, rx=0.8, dash=dash))
            parts.append(text(lx + 9.5, ly + LEGEND_H * 0.55, lbl, 4.5, COL_LABEL, anchor="start"))
            lx += 9.5 + 5 * len(lbl) * 0.42 + 6

        # Border
        parts.append(
            f'<rect x="0" y="0" width="{W:.1f}" height="{H:.1f}"'
            f' fill="none" stroke="{COL_SEP}" stroke-width="0.4"/>'
        )

        return "\n".join(parts)

    # ---------------------------------------------------------------------------
    # Combine two panels side-by-side
    # ---------------------------------------------------------------------------

    def combine(panel_b: str, panel_g: str) -> str:
        GAP     = 8.0
        total_w = PANEL_W * 2 + GAP
        total_h = PANEL_H

        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' width="{total_w:.1f}" height="{total_h:.1f}"'
            f' viewBox="0 0 {total_w:.1f} {total_h:.1f}">',
            f'<rect width="{total_w:.1f}" height="{total_h:.1f}" fill="#ffffff"/>',
            f'<g transform="translate(0 0)">\n{panel_b}\n</g>',
            (
                f'<line x1="{PANEL_W + GAP/2:.1f}" y1="0"'
                f' x2="{PANEL_W + GAP/2:.1f}" y2="{total_h:.1f}"'
                f' stroke="{COL_SEP}" stroke-width="0.5" stroke-dasharray="4,3"/>'
            ),
            f'<g transform="translate({PANEL_W + GAP:.1f} 0)">\n{panel_g}\n</g>',
            "</svg>",
        ]
        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Main
    # ---------------------------------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events_b = _load(trace_baseline_path)
    events_g = _load(trace_guardrail_path)
    calls_b = extract_calls(events_b)
    calls_g = extract_calls(events_g)
    panel_b = render_panel(calls_b, "Baseline")
    panel_g = render_panel(calls_g, "+Guardrail")
    combined = combine(panel_b, panel_g)
    output_path.write_text(combined, encoding="utf-8")
    print(f"Saved → {output_path}")

class FigureTrajectorySwimlaneStep(PipelineStep):
    type = "figure_trajectory_swimlane"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_trace_figure(ctx, self, render)

register_step_type("figure_trajectory_swimlane", FigureTrajectorySwimlaneStep)

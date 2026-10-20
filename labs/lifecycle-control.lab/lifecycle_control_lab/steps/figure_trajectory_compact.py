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

    AGENT_ORDER = ["moderator", "itinerary_agent", "schedule_agent", "concierge_agent"]
    AGENT_LABEL = {
        "moderator":        "moderator",
        "itinerary_agent":  "itinerary",
        "schedule_agent":   "schedule",
        "concierge_agent":  "concierge",
    }

    # ---------------------------------------------------------------------------
    # Colours (B&W, print-safe)
    # ---------------------------------------------------------------------------
    COL_BG       = "#ffffff"
    COL_LANE_EVEN = "#f5f5f5"
    COL_LANE_ODD  = "#ebebeb"
    COL_LABEL    = "#222222"
    COL_SEP      = "#aaaaaa"
    COL_PROC     = "#d4d4d4"   # ProcessingCall — light grey
    COL_LLM      = "#1a1a1a"   # LLMCall        — near black
    COL_TOOL     = "#666666"   # ToolCall       — medium grey
    COL_BLOCKED_FILL   = "#f0f0f0"
    COL_BLOCKED_STROKE = "#1a1a1a"
    COL_ACCENT   = "#555555"   # session/mas span

    FONT = "ui-monospace,SFMono-Regular,Consolas,Liberation Mono,monospace"

    # ---------------------------------------------------------------------------
    # Parse a trace
    # ---------------------------------------------------------------------------

    def _load(path: pathlib.Path) -> list[dict]:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _extract_calls(events: list[dict]) -> dict[str, list[dict]]:
        """Return per-agent ordered list of call dicts.

        Each call dict has: type ('proc'|'llm'|'tool'|'blocked'), label, seq.
        """
        CALL_KINDS = {
            "processing_call_start": "proc",
            "llm_call_start": "llm",
            "tool_call_start": "tool",
            "governance_denied": "blocked",
        }

        # Sort by timestamp
        events = sorted(events, key=lambda e: e.get("timestamp", 0))

        per_agent: dict[str, list[dict]] = {a: [] for a in AGENT_ORDER}
        # Strategy: the blocked agent is identified by the 'audit' event that fires
        # for the affected agent after governance_policy|block and before governance_denied.
        # Also track the first governance_event for a known agent after a policy block.
        blocked_agent_candidate: str | None = None
        saw_policy_block = False

        for e in events:
            kind = e.get("kind", "")
            agent_id = e.get("agent_id", "")

            # Detect governance_policy block → mark that the next audit/governance_event
            # with a known agent_id is our candidate.
            if kind == "governance_policy" and e.get("action_taken", "") == "block":
                saw_policy_block = True
                blocked_agent_candidate = None

            # After a policy block, the first audit event for a known agent is the blocked agent.
            if saw_policy_block and kind == "audit" and agent_id in per_agent:
                if blocked_agent_candidate is None:
                    blocked_agent_candidate = agent_id

            ctype = CALL_KINDS.get(kind)
            if ctype is None:
                continue

            if ctype == "blocked":
                target = blocked_agent_candidate if blocked_agent_candidate in (per_agent or {}) else agent_id
                if target and target in per_agent:
                    per_agent[target].append({"type": "blocked", "label": "BLOCK", "seq": len(per_agent[target])})
                saw_policy_block = False
                blocked_agent_candidate = None
                continue

            if agent_id not in per_agent:
                continue

            label = ""
            if ctype == "llm":
                label = "LLM"
            elif ctype == "tool":
                raw = e.get("tool_name", "")
                label = raw[:6] if raw else "tool"
            elif ctype == "proc":
                label = "⚙"

            per_agent[agent_id].append({
                "type": ctype,
                "label": label,
                "seq": len(per_agent[agent_id]),
            })

        return per_agent

    # ---------------------------------------------------------------------------
    # SVG panel renderer
    # ---------------------------------------------------------------------------

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def render_panel(calls_by_agent: dict[str, list[dict]], title: str,
                     W: float, H: float) -> str:
        """Render one panel as SVG inner content (no <svg> wrapper).

        W, H are the panel dimensions in SVG user units (= pt for PDF).
        """
        parts: list[str] = []

        TITLE_H = 12.0
        LABEL_W = 54.0
        PAD_H   = 2.0      # top/bottom padding inside each lane
        LANE_SEP = 0.4     # separator line between lanes
        N_LANES = len(AGENT_ORDER)
        LANE_H = (H - TITLE_H) / N_LANES
        BLOCK_H = LANE_H - PAD_H * 2 - 1.0  # inner block height
        TW = W - LABEL_W - 4.0              # timeline width (px after label + gap)
        TX = LABEL_W + 4.0                  # timeline x start
        FONT_SIZE = 5.0

        # Background
        parts.append(f'<rect width="{W:.1f}" height="{H:.1f}" fill="{COL_BG}"/>')

        # Title
        parts.append(
            f'<text x="{W/2:.1f}" y="{TITLE_H - 2.5:.1f}"'
            f' text-anchor="middle" font-size="7" font-weight="bold"'
            f' fill="{COL_LABEL}" font-family="{FONT}">{_esc(title)}</text>'
        )

        # Compute global max calls for uniform x-scaling across agents
        all_seqs = [len(calls) for calls in calls_by_agent.values() if calls]
        max_calls = max(all_seqs) if all_seqs else 1
        col_w = TW / max_calls  # width of one call column

        for li, agent_id in enumerate(AGENT_ORDER):
            calls = calls_by_agent.get(agent_id, [])
            lane_bg = COL_LANE_EVEN if li % 2 == 0 else COL_LANE_ODD
            y0 = TITLE_H + li * LANE_H
            cy = y0 + LANE_H / 2

            # Lane background
            parts.append(
                f'<rect x="0" y="{y0:.1f}" width="{W:.1f}" height="{LANE_H:.1f}"'
                f' fill="{lane_bg}"/>'
            )

            # Lane separator
            if li > 0:
                parts.append(
                    f'<line x1="0" y1="{y0:.1f}" x2="{W:.1f}" y2="{y0:.1f}"'
                    f' stroke="{COL_SEP}" stroke-width="{LANE_SEP:.1f}"/>'
                )

            # Lane label
            label_text = AGENT_LABEL.get(agent_id, agent_id[:8])
            parts.append(
                f'<text x="{LABEL_W - 2:.1f}" y="{cy + FONT_SIZE/3:.1f}"'
                f' text-anchor="end" font-size="{FONT_SIZE}" fill="{COL_LABEL}"'
                f' font-family="{FONT}">{_esc(label_text)}</text>'
            )

            # Left accent bar
            parts.append(
                f'<rect x="0" y="{y0 + 1:.1f}" width="2.5" height="{LANE_H - 2:.1f}"'
                f' fill="{COL_ACCENT}" rx="1"/>'
            )

            if not calls:
                continue

            n = len(calls)
            bw = TW / n  # actual block width for this agent
            by = y0 + PAD_H + 0.5
            bh = BLOCK_H

            for ci, call in enumerate(calls):
                bx = TX + ci * bw
                ctype = call["type"]

                if ctype == "proc":
                    fill = COL_PROC
                    stroke = "none"
                    dasharray = ""
                    text_fill = "#666666"
                elif ctype == "llm":
                    fill = COL_LLM
                    stroke = "none"
                    dasharray = ""
                    text_fill = "#ffffff"
                elif ctype == "tool":
                    fill = COL_TOOL
                    stroke = "none"
                    dasharray = ""
                    text_fill = "#ffffff"
                elif ctype == "blocked":
                    fill = COL_BLOCKED_FILL
                    stroke = COL_BLOCKED_STROKE
                    dasharray = ' stroke-dasharray="2,1"'
                    text_fill = "#1a1a1a"
                else:
                    fill = "#cccccc"
                    stroke = "none"
                    dasharray = ""
                    text_fill = "#333333"

                rx = min(1.5, bw * 0.2)
                stroke_attr = f' stroke="{stroke}" stroke-width="0.6"{dasharray}' if stroke != "none" else ""
                parts.append(
                    f'<rect x="{bx + 0.5:.2f}" y="{by:.2f}"'
                    f' width="{bw - 1.0:.2f}" height="{bh:.2f}"'
                    f' fill="{fill}" rx="{rx:.1f}"{stroke_attr}/>'
                )

                # Label inside block if wide enough
                if bw >= 10.0:
                    lbl = call["label"]
                    if ctype == "blocked":
                        lbl = "✕"
                    elif ctype == "proc":
                        lbl = "⚙"
                    parts.append(
                        f'<text x="{bx + bw/2:.2f}" y="{by + bh/2 + FONT_SIZE*0.35:.2f}"'
                        f' text-anchor="middle" font-size="{FONT_SIZE:.1f}"'
                        f' fill="{text_fill}" font-family="{FONT}">{_esc(lbl)}</text>'
                    )

        # Bottom border
        parts.append(
            f'<line x1="0" y1="{H:.1f}" x2="{W:.1f}" y2="{H:.1f}"'
            f' stroke="{COL_SEP}" stroke-width="0.4"/>'
        )

        # Right border
        parts.append(
            f'<line x1="{W:.1f}" y1="0" x2="{W:.1f}" y2="{H:.1f}"'
            f' stroke="{COL_SEP}" stroke-width="0.4"/>'
        )

        return "\n".join(parts)

    # ---------------------------------------------------------------------------
    # Combine two panels
    # ---------------------------------------------------------------------------

    def combine_panels(panel_b: str, panel_g: str,
                       PW: float, PH: float) -> str:
        """Combine two panels side-by-side with a vertical divider."""
        GAP = 6.0
        total_w = PW * 2 + GAP
        total_h = PH

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' width="{total_w:.1f}" height="{total_h:.1f}"'
            f' viewBox="0 0 {total_w:.1f} {total_h:.1f}">',
            f'<rect width="{total_w:.1f}" height="{total_h:.1f}" fill="{COL_BG}"/>',
            # Left panel
            f'<g transform="translate(0 0)">',
            panel_b,
            '</g>',
            # Divider
            f'<line x1="{PW + GAP/2:.1f}" y1="0" x2="{PW + GAP/2:.1f}" y2="{total_h:.1f}"'
            f' stroke="{COL_SEP}" stroke-width="0.5" stroke-dasharray="3,2"/>',
            # Right panel
            f'<g transform="translate({PW + GAP:.1f} 0)">',
            panel_g,
            '</g>',
            '</svg>',
        ]
        return "\n".join(svg_parts)

    # ---------------------------------------------------------------------------
    # Main
    # ---------------------------------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events_b = _load(trace_baseline_path)
    events_g = _load(trace_guardrail_path)
    calls_b = _extract_calls(events_b)
    calls_g = _extract_calls(events_g)
    pw, ph = 249.0, 92.0
    panel_b = render_panel(calls_b, "Baseline", pw, ph)
    panel_g = render_panel(calls_g, "+Guardrail", pw, ph)
    combined = combine_panels(panel_b, panel_g, pw, ph)
    output_path.write_text(combined, encoding="utf-8")
    print(f"Saved → {output_path}")
    pdf = output_path.with_suffix(".pdf")
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF
        drawing = svg2rlg(str(output_path))
        if drawing:
            renderPDF.drawToFile(drawing, str(pdf))
            print(f"Saved → {pdf}")
    except Exception as exc:
        print(f"PDF skipped: {exc}")

class FigureTrajectoryCompactStep(PipelineStep):
    type = "figure_trajectory_compact"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_trace_figure(ctx, self, render)

register_step_type("figure_trajectory_compact", FigureTrajectoryCompactStep)

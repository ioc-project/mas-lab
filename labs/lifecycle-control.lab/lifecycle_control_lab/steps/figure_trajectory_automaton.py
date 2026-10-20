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
    import pathlib

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
        "get_fares":            "fare",
    }

    # ---------------------------------------------------------------------------
    # Layout constants (pt — scale-invariant for LaTeX inclusion)
    # ---------------------------------------------------------------------------
    LABEL_W    = 50.0   # left label column width
    COL_W      = 20.0   # pt per call column
    DELEG_GAP  = 16.0   # horizontal gap for delegation arrows
    RETURN_GAP = 16.0   # horizontal gap for return arrows
    PAD_R      = 8.0

    N_INIT     = 1      # moderator LLM calls before delegation
    N_PAR_MAX  = 3      # max specialist calls (L T L)
    N_SYNTH    = 1      # moderator LLM calls after return

    LANE_H   = 24.0
    TITLE_H  = 11.0
    LEGEND_H = 24.0

    PANEL_W = (LABEL_W
               + N_INIT * COL_W + DELEG_GAP
               + N_PAR_MAX * COL_W + RETURN_GAP
               + N_SYNTH * COL_W + PAD_R)
    PANEL_H = TITLE_H + len(AGENTS) * LANE_H + LEGEND_H

    # Phase x-coordinates (left edges / reference points)
    _x0 = LABEL_W
    X_INIT_S  = _x0                                          # first init state
    X_INIT_E  = _x0 + N_INIT * COL_W                        # after init (delegation point)
    X_DELEG   = X_INIT_E + DELEG_GAP / 2                    # vertical delegation arrow x
    X_PAR_S   = X_INIT_E + DELEG_GAP                        # first parallel state
    X_PAR_E   = X_PAR_S + N_PAR_MAX * COL_W                 # end of parallel zone
    X_RETURN  = X_PAR_E + RETURN_GAP / 2                    # vertical return arrow x
    X_SYNTH_S = X_PAR_E + RETURN_GAP                        # first synth state
    X_SYNTH_E = X_SYNTH_S + N_SYNTH * COL_W                 # final state (right edge)

    # Geometry
    STATE_R = 2.2    # state circle radius
    AH_L    = 3.5    # arrowhead length
    AH_W    = 1.8    # arrowhead half-width
    CROSS_S = 2.4    # × marker half-size

    # Colours
    COL_BG_EVEN   = "#f6f6f6"
    COL_BG_ODD    = "#eeeeee"
    COL_LABEL     = "#222222"
    COL_LIFELINE  = "#cccccc"
    COL_STATE     = "#222222"
    COL_LLM       = "#141414"
    COL_TOOL      = "#444444"
    COL_BLOCKED   = "#222222"
    COL_DELEG     = "#777777"
    COL_SEP       = "#bbbbbb"

    FONT = "ui-monospace,SFMono-Regular,Consolas,Liberation Mono,monospace"

    # ---------------------------------------------------------------------------
    # Trace extraction
    # ---------------------------------------------------------------------------

    def _load(path: pathlib.Path) -> list[dict]:
        with open(path) as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def extract_calls(events: list[dict]) -> dict[str, list[dict]]:
        """Extract per-agent call list (LLM, Tool, Blocked only — proc filtered)."""
        INTERESTING = {"llm_call_start": "llm", "tool_call_start": "tool",
                       "governance_denied": "blocked"}

        events = sorted(events, key=lambda e: e.get("timestamp", 0))

        # Only keep llm_call_start events that have a matching llm_call_end:
        # outer "agent-loop" wrapper calls never complete before the next start,
        # so they have no end event — they're plumbing, not real inferences.
        llm_completed = {
            e.get("call_id")
            for e in events
            if e.get("kind") == "llm_call_end" and e.get("call_id")
        }

        per_agent: dict[str, list[dict]] = {a: [] for a in AGENTS}
        saw_policy = False
        blocked_candidate: str | None = None

        for e in events:
            kind     = e.get("kind", "")
            agent_id = e.get("agent_id", "")

            if kind == "governance_policy" and e.get("action_taken") == "block":
                saw_policy = True
                blocked_candidate = None
            if saw_policy and kind == "audit" and agent_id in per_agent and not blocked_candidate:
                blocked_candidate = agent_id

            ctype = INTERESTING.get(kind)
            if ctype is None:
                continue

            if ctype == "blocked":
                if blocked_candidate in per_agent:
                    per_agent[blocked_candidate].append({"type": "blocked", "abbrev": ""})
                saw_policy = False
                blocked_candidate = None
                continue

            if agent_id not in per_agent:
                continue

            # Skip outer "agent-loop" llm_call_start that wrap tool dispatch:
            # they have no matching llm_call_end, unlike real model inferences.
            if ctype == "llm" and e.get("call_id") not in llm_completed:
                continue

            tool   = e.get("tool_name")
            abbrev = TOOL_ABBREV.get(tool or "", tool[:4] if tool else "")
            per_agent[agent_id].append({"type": ctype, "abbrev": abbrev})

        # Collapse consecutive same-tool calls (parallel tool fan-outs → single entry)
        for agent_id in per_agent:
            collapsed: list[dict] = []
            for c in per_agent[agent_id]:
                if (collapsed
                        and c["type"] == "tool"
                        and collapsed[-1]["type"] == "tool"
                        and c["abbrev"] == collapsed[-1]["abbrev"]):
                    collapsed[-1]["count"] = collapsed[-1].get("count", 1) + 1
                else:
                    collapsed.append(dict(c))
            per_agent[agent_id] = collapsed

        return per_agent

    # ---------------------------------------------------------------------------
    # SVG helpers
    # ---------------------------------------------------------------------------

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _circle(cx: float, cy: float, r: float = STATE_R,
                fill: str = COL_STATE, stroke: str = "none") -> str:
        s = f' stroke="{stroke}" stroke-width="0.5"' if stroke != "none" else ""
        return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.1f}" fill="{fill}"{s}/>'

    def _double_circle(cx: float, cy: float) -> str:
        """Accepting state: outer + inner circle."""
        return (
            _circle(cx, cy, STATE_R + 1.8, "none", COL_STATE)
            + _circle(cx, cy, STATE_R)
        )

    def _arrow_h(x1: float, cy: float, x2: float, stroke: str, sw: float,
                 label: str = "", dash: str = "", head: str = "open") -> str:
        """Horizontal arrow from x1 to x2 (x2 is tip position).
        head='open' → open chevron (LLM); head='filled' → solid triangle (Tool)."""
        shaft_end = x2 - AH_L
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts = [
            f'<line x1="{x1:.2f}" y1="{cy:.2f}" x2="{shaft_end:.2f}" y2="{cy:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"{dash_attr}/>',
        ]
        if head == "filled":
            parts.append(
                f'<polygon points="{shaft_end:.2f},{cy - AH_W:.2f} {x2:.2f},{cy:.2f}'
                f' {shaft_end:.2f},{cy + AH_W:.2f}" fill="{stroke}"/>'
            )
        else:  # open chevron
            parts += [
                f'<line x1="{shaft_end:.2f}" y1="{cy - AH_W:.2f}"'
                f' x2="{x2:.2f}" y2="{cy:.2f}"'
                f' stroke="{stroke}" stroke-width="{sw:.1f}"/>',
                f'<line x1="{shaft_end:.2f}" y1="{cy + AH_W:.2f}"'
                f' x2="{x2:.2f}" y2="{cy:.2f}"'
                f' stroke="{stroke}" stroke-width="{sw:.1f}"/>',
            ]
        if label:
            lx = (x1 + x2) / 2
            parts.append(
                f'<text x="{lx:.2f}" y="{cy - 4.0:.2f}" text-anchor="middle"'
                f' font-size="4.0" fill="{stroke}" font-family="{FONT}"'
                f' font-style="italic">{_esc(label)}</text>'
            )
        return "\n".join(parts)

    def _cross(cx: float, cy: float) -> str:
        """× marker."""
        s = CROSS_S
        sw = 1.2
        return (
            f'<line x1="{cx-s:.2f}" y1="{cy-s:.2f}" x2="{cx+s:.2f}" y2="{cy+s:.2f}"'
            f' stroke="{COL_BLOCKED}" stroke-width="{sw:.1f}"/>'
            f'<line x1="{cx+s:.2f}" y1="{cy-s:.2f}" x2="{cx-s:.2f}" y2="{cy+s:.2f}"'
            f' stroke="{COL_BLOCKED}" stroke-width="{sw:.1f}"/>'
        )

    def _vline(x: float, y1: float, y2: float,
               stroke: str = COL_SEP, sw: float = 0.4, dash: str = "") -> str:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return (
            f'<line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{y2:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"{dash_attr}/>'
        )

    def _hline(y: float, x1: float, x2: float,
               stroke: str = COL_LIFELINE, sw: float = 0.5, dash: str = "") -> str:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return (
            f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"{dash_attr}/>'
        )

    def _arrow_v_down(x: float, y1: float, y2: float,
                      stroke: str = COL_DELEG, sw: float = 0.6) -> str:
        """Downward vertical arrow with open head."""
        tip = y2
        shaft_end = tip - AH_L
        return (
            f'<line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{shaft_end:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"/>'
            f'<line x1="{x - AH_W:.2f}" y1="{shaft_end:.2f}"'
            f' x2="{x:.2f}" y2="{tip:.2f}" stroke="{stroke}" stroke-width="{sw:.1f}"/>'
            f'<line x1="{x + AH_W:.2f}" y1="{shaft_end:.2f}"'
            f' x2="{x:.2f}" y2="{tip:.2f}" stroke="{stroke}" stroke-width="{sw:.1f}"/>'
        )

    def _arrow_v_up(x: float, y1: float, y2: float,
                    stroke: str = COL_DELEG, sw: float = 0.6,
                    dash: str = "") -> str:
        """Upward vertical arrow (y1 > y2) with open head. dash= for error returns."""
        tip = y2
        shaft_end = tip + AH_L
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return (
            f'<line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{shaft_end:.2f}"'
            f' stroke="{stroke}" stroke-width="{sw:.1f}"{dash_attr}/>'
            f'<line x1="{x - AH_W:.2f}" y1="{shaft_end:.2f}"'
            f' x2="{x:.2f}" y2="{tip:.2f}" stroke="{stroke}" stroke-width="{sw:.1f}"/>'
            f'<line x1="{x + AH_W:.2f}" y1="{shaft_end:.2f}"'
            f' x2="{x:.2f}" y2="{tip:.2f}" stroke="{stroke}" stroke-width="{sw:.1f}"/>'
        )

    # ---------------------------------------------------------------------------
    # Call-sequence rendering (per lane)
    # ---------------------------------------------------------------------------

    def _lane_cy(li: int) -> float:
        return TITLE_H + li * LANE_H + LANE_H / 2

    def _render_calls(calls: list[dict], x0: float, cy: float) -> str:
        """Draw state circles and typed arrows for one agent's call sequence.

        x0 is the x-position of the initial state (circle 0).
        Returns SVG fragment.
        """
        parts: list[str] = []

        for ci, call in enumerate(calls):
            sx  = x0 + ci * COL_W          # state circle x
            nx  = x0 + (ci + 1) * COL_W    # next state circle x
            ctype = call["type"]

            # Draw current state circle
            parts.append(_circle(sx, cy))

            if ctype == "llm":
                x1 = sx + STATE_R
                x2 = nx - STATE_R
                parts.append(_arrow_h(x1, cy, x2, COL_LLM, sw=1.0))

            elif ctype == "tool":
                x1 = sx + STATE_R
                x2 = nx - STATE_R
                count  = call.get("count", 1)
                abbrev = call.get("abbrev", "")
                label  = f"{abbrev}\u00d7{count}" if count > 1 else abbrev
                parts.append(_arrow_h(x1, cy, x2, COL_TOOL, sw=0.7, label=label, head="filled"))

            elif ctype == "blocked":
                # Dashed arrow ending with ×, no arrival state
                x_cross = nx - STATE_R - 1.0
                x1 = sx + STATE_R
                x2 = x_cross - CROSS_S - 1.0
                parts.append(
                    _arrow_h(x1, cy, x2 + AH_L, COL_BLOCKED, sw=0.7, dash="2.5,1.5")
                )
                parts.append(_cross(x_cross, cy))
                # ── Governance policy annotation ──────────────────────────────
                # Light bracket above the blocked transition span, labelled "policy"
                bx0   = sx - STATE_R          # left edge of span
                bx1   = x_cross + CROSS_S     # right edge of span
                bmid  = (bx0 + bx1) / 2
                top   = cy - LANE_H * 0.42    # just above the arrow zone
                tick  = 2.0
                bracket_col = "#888888"
                parts.append(
                    f'<line x1="{bx0:.2f}" y1="{top + tick:.2f}"'
                    f' x2="{bx0:.2f}" y2="{top:.2f}"'
                    f' stroke="{bracket_col}" stroke-width="0.5"/>'
                    f'<line x1="{bx0:.2f}" y1="{top:.2f}"'
                    f' x2="{bx1:.2f}" y2="{top:.2f}"'
                    f' stroke="{bracket_col}" stroke-width="0.5"/>'
                    f'<line x1="{bx1:.2f}" y1="{top:.2f}"'
                    f' x2="{bx1:.2f}" y2="{top + tick:.2f}"'
                    f' stroke="{bracket_col}" stroke-width="0.5"/>'
                )
                parts.append(
                    f'<text x="{bmid:.2f}" y="{top - 1.0:.2f}" text-anchor="middle"'
                    f' font-size="3.5" fill="{bracket_col}"'
                    f' font-family="{FONT}" font-style="italic">content-filter</text>'
                )
                return "\n".join(parts)             # no final state circle

        # All calls done — final accepting state (double circle)
        final_x = x0 + len(calls) * COL_W
        if calls:
            parts.append(_double_circle(final_x, cy))
        else:
            parts.append(_circle(x0, cy))  # empty agent: just start circle

        return "\n".join(parts)

    # ---------------------------------------------------------------------------
    # Panel renderer
    # ---------------------------------------------------------------------------

    def render_panel(calls_by_agent: dict[str, list[dict]], title: str) -> str:
        W, H = PANEL_W, PANEL_H
        parts: list[str] = []

        # White background
        parts.append(f'<rect width="{W:.1f}" height="{H:.1f}" fill="#ffffff"/>')

        # Title
        parts.append(
            f'<text x="{W/2:.1f}" y="{TITLE_H - 2:.1f}" text-anchor="middle"'
            f' font-size="7.5" font-weight="bold" fill="{COL_LABEL}"'
            f' font-family="{FONT}">{_esc(title)}</text>'
        )

        # Lane backgrounds and labels
        for li, agent_id in enumerate(AGENTS):
            top = TITLE_H + li * LANE_H
            bg  = COL_BG_EVEN if li % 2 == 0 else COL_BG_ODD
            cy  = _lane_cy(li)

            parts.append(
                f'<rect x="0" y="{top:.1f}" width="{W:.1f}" height="{LANE_H:.1f}"'
                f' fill="{bg}"/>'
            )
            if li > 0:
                parts.append(_hline(top, 0, W, COL_SEP, sw=0.3))

            # Left accent bar
            parts.append(
                f'<rect x="0" y="{top+1:.1f}" width="2.0" height="{LANE_H-2:.1f}"'
                f' fill="{COL_LABEL}" rx="0.5"/>'
            )

            # Agent label
            lbl = AGENT_LABEL.get(agent_id, agent_id[:8])
            parts.append(
                f'<text x="{LABEL_W - 3:.1f}" y="{cy + 1.8:.1f}"'
                f' text-anchor="end" font-size="5.0" fill="{COL_LABEL}"'
                f' font-family="{FONT}">{_esc(lbl)}</text>'
            )

            # ── Lifelines ──────────────────────────────────────────────────────
            if agent_id == "moderator":
                # Active during init and synth, dashed/idle during parallel phase
                parts.append(_hline(cy, X_INIT_S, X_INIT_E, COL_LIFELINE, sw=0.5))
                parts.append(_hline(cy, X_INIT_E, X_PAR_S, COL_LIFELINE, sw=0.3, dash="1,2"))
                parts.append(_hline(cy, X_PAR_E, X_SYNTH_E, COL_LIFELINE, sw=0.5))
            else:
                calls = calls_by_agent.get(agent_id, [])
                n = len(calls)
                # Lifeline spans from delegation point to last state
                x_end = X_PAR_S + n * COL_W
                if calls and calls[-1]["type"] == "blocked":
                    # Lifeline up to just before blocked marker
                    x_end = X_PAR_S + (n - 1) * COL_W + COL_W * 0.75
                parts.append(_hline(cy, X_PAR_S - DELEG_GAP * 0.3, x_end, COL_LIFELINE, sw=0.5))

        # ── Call sequences ──────────────────────────────────────────────────────
        for li, agent_id in enumerate(AGENTS):
            cy    = _lane_cy(li)
            calls = calls_by_agent.get(agent_id, [])

            if agent_id == "moderator":
                # Split into init and synth
                init_calls  = [c for c in calls[:N_INIT]]
                synth_calls = [c for c in calls[N_INIT:]]
                parts.append(_render_calls(init_calls, X_INIT_S, cy))
                parts.append(_render_calls(synth_calls, X_SYNTH_S, cy))
            else:
                parts.append(_render_calls(calls, X_PAR_S, cy))

        # ── Delegation arrows (moderator → each agent) ──────────────────────────
        mod_cy = _lane_cy(0)
        for li in range(1, len(AGENTS)):
            target_cy = _lane_cy(li)
            x_arr = X_DELEG - (li - 1) * 1.4   # slight horizontal stagger
            y1 = mod_cy + STATE_R + 0.5
            y2 = target_cy - STATE_R - 0.5
            parts.append(_arrow_v_down(x_arr, y1, y2))

        # ── "‖" parallel annotation between delegation and return ────────────────
        par_cx = (X_PAR_S + X_PAR_E) / 2
        par_y  = TITLE_H + 3.0
        parts.append(
            f'<text x="{par_cx:.1f}" y="{par_y:.1f}" text-anchor="middle"'
            f' font-size="5.0" fill="{COL_SEP}" font-family="{FONT}"'
            f' font-style="italic">parallel</text>'
        )

        # ── Return arrows (each agent → moderator) ─────────────────────────────
        # Each arrow originates at the x where the agent's sublane ends (faithful
        # to the actual finish time), then goes straight up to the moderator lane.
        # Agents that ended with 'blocked' use a dashed shaft (error return).
        y2_rtn = mod_cy + STATE_R + 0.5
        for li in range(1, len(AGENTS)):
            agent_id    = AGENTS[li]
            agent_calls = calls_by_agent.get(agent_id, [])
            is_err      = bool(agent_calls) and agent_calls[-1]["type"] == "blocked"
            source_cy   = _lane_cy(li)
            y1_rtn      = source_cy - STATE_R - 0.5
            n = len(agent_calls)
            if is_err:
                # Arrow from the cross position (≈ 75% into the blocked step)
                x_arr = X_PAR_S + (n - 1) * COL_W + COL_W * 0.75
            else:
                # Arrow from the final accepting state
                x_arr = X_PAR_S + n * COL_W
            dash = "2,2" if is_err else ""
            parts.append(_arrow_v_up(x_arr, y1_rtn, y2_rtn, dash=dash))

        # ── Phase separator lines ────────────────────────────────────────────────
        content_y1 = TITLE_H
        content_y2 = PANEL_H - LEGEND_H
        for x_sep in [X_INIT_E, X_PAR_E]:
            parts.append(_vline(x_sep, content_y1, content_y2, COL_SEP, sw=0.35, dash="3,2"))

        # ── Legend (2 rows) ──────────────────────────────────────────────────────
        ly    = PANEL_H - LEGEND_H + 2.0
        row_h = 10.0
        lcy1  = ly + row_h * 0.5          # row 1 centre: call types
        lcy2  = ly + row_h * 0.5 + row_h  # row 2 centre: structural arrows

        def _legend_item(x: float, cy_row: float, arrow_fn, label: str) -> tuple[str, float]:
            """Draw one legend item (horizontal call type), return SVG + new x."""
            item_parts = []
            x1, x2 = x, x + 14.0
            item_parts.append(arrow_fn(x1, cy_row, x2))
            item_parts.append(
                f'<text x="{x2 + 2.0:.1f}" y="{cy_row + 1.8:.1f}" text-anchor="start"'
                f' font-size="4.5" fill="{COL_LABEL}" font-family="{FONT}">{_esc(label)}</text>'
            )
            next_x = x2 + 2.0 + len(label) * 2.7 + 5.0
            return "\n".join(item_parts), next_x

        lx = LABEL_W + 2.0

        # Row 1 — call type arrows
        svgi, lx = _legend_item(
            lx, lcy1,
            lambda x1, cy2, x2: _arrow_h(x1 + STATE_R, cy2, x2, COL_LLM, sw=1.0),
            "LLM call",
        )
        parts.append(svgi)

        svgi, lx = _legend_item(
            lx, lcy1,
            lambda x1, cy2, x2: _arrow_h(x1 + STATE_R, cy2, x2, COL_TOOL, sw=0.7, head="filled"),
            "Tool call",
        )
        parts.append(svgi)

        x_bl    = lx
        x_cross = x_bl + 14.0 - CROSS_S - 1.0
        x2_bl   = x_cross - CROSS_S - 1.0
        parts.append(
            _arrow_h(x_bl + STATE_R, lcy1, x2_bl + AH_L, COL_BLOCKED, sw=0.7, dash="2.5,1.5")
        )
        parts.append(_cross(x_cross, lcy1))
        parts.append(
            f'<text x="{x_bl + 16.5:.1f}" y="{lcy1 + 1.8:.1f}" text-anchor="start"'
            f' font-size="4.5" fill="{COL_LABEL}" font-family="{FONT}">Blocked</text>'
        )

        # Row 2 — structural arrows + accepting state
        lx2 = LABEL_W + 2.0

        # Accepting state (double circle)
        dc_cx = lx2 + 4.0
        parts.append(_double_circle(dc_cx, lcy2))
        parts.append(
            f'<text x="{dc_cx + STATE_R + 2.0 + 1.8:.1f}" y="{lcy2 + 1.8:.1f}"'
            f' text-anchor="start" font-size="4.5" fill="{COL_LABEL}"'
            f' font-family="{FONT}">Sub-agent result</text>'
        )
        lx2 += 4.0 + STATE_R + 2.0 + 1.8 + 2.0 + 16 * 2.7 + 5.0

        # Delegation arrow (short downward)
        deleg_x = lx2 + 4.0
        arr_len  = 8.0
        parts.append(_arrow_v_down(deleg_x, lcy2 - arr_len * 0.45, lcy2 + arr_len * 0.45))
        parts.append(
            f'<text x="{deleg_x + 3.5:.1f}" y="{lcy2 + 1.8:.1f}" text-anchor="start"'
            f' font-size="4.5" fill="{COL_LABEL}" font-family="{FONT}">Delegation</text>'
        )
        lx2 += 4.0 + 3.5 + 10 * 2.7 + 5.0

        # Return arrow (short upward, solid)
        rtn_x = lx2 + 4.0
        parts.append(_arrow_v_up(rtn_x, lcy2 + arr_len * 0.45, lcy2 - arr_len * 0.45))
        parts.append(
            f'<text x="{rtn_x + 3.5:.1f}" y="{lcy2 + 1.8:.1f}" text-anchor="start"'
            f' font-size="4.5" fill="{COL_LABEL}" font-family="{FONT}">Return</text>'
        )

        # Border
        parts.append(
            f'<rect x="0" y="0" width="{W:.1f}" height="{H:.1f}"'
            f' fill="none" stroke="{COL_SEP}" stroke-width="0.4"/>'
        )

        return "\n".join(parts)

    # ---------------------------------------------------------------------------
    # Combine two panels
    # ---------------------------------------------------------------------------

    def combine(panel_b: str, panel_g: str) -> str:
        """Stack panels vertically so x-axis aligns between baseline and +guardrail."""
        GAP = 5.0
        total_w = PANEL_W
        total_h = PANEL_H * 2 + GAP
        sep_y = PANEL_H + GAP / 2
        return "\n".join([
            f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' width="{total_w:.1f}" height="{total_h:.1f}"'
            f' viewBox="0 0 {total_w:.1f} {total_h:.1f}">',
            f'<rect width="{total_w:.1f}" height="{total_h:.1f}" fill="#ffffff"/>',
            f'<g transform="translate(0 0)">\n{panel_b}\n</g>',
            # Thin dashed rule between panels
            (
                f'<line x1="0" y1="{sep_y:.1f}"'
                f' x2="{total_w:.1f}" y2="{sep_y:.1f}"'
                f' stroke="{COL_SEP}" stroke-width="0.5" stroke-dasharray="4,3"/>'
            ),
            f'<g transform="translate(0 {PANEL_H + GAP:.1f})">\n{panel_g}\n</g>',
            "</svg>",
        ])

    # ---------------------------------------------------------------------------
    # PDF export
    # ---------------------------------------------------------------------------

    def _write_pdf(svg: pathlib.Path, pdf: pathlib.Path) -> None:
        import subprocess
        r = subprocess.run(
            ["uv", "tool", "run", "--from", "svglib[reportlab]",
             "python3", "-c",
             f"from svglib.svglib import svg2rlg; from reportlab.graphics import renderPDF;"
             f" d=svg2rlg(r'{svg}'); renderPDF.drawToFile(d,r'{pdf}') if d else None"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and pdf.exists():
            print(f"Written {pdf}")
        else:
            print(f"PDF skipped: {r.stderr[:120]}")

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
    _write_pdf(output_path, output_path.with_suffix(".pdf"))

class FigureTrajectoryAutomatonStep(PipelineStep):
    type = "figure_trajectory_automaton"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_trace_figure(ctx, self, render)

register_step_type("figure_trajectory_automaton", FigureTrajectoryAutomatonStep)

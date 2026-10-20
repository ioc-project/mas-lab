#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline figure step (migrated from lab script)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

from ._helpers import execute_csv_figure

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

def render(output_path: Path, mealy_stats_path: Path, *, full_width: bool = True) -> None:
    import numpy as np
    import pandas as pd
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.ticker as mticker
    from pathlib import Path

    FULL_WIDTH = full_width

    matplotlib.rcParams.update({
        "font.family": "sans-serif", "font.size": 8,
        "axes.titlesize": 8, "xtick.labelsize": 7,
    })

    ms   = pd.read_csv(mealy_stats_path)
    SCENARIO = "production-like"   # all governance policies stacked
    b    = ms[ms.scenario == SCENARIO].copy()
    GOV_LAT = 0.194  # ms per governance check (measured)

    AGENTS = ["concierge_agent", "itinerary_agent", "schedule_agent", "moderator"]
    AL = {"concierge_agent": "Concierge", "itinerary_agent": "Itinerary",
          "schedule_agent": "Schedule",   "moderator": "Moderator"}
    AC = {"concierge_agent": "#9DC8E8", "itinerary_agent": "#5DA3D9",
          "schedule_agent":  "#3A7FC1", "moderator":       "#1A4F8C"}

    # Five governance hooks, ordered by typical call count (most→least frequent)
    HOOKS = [
        ("gov_pre_tool_call_n",   "#3DAB3B", "pre_tool"),   # fires at every tool call
        ("gov_pre_llm_call_n",    "#6CC26A", "pre_llm"),    # fires at every LLM call
        ("gov_pre_execution_n",   "#A8D5A2", "pre_exec"),   # fires once per agent run
        ("gov_pre_agent_comm_n",  "#1E7B1C", "pre_comm"),   # fires at inter-agent msgs
        ("gov_user_output_n",     "#0D5C0B", "user_out"),   # fires at final output
    ]

    # ── aggregate (mean over all items in scenario) ───────────────────────────────
    ba = b.groupby("agent_id").agg(
        llm_ms  = ("llm_total_ms",  "mean"),
        tool_ms = ("tool_total_ms", "mean"),
        **{col: (col, "mean") for col, _, _ in HOOKS},
    ).reindex(AGENTS)

    for col, _, _ in HOOKS:
        ba[col + "_ms"] = ba[col] * GOV_LAT
    ba["gov_ms"] = sum(ba[col + "_ms"] for col, _, _ in HOOKS)

    T = {
        "sys":  0.0,
        "gov":  ba["gov_ms"].sum(),
        "tool": ba["tool_ms"].sum(),
        "llm":  ba["llm_ms"].sum(),
    }
    T["plugins"] = T["gov"] + T["tool"]

    print(f"Scenario: {SCENARIO} | Mode: {'full-width' if FULL_WIDTH else 'context'}")
    print("Totals (cumulative, ms):")
    for k, v in T.items():
        print(f"  {k:10s}: {v:9.2f} ms")

    # ── positions on the GLOBAL log axis ─────────────────────────────────────────
    XMIN  = 0.02
    SYS_W = 0.05
    pos   = {}
    cur   = XMIN
    for seg, w in [("sys", SYS_W), ("plugins", T["plugins"]), ("llm", T["llm"])]:
        pos[seg] = cur
        cur += w
    pos["gov"]  = pos["plugins"]
    pos["tool"] = pos["plugins"] + T["gov"]
    GLOBAL_XMIN = XMIN * 0.4
    GLOBAL_XMAX = (SYS_W + T["plugins"] + T["llm"]) * 1.12

    # ── zoom xlim helper ──────────────────────────────────────────────────────────
    def zoom_xlim(left, width, fill=0.78):
        """
        Return (xmin, xmax) such that the segment [left, left+width] occupies
        `fill` fraction of the log-axis visual width.
        """
        log_l = np.log10(max(left, 1e-12))
        log_r = np.log10(max(left + width, 1e-12))
        log_c = (log_l + log_r) / 2
        half  = (log_r - log_l) / (2 * fill)
        return (10 ** (log_c - half), 10 ** (log_c + half))

    # ── colours ───────────────────────────────────────────────────────────────────
    C = {"sys": "#CCCCCC", "plugins": "#E8935A",
         "gov": "#5DBB63",  "tool":    "#4C9BE8", "llm": "#888888"}

    # ── draw helpers ─────────────────────────────────────────────────────────────
    BAR_H = 0.50
    BAR_Y = 0.50
    GHOST = 0.10

    def _fmt(x, _):
        if x >= 1_000: return f"{x/1000:.0f}s"
        if x >= 1:     return f"{x:.0f}ms"
        return f"{x:.3f}ms"

    def _setup(ax, xlim, row_label, show_x=False, indent=0):
        ax.set_xscale("log")
        ax.set_xlim(*xlim)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        # Decade-only major ticks; suppress sub-decade minor tick labels
        from matplotlib.ticker import LogLocator, NullFormatter
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=[1.0], numticks=12))
        ax.xaxis.set_minor_formatter(NullFormatter())
        for dec in [0.001, 0.01, 0.1, 1, 10, 100, 1_000, 10_000, 100_000]:
            if xlim[0] < dec < xlim[1]:
                ax.axvline(dec, color="#EBEBEB", lw=0.6, zorder=0)
        for sp in ["top", "right", "left"]:
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color("#CCCCCC")
        if show_x:
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(_fmt))
            ax.set_xlabel("Cumulative time (log scale)", fontsize=7, color="#444")
            ax.tick_params(axis="x", labelsize=7, length=2)
        else:
            ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
        prefix = "  " * indent
        ax.set_ylabel(prefix + row_label, fontsize=7, rotation=0,
                      labelpad=74 - indent * 5, va="center", color="#333")

    def _ghost(ax, segs, xlim):
        for left, w, color in segs:
            if left + w > xlim[0] and left < xlim[1]:   # only if visible
                ax.barh(BAR_Y, max(w, 1e-5), BAR_H, left=left,
                        color=color, edgecolor="none", alpha=GHOST, zorder=1)

    def _bar(ax, left, width, color, label=None, xlim=None):
        """Draw segment; auto-place label inside or to the right."""
        w = max(width, 1e-5)
        ax.barh(BAR_Y, w, BAR_H, left=left,
                color=color, edgecolor="white", linewidth=0.2, zorder=2)
        if label is None:
            return
        xl = xlim or (GLOBAL_XMIN, GLOBAL_XMAX)
        log_frac = (np.log10(max(left + w, 1e-12)) - np.log10(max(left, 1e-12))) / \
                   (np.log10(xl[1]) - np.log10(xl[0]))
        if log_frac > 0.08:
            ax.text(left + w / 2, BAR_Y, label,
                    ha="center", va="center", fontsize=6, color="#111",
                    clip_on=True, zorder=3)
        elif log_frac > 0.025:
            ax.text(left + w * 1.06, BAR_Y, label,
                    ha="left", va="center", fontsize=5.5, color="#555",
                    clip_on=True, zorder=3)

    def _note(ax, text):
        ax.text(0.995, 0.97, text, transform=ax.transAxes,
                ha="right", va="top", fontsize=5.5, color="#777", clip_on=False)

    # ═════════════════════════════════════════════════════════════════════════════
    # FIGURE  — 5 data rows + thin x-axis row
    # ═════════════════════════════════════════════════════════════════════════════
    ROW_H    = 0.65        # absolute row height in inches
    XROW_H   = 0.40        # x-axis strip height — taller to show colour spans
    N_DATA   = 5
    FIG_H    = N_DATA * ROW_H + XROW_H + 0.55   # top margin for suptitle
    HR = [ROW_H] * N_DATA + [XROW_H]

    fig, axes = plt.subplots(
        N_DATA + 1, 1,
        figsize=(7.0, FIG_H),
        gridspec_kw={"height_ratios": HR, "hspace": 0.10},
    )

    mode_str = "zoom" if FULL_WIDTH else "context"
    fig.suptitle(
        f"Fig 2a — Cost model: lifecycle-control plugin overhead vs LLM time\n"
        f"(scenario: {SCENARIO}, cumulative per-agent time, log scale; "
        f"plugin contracts are sub-1% of session time → stacking them is 'free')",
        fontsize=7.5, y=1.01, color="#333",
    )

    # ── pre-compute per-row xlim ──────────────────────────────────────────────────
    if FULL_WIDTH:
        xlims = {
            "L0":  (GLOBAL_XMIN, GLOBAL_XMAX),
            "L1":  zoom_xlim(pos["plugins"], T["plugins"]),
            "L2a": zoom_xlim(pos["gov"],  T["gov"]),
            "L3a": zoom_xlim(pos["gov"],  T["gov"]),
            "L2b": zoom_xlim(pos["tool"], T["tool"]),
        }
    else:
        xlims = {k: (GLOBAL_XMIN, GLOBAL_XMAX) for k in ("L0","L1","L2a","L3a","L2b")}

    # ── Row 0 : L0 — session ──────────────────────────────────────────────────────
    # sys(grey placeholder, not instrumented) | plugins(orange) | LLM(grey, cumul.)
    # The SM step time (sm_calls IS counted but sm_total_ms=0 — processing_call_end
    # emits latency_ms=0; runtime does not time composer.step()).
    ax = axes[0]
    xl = xlims["L0"]
    _bar(ax, pos["sys"],     SYS_W,       C["sys"],     None, xl)
    _bar(ax, pos["plugins"], T["plugins"], C["plugins"], "plugins", xl)
    _bar(ax, pos["llm"],     T["llm"],     C["llm"],     "LLM", xl)
    ax.text(pos["sys"] + SYS_W / 2, BAR_Y + 0.38, "⚠ SM=0ms",
            ha="center", va="bottom", fontsize=5.0, color="#999")
    # Key annotation: plugin overhead as a fraction of total session time
    plugin_pct = 100.0 * T["plugins"] / (T["plugins"] + T["llm"])
    _note(ax, f"plugins {T['plugins']:.1f}ms  |  LLM {T['llm']/1000:.1f}s (cumul.)  "
              f"→ plugin overhead = {plugin_pct:.2f}% of session time")
    _setup(ax, xl, "L0  session", indent=0)

    # ── Row 1 : L1 — zoom into plugins → gov | tool ───────────────────────────────
    # Decomposes orange segment: governance contract (green) + tool contract (blue).
    # Gov = evaluation cost of all policy checks; Tool = I/O time of all tool calls.
    ax = axes[1]
    xl = xlims["L1"]
    if not FULL_WIDTH:
        _ghost(ax, [(pos["sys"], SYS_W, C["sys"]), (pos["llm"], T["llm"], C["llm"])], xl)
    _bar(ax, pos["gov"],  T["gov"],  C["gov"],  "Gov",  xl)
    _bar(ax, pos["tool"], T["tool"], C["tool"], "Tool", xl)
    _note(ax, f"Gov {T['gov']:.2f}ms  |  Tool {T['tool']:.1f}ms")
    _setup(ax, xl, "↳ plugins", indent=1)

    # ── Row 2a : L2a — zoom into gov → 5 hooks ───────────────────────────────────
    # Decomposes governance time by which lifecycle hook fired.
    # Order (most→least frequent): pre_tool > pre_llm > pre_exec > pre_comm > user_out
    ax = axes[2]
    xl = xlims["L2a"]
    if not FULL_WIDTH:
        _ghost(ax, [(pos["sys"], SYS_W, C["sys"]),
                    (pos["tool"], T["tool"], C["tool"]),
                    (pos["llm"], T["llm"], C["llm"])], xl)
    left = pos["gov"]
    for col, color, lbl in HOOKS:
        v = ba[col + "_ms"].sum()
        _bar(ax, left, v, color, lbl, xl)
        left += max(v, 1e-5)
    _note(ax, "  ".join(f"{lbl} {ba[col+'_ms'].sum():.3f}ms" for col, _, lbl in HOOKS))
    _setup(ax, xl, "  ↳ gov (hooks)", indent=2)

    # ── Row 3a : L3a — zoom into gov → per-agent gov time ────────────────────────
    # Same x-range as L2a. Shows which agent pays the most governance overhead.
    # Concierge/Itinerary/Schedule have more tool calls → more pre_tool evaluations.
    ax = axes[3]
    xl = xlims["L3a"]
    if not FULL_WIDTH:
        _ghost(ax, [(pos["sys"], SYS_W, C["sys"]),
                    (pos["tool"], T["tool"], C["tool"]),
                    (pos["llm"], T["llm"], C["llm"])], xl)
    left = pos["gov"]
    for agent in AGENTS:
        v = ba.loc[agent, "gov_ms"]
        _bar(ax, left, v, AC[agent], AL[agent], xl)
        left += max(v, 1e-5)
    _note(ax, "  ".join(f"{AL[a]} {ba.loc[a,'gov_ms']:.3f}ms" for a in AGENTS))
    _setup(ax, xl, "  ↳ gov (agents)", indent=2)

    # ── Row 2b : L2b — zoom into tool → per-agent tool time ──────────────────────
    # Itinerary makes more/heavier tool calls (flight search, hotel search).
    ax = axes[4]
    xl = xlims["L2b"]
    if not FULL_WIDTH:
        _ghost(ax, [(pos["sys"], SYS_W, C["sys"]),
                    (pos["gov"], T["gov"], C["gov"]),
                    (pos["llm"], T["llm"], C["llm"])], xl)
    left = pos["tool"]
    for agent in AGENTS:
        v = ba.loc[agent, "tool_ms"]
        _bar(ax, left, v, AC[agent], AL[agent], xl)
        left += max(v, 1e-5)
    _note(ax, "  ".join(f"{AL[a]} {ba.loc[a,'tool_ms']:.1f}ms" for a in AGENTS
                        if ba.loc[a,'tool_ms'] > 0.1))
    _setup(ax, xl, "  ↳ tool (agents)", indent=2)

    # ── x-axis row ────────────────────────────────────────────────────────────────
    # Always use the GLOBAL xlim for the reference strip so the reader can
    # locate every row's zoomed region within the full session timeline.
    ax = axes[N_DATA]
    xl = (GLOBAL_XMIN, GLOBAL_XMAX)
    ax.set_xscale("log"); ax.set_xlim(*xl)
    ax.set_ylim(0, 1); ax.set_yticks([]); ax.set_frame_on(False)
    from matplotlib.ticker import LogLocator, NullFormatter
    ax.xaxis.set_major_locator(LogLocator(base=10, subs=[1.0], numticks=12))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_fmt))
    ax.set_xlabel("Cumulative time — global reference (log scale)", fontsize=7, color="#444")
    ax.tick_params(axis="x", labelsize=7, length=2, top=False)
    ax.spines["bottom"].set_color("#CCCCCC")
    for dec in [0.001, 0.01, 0.1, 1, 10, 100, 1_000, 10_000, 100_000]:
        if xl[0] < dec < xl[1]:
            ax.axvline(dec, color="#EBEBEB", lw=0.6, zorder=0)
    # In FULL_WIDTH mode: draw a coloured span showing where each zoomed region
    # sits within the global timeline, so the reader can orient themselves.
    if FULL_WIDTH:
        # Thin coloured indicators for each zoom region
        for seg, color, row_name in [
            ("plugins", C["plugins"], "↳ plugins"),
            ("gov",     C["gov"],     "↳ gov"),
            ("tool",    C["tool"],    "↳ tool"),
        ]:
            l, w = pos[seg], T[seg]
            ax.axvspan(l, l + w, ymin=0.60, ymax=1.0,
                       color=color, alpha=0.35, zorder=2, linewidth=0)

    # ── legend ────────────────────────────────────────────────────────────────────
    entries = [
        mpatches.Patch(facecolor=C["sys"],     label="System (SM, not timed)"),
        mpatches.Patch(facecolor=C["plugins"], label="User-plugin contracts"),
        mpatches.Patch(facecolor=C["gov"],     label="GovernanceContract"),
        mpatches.Patch(facecolor=C["tool"],    label="ToolContract"),
        mpatches.Patch(facecolor=C["llm"],     label="LLM API (cumulative)"),
        mpatches.Patch(facecolor=AC["concierge_agent"], label="Concierge"),
        mpatches.Patch(facecolor=AC["itinerary_agent"], label="Itinerary"),
        mpatches.Patch(facecolor=AC["schedule_agent"],  label="Schedule"),
        mpatches.Patch(facecolor=AC["moderator"],       label="Moderator"),
    ]
    fig.legend(handles=entries, loc="lower center", ncol=5, fontsize=6.0,
               bbox_to_anchor=(0.5, -0.09), framealpha=0.97, edgecolor="#DDD",
               handlelength=1.0, handletextpad=0.5, columnspacing=1.0)

    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    print(f"Saved → {output_path}")

class FigureMoodbarDetailStep(PipelineStep):
    type = "figure_moodbar_detail"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="mealy_stats",
            input_default="results/mealy_stats.csv",
            output_name="fig-moodbar-detail.png",
            render=render,
        )

register_step_type("figure_moodbar_detail", FigureMoodbarDetailStep)

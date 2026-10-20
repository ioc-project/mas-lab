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

def render(output_path: Path, mealy_stats_path: Path) -> None:
    import numpy as np
    import pandas as pd
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.ticker as mticker
    from pathlib import Path

    matplotlib.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 7.5,
        "xtick.labelsize": 7,
    })

    ms = pd.read_csv(mealy_stats_path)
    b  = ms[ms.scenario == "baseline"].copy()
    GOV_LAT = 0.194  # ms per governance check

    AGENTS = ["concierge_agent", "itinerary_agent", "schedule_agent", "moderator"]
    AGENT_LABEL = {"concierge_agent":"Concierge","itinerary_agent":"Itinerary",
                   "schedule_agent":"Schedule","moderator":"Moderator"}
    AGENT_C = {"concierge_agent":"#9DC8E8","itinerary_agent":"#5DA3D9",
               "schedule_agent":"#3A7FC1","moderator":"#1A4F8C"}

    HOOKS = [
        ("gov_pre_execution_n",  "#C5E8C5", "pre_exec"),
        ("gov_pre_llm_call_n",   "#82CC82", "pre_llm"),
        ("gov_pre_tool_call_n",  "#4CAF50", "pre_tool"),
        ("gov_pre_agent_comm_n", "#2E7D32", "pre_comm"),
        ("gov_user_output_n",    "#1B5E20", "user_out"),
    ]

    C_TOOL = "#4C9BE8"
    C_GOV  = "#5DBB63"
    C_SM   = "#DDAA55"   # SM: 0ms, shown as ghost

    # ── aggregate ─────────────────────────────────────────────────────────────────
    ba = b.groupby("agent_id").agg(
        tool_ms = ("tool_total_ms", "mean"),
        **{col: (col, "mean") for col, _, _ in HOOKS},
    ).reindex(AGENTS)

    for col, _, _ in HOOKS:
        ba[col + "_ms"] = ba[col] * GOV_LAT

    ba["gov_ms"]  = sum(ba[col + "_ms"] for col, _, _ in HOOKS)
    tot_tool = ba["tool_ms"].sum()
    tot_gov  = ba["gov_ms"].sum()
    tot_sm   = 0.0  # not instrumented

    hook_totals = {col: ba[col + "_ms"].sum() for col, _, _ in HOOKS}

    print(f"Non-LLM totals (ms): SM={tot_sm:.0f}  Tool={tot_tool:.1f}  Gov={tot_gov:.3f}")

    # ── shared axis ───────────────────────────────────────────────────────────────
    XMIN = 0.001
    XMAX = max(tot_tool, tot_gov) * 15   # generous right margin

    # ── figure ────────────────────────────────────────────────────────────────────
    # Rows: L0(1), L1a-tool(4 agents), L1b-gov(5 hooks), L2b-gov/hook by agent needs
    # to be compact — we'll do 3 representative hooks only to avoid too many rows
    # Actually show all hooks but compact (height ratio 1 per bar)

    NROWS = 1 + len(AGENTS) + len(HOOKS)   # L0 + L1a + L1b
    H_RATIOS = [1] + [1]*len(AGENTS) + [1]*len(HOOKS)

    fig, axes = plt.subplots(
        NROWS, 1,
        figsize=(7.0, 6.0),
        sharex=False,
        gridspec_kw={"height_ratios": H_RATIOS, "hspace": 0.0},
    )
    fig.suptitle(
        "Baseline — Non-LLM time decomposed by contract and plugin  (mean over 13 items, log scale)",
        fontsize=9, fontweight="bold", y=1.01,
    )

    H = 0.55  # bar height in data units (each subplot has ylim 0–1)

    def setup_ax(ax, show_xlabel=False):
        ax.set_xscale("log")
        ax.set_xlim(XMIN, XMAX)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        for ref in [0.001, 0.01, 0.1, 1, 10, 100]:
            ax.axvline(ref, color="#F0F0F0", linewidth=0.7, zorder=0)
        if show_xlabel:
            ax.set_xlabel("Time (ms, log scale)", fontsize=7)
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(
                lambda x, _: (f"{x:.0f}ms" if x >= 1 else f"{x:.3f}ms")))
            ax.tick_params(axis="x", labelsize=7, length=2)
        else:
            ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
        for sp in ["top", "right", "left"]: ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color("#EEE")

    def draw(ax, val, color, right_label="", ghost=False):
        """Draw one bar in a single-bar subplot (y centre = 0.5)."""
        alpha = 0.25 if ghost else 1.0
        ax.barh(0.5, max(val, XMIN*2), H, left=XMIN,
                color=color, edgecolor="white", linewidth=0.3,
                alpha=alpha, zorder=2)
        if right_label:
            txt = f"{val:.3f}ms" if val < 1 else (f"{val:.1f}ms" if val < 1000 else f"{val/1000:.1f}s")
            ax.text(max(val, XMIN*2) * 1.4, 0.5, f"{txt}  {right_label}",
                    va="center", fontsize=6.5, color=color if not ghost else "#AAA")

    def row_label(ax, txt, color="#333", indent=0):
        pad = "  " * indent
        ax.text(-0.01, 0.5, pad + txt, transform=ax.transAxes,
                ha="right", va="center", fontsize=7, color=color)

    # ── L0: total non-LLM ─────────────────────────────────────────────────────────
    ax0 = axes[0]
    # Stacked bar: SM (ghost) | Gov | Tool
    left = XMIN
    for val, color, alpha, lbl in [
        (0.0001, C_SM, 0.25, ""),          # SM: placeholder, ghost
        (tot_gov,  C_GOV,  1.0, ""),
        (tot_tool, C_TOOL, 1.0, ""),
    ]:
        ax0.barh(0.5, max(val, XMIN*2), H, left=left,
                 color=color, edgecolor="white", linewidth=0.3,
                 alpha=alpha, zorder=2)
        left += max(val, XMIN*2)

    ax0.text(XMIN * 1.4, 0.5,
             f"SM: 0ms (⚠ not instrumented)  |  Gov: {tot_gov:.1f}ms  |  Tool: {tot_tool:.0f}ms",
             va="center", fontsize=6.5, color="#555")
    row_label(ax0, "Non-LLM total")
    # Divider lines marking contract boundaries
    ax0.axvline(tot_gov + XMIN,  color=C_GOV,  lw=0.8, linestyle="--", alpha=0.5)
    ax0.axvline(tot_tool + tot_gov + XMIN, color=C_TOOL, lw=0.8, linestyle="--", alpha=0.5)
    setup_ax(ax0)

    # Section header: ToolContract
    n_tool = len(AGENTS)
    TOOL_START = 1
    for ii, agent in enumerate(AGENTS):
        ax = axes[TOOL_START + ii]
        v = ba.loc[agent, "tool_ms"]
        draw(ax, v, AGENT_C[agent],
             right_label=f"{AGENT_LABEL[agent]}")
        row_label(ax, f"  {AGENT_LABEL[agent]}", AGENT_C[agent], indent=1)
        setup_ax(ax, show_xlabel=(ii == n_tool - 1))
    # Add section label as text in first tool row
    axes[TOOL_START].set_title("ToolContract →", fontsize=7, loc="left",
                                pad=1, color=C_TOOL, fontweight="bold")

    # Section header: GovernanceContract
    GOV_START = TOOL_START + n_tool
    for ii, (col, color, lbl) in enumerate(HOOKS):
        ax = axes[GOV_START + ii]
        v = hook_totals[col]
        draw(ax, v, color, right_label=lbl)
        row_label(ax, f"  {lbl}", color, indent=1)
        setup_ax(ax, show_xlabel=(ii == len(HOOKS) - 1))
    axes[GOV_START].set_title("GovernanceContract →", fontsize=7, loc="left",
                                pad=1, color=C_GOV, fontweight="bold")

    # ── legend ────────────────────────────────────────────────────────────────────
    handles = [
        mpatches.Patch(facecolor=C_SM,   alpha=0.3, label="SM (not instrumented)"),
        mpatches.Patch(facecolor=C_TOOL, label="ToolContract"),
        mpatches.Patch(facecolor=C_GOV,  label="GovernanceContract"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=7,
               bbox_to_anchor=(0.5, -0.02), framealpha=0.95, edgecolor="#DDD")

    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    print(f"Saved → {output_path}")

class FigureNonllmStep(PipelineStep):
    type = "figure_nonllm"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="mealy_stats",
            input_default="results/mealy_stats.csv",
            output_name="fig-nonllm.png",
            render=render,
        )

register_step_type("figure_nonllm", FigureNonllmStep)

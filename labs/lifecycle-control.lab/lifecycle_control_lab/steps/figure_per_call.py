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
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from pathlib import Path

    ms = pd.read_csv(mealy_stats_path)
    ms_base = ms[ms["scenario"] == "baseline"].copy()

    GOV_LATENCY_MS = 0.194   # measured mean governance check latency

    # ── Governance hook columns ───────────────────────────────────────────────────
    HOOK_COLS = [
        "gov_pre_execution_n",
        "gov_pre_llm_call_n",
        "gov_pre_tool_call_n",
        "gov_pre_agent_comm_n",
        "gov_user_output_n",
    ]
    HOOK_SHORT = {
        "gov_pre_execution_n":    "pre_exec",
        "gov_pre_llm_call_n":     "pre_llm",
        "gov_pre_tool_call_n":    "pre_tool",
        "gov_pre_agent_comm_n":   "pre_comm",
        "gov_user_output_n":      "user_out",
    }
    HOOK_COLORS = {
        "pre_exec":  "#9B59B6",
        "pre_llm":   "#E67E22",
        "pre_tool":  "#27AE60",
        "pre_comm":  "#2980B9",
        "user_out":  "#E05252",
    }

    # ── Aggregate per agent (sum over all baseline items) ─────────────────────────
    gov_n_cols = [c for c in ms_base.columns if c.endswith("_n") and "gov_" in c
                  and not c.endswith("_fired")]
    ms_base["total_gov_n"] = ms_base[gov_n_cols].sum(axis=1)

    agg = ms_base.groupby("agent_id").agg(
        llm_mean_ms   = ("llm_mean_ms",   "mean"),   # mean LLM call duration
        llm_calls     = ("llm_calls",     "sum"),
        sm_per_event  = ("sm_total_ms",   "sum"),     # will divide by sm_calls
        sm_calls      = ("sm_calls",      "sum"),
        total_gov_n   = ("total_gov_n",   "sum"),
        **{h: (h, "sum") for h in HOOK_COLS},
    )
    agg["sm_per_event_ms"]  = agg["sm_per_event"] / agg["sm_calls"].clip(lower=1)
    agg["gov_per_llm_ms"]   = (agg["total_gov_n"] / agg["llm_calls"].clip(lower=1)) * GOV_LATENCY_MS

    # Hook ratios: checks per LLM call
    for h in HOOK_COLS:
        agg[HOOK_SHORT[h] + "_ratio"] = agg[h] / agg["llm_calls"].clip(lower=1)

    AGENTS = ["moderator", "schedule_agent", "itinerary_agent", "concierge_agent"]
    AGENT_LABELS = ["Moderator", "Schedule\nAgent", "Itinerary\nAgent", "Concierge\nAgent"]
    agg = agg.reindex(AGENTS)

    # ── Figure ─────────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        1, 2,
        figsize=(13, 5),
        gridspec_kw={"width_ratios": [1.2, 1]},
    )
    fig.suptitle(
        "Per-call overhead by component — baseline scenario, lifecycle-control.lab",
        fontsize=11, fontweight="bold",
    )

    # ────────────────────────────────────────────────────────────────────────────
    # Panel (a): Log-scale per-call breakdown per agent
    # ────────────────────────────────────────────────────────────────────────────
    ax1.set_title("(a) Per-call latency per component per agent (log scale)", fontsize=10)

    COMP_LABELS = ["LLM call\n(execution)", "SM processing\n(observability)", "Gov. eval\n(governance)"]
    COMP_COLORS = ["#CCCCCC", "#4C9BE8", "#5DBB63"]
    n_agents = len(AGENTS)
    n_comps  = 3
    x = np.arange(n_agents)
    width = 0.24
    offsets = [-width, 0, width]

    for ci, (comp, color, label) in enumerate(zip(
        ["llm_mean_ms", "sm_per_event_ms", "gov_per_llm_ms"],
        COMP_COLORS,
        COMP_LABELS,
    )):
        vals = [agg.loc[a, comp] for a in AGENTS]
        # Clip zeros to a small positive number for log scale
        vals_plot = [max(v, 0.01) for v in vals]
        bars = ax1.bar(x + offsets[ci], vals_plot, width=width * 0.85,
                       color=color, label=label, zorder=2)
        # Value annotations
        for j, (bar, v) in enumerate(zip(bars, vals)):
            if v < 0.1:
                txt = "0 ms"
                col = "#888888"
            elif v < 10:
                txt = f"{v:.2f}ms"
                col = "#333333"
            elif v < 1000:
                txt = f"{v:.0f}ms"
                col = "#333333"
            else:
                txt = f"{v/1000:.2f}s"
                col = "#333333"
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() * 1.15,
                     txt, ha="center", va="bottom", fontsize=6.5, color=col, rotation=90)

    ax1.set_yscale("log")
    ax1.set_ylim(0.008, 15_000)
    ax1.set_xticks(x)
    ax1.set_xticklabels(AGENT_LABELS, fontsize=9)
    ax1.set_ylabel("Mean per-call latency (ms, log)", fontsize=9)

    # Reference lines
    for ref_ms, label in [(1, "1 ms"), (1000, "1 s")]:
        ax1.axhline(ref_ms, color="#DDDDDD", linewidth=0.8, linestyle=":")
        ax1.text(-0.5, ref_ms * 1.1, label, fontsize=7, color="#AAAAAA")

    ax1.tick_params(labelsize=8)
    ax1.legend(loc="upper right", fontsize=8, framealpha=0.85, edgecolor="#CCCCCC")
    ax1.grid(axis="y", color="#EEEEEE", linewidth=0.6)
    for spine in ["top", "right"]:
        ax1.spines[spine].set_visible(False)

    # Ratio annotation: LLM / gov
    for j, a in enumerate(AGENTS):
        ratio = agg.loc[a, "llm_mean_ms"] / max(agg.loc[a, "gov_per_llm_ms"], 0.001)
        ax1.text(j, 5000, f"×{ratio:.0f}", ha="center", fontsize=7,
                 color="#555555", style="italic")

    ax1.text(n_agents / 2 - 0.5, 7000, "LLM / Gov ratio:", ha="center",
             fontsize=7, color="#555555", style="italic")

    # ────────────────────────────────────────────────────────────────────────────
    # Panel (b): Governance hook breakdown per agent
    # ────────────────────────────────────────────────────────────────────────────
    ax2.set_title("(b) Governance checks per LLM call\n— per hook type per agent", fontsize=10)

    hook_keys = [HOOK_SHORT[h] for h in HOOK_COLS]
    n_hooks   = len(hook_keys)
    x2 = np.arange(n_agents)
    hw = 0.14
    hook_offsets = np.linspace(-(n_hooks - 1) * hw / 2, (n_hooks - 1) * hw / 2, n_hooks)

    for hi, (h_orig, h_short) in enumerate(HOOK_SHORT.items()):
        if h_orig not in HOOK_COLS:
            continue
        ratios = [agg.loc[a, h_short + "_ratio"] for a in AGENTS]
        ax2.bar(x2 + hook_offsets[hi], ratios, width=hw * 0.85,
                color=HOOK_COLORS[h_short], label=h_short, zorder=2)

    ax2.set_xticks(x2)
    ax2.set_xticklabels(AGENT_LABELS, fontsize=9)
    ax2.set_ylabel("Checks per LLM call (ratio)", fontsize=9)
    ax2.set_ylim(0, 2.2)
    ax2.axhline(1.0, color="#888888", linewidth=0.7, linestyle="--")
    ax2.text(n_agents - 0.5, 1.02, "1× (one check/call)", ha="right", fontsize=7, color="#888888")

    ax2.tick_params(labelsize=8)
    ax2.legend(loc="upper right", fontsize=8, framealpha=0.85, edgecolor="#CCCCCC",
               title="Hook type", title_fontsize=7)
    ax2.grid(axis="y", color="#EEEEEE", linewidth=0.6)
    for spine in ["top", "right"]:
        ax2.spines[spine].set_visible(False)

    # Total governance overhead annotation
    for j, a in enumerate(AGENTS):
        gov_total = agg.loc[a, "gov_per_llm_ms"]
        ax2.text(j, 2.05, f"{gov_total:.2f} ms/call", ha="center",
                 fontsize=6.5, color="#5DBB63", fontweight="bold")

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved → {output_path}")
    print("\nPer-call summary (baseline):")
    print(agg[["llm_mean_ms", "sm_per_event_ms", "gov_per_llm_ms"]].round(3).to_string())
    print("\nHook ratios (checks per LLM call):")
    print(agg[[h + "_ratio" for h in HOOK_SHORT.values()]].round(2).to_string())

class FigurePerCallStep(PipelineStep):
    type = "figure_per_call"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="mealy_stats",
            input_default="results/mealy_stats.csv",
            output_name="fig-per-call.png",
            render=render,
        )

register_step_type("figure_per_call", FigurePerCallStep)

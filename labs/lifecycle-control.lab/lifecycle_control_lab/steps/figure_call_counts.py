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

def render(output_path: Path, trace_stats_path: Path) -> None:
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from pathlib import Path

    df = pd.read_csv(trace_stats_path)

    STACK_ORDER = [
        "baseline",
        "governance-off",
        "with-guardrail",
        "with-budget",
        "with-budget-low",
    ]
    LABELS = [
        "Baseline\n(no governance)",
        "SM + OTel\n(no policy)",
        "+ Guardrail",
        "+ Budget\n(normal)",
        "+ Budget\n(low — fires)",
    ]

    sub = df[df["scenario"].isin(STACK_ORDER)].copy()
    sub["scenario"] = pd.Categorical(sub["scenario"], categories=STACK_ORDER, ordered=True)

    stats = (
        sub.groupby("scenario", observed=True)[
            ["n_llm_calls", "n_tool_calls", "n_governance_checks", "n_governance_fired", "duration_s"]
        ]
        .agg(["mean", "std"])
        .reindex(STACK_ORDER)
    )

    # duration_per_llm_call (wall-clock / n_llm_calls, per item)
    sub["dur_per_llm"] = sub["duration_s"] / sub["n_llm_calls"].clip(lower=1)
    dur_per_llm = sub.groupby("scenario", observed=True)["dur_per_llm"].agg(["mean", "std"]).reindex(STACK_ORDER)

    # ── Colors ────────────────────────────────────────────────────────────────────
    C_LLM    = "#888888"
    C_TOOL   = "#4C9BE8"
    C_CHECK  = "#5DBB63"
    C_FIRED  = "#E05252"

    y   = np.arange(len(STACK_ORDER))
    h   = 0.22   # bar height per group
    gap = 0.27   # space between groups

    # ── Figure ────────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.0),
                                    gridspec_kw={"width_ratios": [2, 1]},
                                    constrained_layout=True)
    fig.suptitle("Call counts and per-call cost per governance scenario\n"
                 "(lifecycle-control.lab, mean ± std over 13 items)",
                 fontsize=12, fontweight="bold")

    # ── Panel (a): call counts ────────────────────────────────────────────────────
    ax1.set_title("(a) Mean call counts per item", fontsize=10)

    bar_defs = [
        ("n_llm_calls",          C_LLM,   "LLM calls",              0),
        ("n_tool_calls",          C_TOOL,  "Tool calls",              1),
        ("n_governance_checks",   C_CHECK, "Governance checks",       2),
        ("n_governance_fired",    C_FIRED, "Policy firings",          3),
    ]

    for metric, color, label, offset in bar_defs:
        means = stats[(metric, "mean")].values
        stds  = stats[(metric, "std")].values
        ypos  = y - gap * 1.5 + offset * gap
        ax1.barh(ypos, means, height=h, color=color, label=label,
                 edgecolor="white", linewidth=0.5)
        ax1.errorbar(means, ypos, xerr=stds, fmt="none",
                     ecolor="black", elinewidth=0.8, capsize=3)
        # Annotate value
        for xi, yi, v in zip(means, ypos, means):
            if v > 0.05:
                ax1.text(v + 0.3, yi, f"{v:.1f}", va="center", fontsize=7.5)

    ax1.set_yticks(y)
    ax1.set_yticklabels(LABELS, fontsize=9)
    ax1.set_xlabel("Mean count per item", fontsize=9)
    ax1.invert_yaxis()
    ax1.grid(axis="x", alpha=0.2, lw=0.5)
    ax1.legend(fontsize=8.5, loc="lower right")

    # Highlight budget-low fired bar annotation
    fired_bl = stats.loc["with-budget-low", ("n_governance_fired", "mean")]
    ybl = y[STACK_ORDER.index("with-budget-low")] - gap * 1.5 + 3 * gap
    ax1.annotate(f"⚡ {fired_bl:.1f} firings/item",
                 xy=(fired_bl, ybl), xytext=(fired_bl + 6, ybl + 0.35),
                 arrowprops=dict(arrowstyle="->", color=C_FIRED, lw=1.2),
                 fontsize=8, color=C_FIRED, fontweight="bold")

    # ── Panel (b): duration per LLM call ─────────────────────────────────────────
    ax2.set_title("(b) Wall-clock time per LLM call", fontsize=10)

    colors_b = ["#888888", "#4C9BE8", "#5DBB63", "#F5A623", "#E05252"]
    means_b = dur_per_llm["mean"].values
    stds_b  = dur_per_llm["std"].values

    bars = ax2.barh(y, means_b, height=0.5, color=colors_b,
                    edgecolor="white", linewidth=0.5)
    ax2.errorbar(means_b, y, xerr=stds_b, fmt="none",
                 ecolor="black", elinewidth=0.8, capsize=3)

    for xi, yi, v in zip(means_b, y, means_b):
        ax2.text(v + 0.03, yi, f"{v:.2f}s", va="center", fontsize=8.5, fontweight="bold")

    # Reference: baseline cost per call
    ref = dur_per_llm.loc["baseline", "mean"]
    ax2.axvline(ref, color="#888888", linestyle="--", lw=1.0, alpha=0.7,
                label=f"Baseline ({ref:.2f}s/call)")

    ax2.set_yticks(y)
    ax2.set_yticklabels(LABELS, fontsize=9)
    ax2.set_xlabel("duration_s / n_llm_calls (s)", fontsize=9)
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.2, lw=0.5)
    ax2.legend(fontsize=8, loc="lower right")

    # Annotate budget-low
    ybl2 = y[STACK_ORDER.index("with-budget-low")]
    ax2.annotate("Slower/call\n(context rebuild\nafter termination)",
                 xy=(means_b[STACK_ORDER.index("with-budget-low")], ybl2),
                 xytext=(means_b[STACK_ORDER.index("with-budget-low")] - 0.5, ybl2 + 1.2),
                 arrowprops=dict(arrowstyle="->", color=C_FIRED, lw=1.2),
                 fontsize=7.5, color=C_FIRED, ha="center")

    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved → {output_path}")

class FigureCallCountsStep(PipelineStep):
    type = "figure_call_counts"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="trace_stats",
            input_default="results/trace_stats.csv",
            output_name="fig-call-counts.png",
            render=render,
        )

register_step_type("figure_call_counts", FigureCallCountsStep)

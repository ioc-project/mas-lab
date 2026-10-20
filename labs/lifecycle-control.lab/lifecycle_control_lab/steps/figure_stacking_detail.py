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
    import matplotlib.ticker as mticker
    from pathlib import Path

    matplotlib.rcParams.update({
        "font.family": "sans-serif", "font.size": 8,
        "axes.titlesize": 8, "xtick.labelsize": 7,
    })

    ms = pd.read_csv(mealy_stats_path)
    if "agent_comm_wait_ms" not in ms.columns:
        ms["agent_comm_wait_ms"] = 0.0

    # ── Hook columns ──────────────────────────────────────────────────────────────
    HOOK_COLS   = ["gov_pre_execution_n", "gov_pre_llm_call_n", "gov_pre_tool_call_n",
                   "gov_pre_agent_comm_n", "gov_user_output_n"]
    HOOK_LABELS = ["pre_execution", "pre_llm_call", "pre_tool_call",
                   "pre_agent_comm", "user_output"]
    HOOK_COLORS = ["#5B9BD5", "#ED7D31", "#A9D18E", "#FFC000", "#9E6BB5"]

    ms["gov_checks"] = ms[HOOK_COLS].sum(axis=1)

    # ── Measure per-check cost empirically (leaf agents only) ─────────────────────
    # gov_ms_meas = execution_ms - llm_total_ms - tool_total_ms - comm_wait - sm_total_ms
    # Excludes moderator (which absorbs cross-agent wait time)
    ms["gov_ms_meas"] = (ms["execution_ms"] - ms["llm_total_ms"]
                         - ms["tool_total_ms"] - ms["agent_comm_wait_ms"]
                         - ms["sm_total_ms"])
    ms["gov_per_check_meas"] = ms["gov_ms_meas"] / ms["gov_checks"].clip(1)

    STACK_IDS = ["baseline", "governance-off", "with-guardrail", "production-like"]
    STACK_LBLS = ["Baseline\n(no policy)", "+Observability\n(hooks, no policy)",
                  "+Guardrail\n(forbidden dest.)", "Full stack\n(budget+guardrail+HITL)"]
    SC_COLORS  = {"baseline": "#AAAAAA", "governance-off": "#6BA3D9",
                  "with-guardrail": "#5DBB63", "production-like": "#E8935A"}

    # Filter: leaf agents, valid rows, nominal items only, scenarios of interest
    leaf = ms[(ms.agent_id != "moderator") &
              (ms.gov_checks > 0) &
              (ms.gov_ms_meas > 0) &
              (ms.gov_ms_meas < 50) &            # exclude outliers from async overlap
              (ms.item_group == "nominal") &
              (ms.scenario.isin(STACK_IDS))].copy()

    GOV_LAT_MEAS = leaf["gov_per_check_meas"].median()
    print(f"Measured GOV_LAT: {GOV_LAT_MEAS:.3f} ms/check  "
          f"(mean={leaf['gov_per_check_meas'].mean():.3f}, "
          f"std={leaf['gov_per_check_meas'].std():.3f})")

    # ── Per-scenario per-check stats ──────────────────────────────────────────────
    sc_stats = (leaf.groupby("scenario")["gov_per_check_meas"]
                .agg(median="median", mean="mean", std="std", q25=lambda x: x.quantile(0.25),
                     q75=lambda x: x.quantile(0.75))
                .reindex(STACK_IDS))
    print("\nPer-check cost by scenario:")
    print(sc_stats.round(3))

    # ── Hook breakdown (production-like, nominal, sum over agents per item) ────────
    pl_nom_item = (ms[(ms.scenario == "production-like") & (ms.item_group == "nominal")]
                   .groupby("item")[HOOK_COLS].sum())
    pl_nom_mean = pl_nom_item.mean()
    pl_nom_std  = pl_nom_item.std()
    pl_nom_total = pl_nom_mean.sum()
    print(f"\nHook breakdown (production-like, nominal mean/item, total={pl_nom_total:.1f}):")
    for h, l in zip(HOOK_COLS, HOOK_LABELS):
        print(f"  {l:<20}: mean={pl_nom_mean[h]:.2f}  pct={pl_nom_mean[h]/pl_nom_total*100:.1f}%")

    # Estimated total gov overhead per item (median checks × measured cost)
    pl_nom_gov_checks = ms[(ms.scenario=="production-like") & (ms.item_group=="nominal")].groupby("item")["gov_checks"].sum().mean()
    pl_nom_llm_ms = ms[(ms.scenario=="production-like") & (ms.item_group=="nominal")]["llm_total_ms"].sum() / 6
    gov_total_ms = pl_nom_gov_checks * GOV_LAT_MEAS
    gov_pct = gov_total_ms / pl_nom_llm_ms * 100
    print(f"\nEstimated total gov overhead: {pl_nom_gov_checks:.1f} checks × {GOV_LAT_MEAS:.2f}ms = {gov_total_ms:.1f}ms")
    print(f"vs mean LLM time/item: {pl_nom_llm_ms:.0f}ms  →  {gov_pct:.3f}%")

    # ── Figure ────────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.5),
                                    gridspec_kw={"width_ratios": [1.3, 1], "wspace": 0.40})

    # ─── Panel (a): Hook call breakdown per item (production-like, nominal) ───────
    # Stacked bar: mean per item. Individual item dots overlaid for each type.
    x_items = np.arange(len(pl_nom_item))
    item_ids = list(pl_nom_item.index)
    bottoms  = np.zeros(len(item_ids))

    for j, (hcol, hlbl, hcol_c) in enumerate(zip(HOOK_COLS, HOOK_LABELS, HOOK_COLORS)):
        vals = pl_nom_item[hcol].values.astype(float)
        ax1.bar(x_items, vals, bottom=bottoms, width=0.6,
                color=hcol_c, alpha=0.85, label=hlbl, zorder=2)
        bottoms += vals

    # Mean total checks line
    ax1.hlines(pl_nom_total, -0.5, len(item_ids) - 0.5, color="#333",
               linewidth=1.2, linestyle="--", zorder=3, alpha=0.6)
    ax1.text(len(item_ids) - 0.5, pl_nom_total + 0.3,
             f"mean = {pl_nom_total:.0f} checks", ha="right", va="bottom",
             fontsize=6, color="#333")

    ax1.set_xticks(x_items)
    ax1.set_xticklabels([f"{it}" for it in item_ids], fontsize=6.5, rotation=25, ha="right")
    ax1.set_ylabel("Hook invocations per item", fontsize=7.5)
    ax1.set_title("(a) Hook call breakdown per item\n(production-like, nominal)", fontsize=7.5)
    ax1.set_xlim(-0.6, len(item_ids) - 0.4)
    ax1.legend(fontsize=5.5, loc="upper right", framealpha=0.9, edgecolor="#DDD",
               ncol=1, handlelength=1.0)
    for sp in ["top", "right"]:
        ax1.spines[sp].set_visible(False)
    ax1.spines["left"].set_color("#CCCCCC")
    ax1.spines["bottom"].set_color("#CCCCCC")
    ax1.yaxis.grid(True, color="#F4F4F4", zorder=0)
    ax1.set_axisbelow(True)

    # ─── Panel (b): Measured cost per check across scenarios ──────────────────────
    # Scatter of individual leaf-agent observations + box plot + median line
    x = np.arange(len(STACK_IDS))
    rng = np.random.default_rng(7)
    bp_data = [leaf[leaf.scenario == sc]["gov_per_check_meas"].values for sc in STACK_IDS]

    bp = ax2.boxplot(bp_data, positions=x, widths=0.45, patch_artist=True,
                     medianprops={"color": "#333", "linewidth": 1.5},
                     whiskerprops={"linewidth": 0.8, "color": "#999"},
                     capprops={"linewidth": 0.8, "color": "#999"},
                     flierprops={"marker": ".", "markersize": 3, "alpha": 0.4,
                                 "markerfacecolor": "#999"})

    for patch, sc in zip(bp["boxes"], STACK_IDS):
        patch.set_facecolor(SC_COLORS[sc])
        patch.set_alpha(0.55)

    # Individual jittered dots
    for i, sc in enumerate(STACK_IDS):
        ys  = leaf[leaf.scenario == sc]["gov_per_check_meas"].values
        jit = rng.uniform(-0.12, 0.12, len(ys))
        ax2.scatter(x[i] + jit, ys, color=SC_COLORS[sc], s=12,
                    alpha=0.55, zorder=3, linewidths=0)

    # Global median reference line
    ax2.axhline(GOV_LAT_MEAS, color="#555", linewidth=1.1, linestyle="--",
                zorder=1, alpha=0.8)
    ax2.text(len(STACK_IDS) - 0.5, GOV_LAT_MEAS + 0.05,
             f"median = {GOV_LAT_MEAS:.2f} ms/check",
             ha="right", va="bottom", fontsize=6, color="#555")

    # Overhead annotation
    ax2.text(0.03, 0.97,
             f"{pl_nom_gov_checks:.0f} checks × {GOV_LAT_MEAS:.2f}ms\n"
             f"= {gov_total_ms:.0f}ms total gov overhead\n"
             f"= {gov_pct:.2f}% of LLM time/item",
             transform=ax2.transAxes, ha="left", va="top",
             fontsize=5.8, color="#444", style="italic",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="#FFFEF0",
                       edgecolor="#DDD", alpha=0.9))

    ax2.set_xticks(x)
    ax2.set_xticklabels(STACK_LBLS, fontsize=6.2)
    ax2.set_ylabel("Measured cost per check (ms)", fontsize=7.5)
    ax2.set_title("(b) Per-check cost across scenarios\n(leaf agents, nominal items)", fontsize=7.5)
    ax2.set_xlim(-0.6, len(STACK_IDS) - 0.4)
    ax2.set_ylim(0, ax2.get_ylim()[1])

    for sp in ["top", "right"]:
        ax2.spines[sp].set_visible(False)
    ax2.spines["left"].set_color("#CCCCCC")
    ax2.spines["bottom"].set_color("#CCCCCC")
    ax2.yaxis.grid(True, color="#F4F4F4", zorder=0)
    ax2.set_axisbelow(True)

    fig.suptitle(
        "Fig 2b — Governance hook overhead: call counts and per-call cost\n"
        "Left: which lifecycle events trigger hooks. "
        "Right: empirically measured cost per hook call (~1.2 ms, constant across scenarios).",
        fontsize=7.5, y=1.03, color="#333",
    )

    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    print(f"\nSaved → {output_path}")

class FigureStackingDetailStep(PipelineStep):
    type = "figure_stacking_detail"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="mealy_stats",
            input_default="results/mealy_stats.csv",
            output_name="fig-stacking-detail.png",
            render=render,
        )

register_step_type("figure_stacking_detail", FigureStackingDetailStep)

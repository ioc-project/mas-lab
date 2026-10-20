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
    import matplotlib.colors as mcolors
    from pathlib import Path

    df = pd.read_csv(trace_stats_path)

    SCENARIOS = [
        ("baseline",        "Baseline"),
        ("governance-off",  "Gov-Off"),
        ("with-guardrail",  "+Guardrail"),
        ("with-budget",     "+Budget"),
        ("with-budget-low", "+Budget-Low"),
    ]
    SCEN_KEYS   = [s for s, _ in SCENARIOS]
    SCEN_LABELS = [l for _, l in SCENARIOS]

    # ── Filter to 5 stacking scenarios ──────────────────────────────────────────
    df5 = df[df["scenario"].isin(SCEN_KEYS)].copy()
    df5["fired"] = (df5["n_governance_fired"] > 0).astype(int)

    # ── Item ordering: fault first, then guardrail, then nominal ─────────────────
    GROUP_ORDER = ["fault", "guardrail", "nominal"]
    GROUP_COLOR = {"fault": "#E05252", "guardrail": "#F5A623", "nominal": "#4C9BE8"}
    GROUP_HATCH = {"fault": "///", "guardrail": "...", "nominal": ""}

    item_meta = (
        df5.drop_duplicates("item")[["item", "item_group"]]
        .assign(group_rank=lambda d: d["item_group"].map({g: i for i, g in enumerate(GROUP_ORDER)}))
        .sort_values(["group_rank", "item"])
    )
    ITEM_ORDER = item_meta["item"].tolist()
    ITEM_GROUPS = item_meta.set_index("item")["item_group"].to_dict()

    # ── Firing heatmap matrix ────────────────────────────────────────────────────
    heat = df5.pivot_table(index="item", columns="scenario", values="fired", aggfunc="max")
    heat = heat.reindex(index=ITEM_ORDER, columns=SCEN_KEYS).fillna(0)

    # ── Precision / Recall ───────────────────────────────────────────────────────
    pr_rows = []
    for scen, label in SCENARIOS:
        sub     = df5[df5["scenario"] == scen]
        fault   = sub[sub["item_group"] == "fault"]
        nominal = sub[sub["item_group"] == "nominal"]
        tp = int((fault["fired"] > 0).sum())
        fp = int(((sub["item_group"] != "fault") & (sub["fired"] > 0)).sum())
        fn = int((fault["fired"] == 0).sum())
        tn = int((nominal["fired"] == 0).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        pr_rows.append({
            "scenario": scen, "label": label,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall,
        })
    pr_df = pd.DataFrame(pr_rows)

    # ── Scenario colours ─────────────────────────────────────────────────────────
    SCEN_COLORS = {
        "baseline":        "#888888",
        "governance-off":  "#AAAAAA",
        "with-guardrail":  "#5DBB63",
        "with-budget":     "#4C9BE8",
        "with-budget-low": "#E05252",
    }
    SCEN_MARKERS = {
        "baseline":        "x",
        "governance-off":  "x",
        "with-guardrail":  "o",
        "with-budget":     "s",
        "with-budget-low": "^",
    }

    # ── Figure ────────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        1, 2,
        figsize=(13, 5.2),
        gridspec_kw={"width_ratios": [1.6, 1]},
    )
    fig.suptitle(
        "Fig 2c — Governance trigger fidelity: fires exactly when it should, never otherwise\n"
        "(GT: item_group='fault' = positive; 'nominal' = negative; 'guardrail' = excluded)",
        fontsize=9.5, fontweight="bold",
    )

    # ── Panel (a): Firing heatmap ────────────────────────────────────────────────
    ax1.set_title("(a) Governance fired — binary per (scenario, item)", fontsize=10)

    NROW = len(ITEM_ORDER)
    NCOL = len(SCEN_KEYS)

    for row_i, item in enumerate(ITEM_ORDER):
        grp = ITEM_GROUPS[item]
        # row background (light tint by group)
        bg = {"fault": "#FFF0F0", "guardrail": "#FFF8EC", "nominal": "#F0F6FF"}[grp]
        ax1.axhspan(row_i - 0.5, row_i + 0.5, color=bg, zorder=0)

    for col_j, scen in enumerate(SCEN_KEYS):
        for row_i, item in enumerate(ITEM_ORDER):
            val  = heat.loc[item, scen]
            grp  = ITEM_GROUPS[item]
            fc   = GROUP_COLOR[grp] if val else "white"
            ec   = GROUP_COLOR[grp] if val else "#CCCCCC"
            rect = plt.Rectangle(
                (col_j - 0.45, row_i - 0.45), 0.90, 0.90,
                facecolor=fc, edgecolor=ec, linewidth=0.8, zorder=1,
            )
            ax1.add_patch(rect)
            if val:
                ax1.text(col_j, row_i, "✓", ha="center", va="center",
                         fontsize=9, color="white", fontweight="bold", zorder=2)

    # Axes
    ax1.set_xlim(-0.5, NCOL - 0.5)
    ax1.set_ylim(-0.5, NROW - 0.5)
    ax1.set_xticks(range(NCOL))
    ax1.set_xticklabels(SCEN_LABELS, fontsize=8.5)
    ax1.set_yticks(range(NROW))
    ytick_labels = ax1.set_yticklabels(ITEM_ORDER, fontsize=7.5)
    # Color each y-tick label by group
    for lbl, item in zip(ytick_labels, ITEM_ORDER):
        lbl.set_color(GROUP_COLOR[ITEM_GROUPS[item]])
    ax1.invert_yaxis()

    # Group separators
    prev_grp = None
    for row_i, item in enumerate(ITEM_ORDER):
        grp = ITEM_GROUPS[item]
        if grp != prev_grp and row_i > 0:
            ax1.axhline(row_i - 0.5, color="#888888", linewidth=0.8, linestyle="--", zorder=3)
        prev_grp = grp

    ax1.tick_params(axis="both", which="both", length=0)
    for spine in ax1.spines.values():
        spine.set_visible(False)

    # ── Panel (b): Precision / Recall ────────────────────────────────────────────
    ax2.set_title("(b) Precision / Recall\n(GT: fault = positive, nominal = negative)", fontsize=10)

    # Ideal corner annotation
    ax2.axhline(1.0, color="#DDDDDD", linewidth=0.8, linestyle=":")
    ax2.axvline(1.0, color="#DDDDDD", linewidth=0.8, linestyle=":")
    ax2.annotate("ideal", xy=(1.01, 1.01), fontsize=7, color="#AAAAAA", ha="left")

    for _, row in pr_df.iterrows():
        s = row["scenario"]
        r = row["recall"]
        p = row["precision"]
        c = SCEN_COLORS[s]
        m = SCEN_MARKERS[s]

        if np.isnan(p):
            # Never fired — show on x-axis baseline
            ax2.scatter(r, -0.05, s=80, color=c, marker=m, zorder=3,
                        clip_on=False)
            ax2.annotate(
                row["label"],
                xy=(r, -0.05), xytext=(r - 0.03, -0.14),
                fontsize=7, color=c, ha="center",
                arrowprops=dict(arrowstyle="-", color=c, lw=0.5),
            )
        else:
            ax2.scatter(r, p, s=100, color=c, marker=m, zorder=3)
            offset = {"with-guardrail": (0.03, 0.03), "with-budget-low": (-0.03, -0.08)}.get(s, (0.02, 0.02))
            ax2.annotate(
                row["label"],
                xy=(r, p),
                xytext=(r + offset[0], p + offset[1]),
                fontsize=8, color=c, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color=c, lw=0.6),
            )

    ax2.set_xlabel("Recall  (fault items covered)", fontsize=9)
    ax2.set_ylabel("Precision  (no false positives)", fontsize=9)
    ax2.set_xlim(-0.1, 1.15)
    ax2.set_ylim(-0.2, 1.12)
    ax2.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax2.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax2.tick_params(labelsize=8)
    ax2.set_aspect("equal")
    ax2.grid(True, color="#EEEEEE", linewidth=0.6)
    for spine in ["top", "right"]:
        ax2.spines[spine].set_visible(False)

    # P/R table inset
    table_y = -0.18
    table_data = []
    for _, row in pr_df.iterrows():
        p_str = f"{row['precision']:.2f}" if not np.isnan(row["precision"]) else "—"
        r_str = f"{row['recall']:.2f}"
        table_data.append(f"{row['label']:>12s}: P={p_str}  R={r_str}  TP={row['tp']} FP={row['fp']}")

    # Legend
    handles = [
        mpatches.Patch(facecolor=GROUP_COLOR["fault"],     label="fault items (GT positive)"),
        mpatches.Patch(facecolor=GROUP_COLOR["guardrail"], label="guardrail items (GT excluded)"),
        mpatches.Patch(facecolor=GROUP_COLOR["nominal"],   label="nominal items (GT negative)"),
    ]
    ax1.legend(handles=handles, loc="lower left", fontsize=7.5,
               framealpha=0.85, edgecolor="#CCCCCC")

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved → {output_path}")
    print("\nPrecision/Recall summary:")
    for _, row in pr_df.iterrows():
        p = f"{row['precision']:.2f}" if not np.isnan(row["precision"]) else " — "
        print(f"  {row['label']:>14s}: P={p}  R={row['recall']:.2f}  "
              f"TP={row['tp']} FP={row['fp']} FN={row['fn']} TN={row['tn']}")

class FigurePrecisionRecallDetailStep(PipelineStep):
    type = "figure_precision_recall_detail"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="trace_stats",
            input_default="results/trace_stats.csv",
            output_name="fig-precision-recall-detail.png",
            render=render,
        )

register_step_type("figure_precision_recall_detail", FigurePrecisionRecallDetailStep)

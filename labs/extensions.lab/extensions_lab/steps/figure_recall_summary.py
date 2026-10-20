#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FigureRecallSummaryStep — 2-panel headline results figure.

Reads ``fact_recall_summary.csv`` (produced by EvalFactRecallStep) and draws:

  Panel (A) — Recall by scenario × query-group
      Grouped bar chart, dodged, one bar per (scenario, group) pair.
      k0 excluded (trivially perfect for all).
      Key question answered: which overlay achieves highest recall on
      multi-fact (k2) queries?

  Panel (B) — Precision by scenario × query-group
      Same structure as (A).
      Reveals the precision cost paid for recall gains (bigk trades
      precision for recall; multistep attempts to keep both).

Together with figure_smoke_evidence (3-panel), these two figures constitute
the complete scientific story for the vector-memory overlay smoke test.

Configuration
-------------
summary_csv   str   Path to fact_recall_summary.csv.
                    Default: "results/fact_recall_summary.csv"
output        str   Output PNG path.
                    Default: "results/fig_recall_summary.png"
dpi           int   PNG DPI. Default: 150
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_input_path,
)
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)

SCENARIOS = ["with-letta-factrecall", "vector-baseline", "vector-multistep", "vector-bigk"]
COLORS = {
    "with-letta-factrecall": "#805ad5",
    "vector-baseline":  "#2b6cb0",
    "vector-multistep": "#dd6b20",
    "vector-bigk":      "#38a169",
}
LABELS = {
    "with-letta-factrecall": "Letta-core\n(inject, k=\u221e)",
    "vector-baseline":  "RAG-direct\n(k=6)",
    "vector-multistep": "RAG-decomposed\n(k=6, multi-query)",
    "vector-bigk":      "RAG-wide\n(k=20)",
}
GROUPS = ["k1", "k2", "k3"]


def _load_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open()))


class FigureRecallSummaryStep(PipelineStep):
    """2-panel headline results figure: Recall and Precision per scenario × group."""

    type = "figure_recall_summary"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np

        cfg = self.config
        output_dir = ctx.output_dir

        summary_csv = resolve_step_input_path(ctx, self.name, cfg, "summary_csv", "results/fact_recall_summary.csv")
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "fig_recall_summary.png", cfg,
            key="output", default_subdir="results", name_keys=(), mkdir=True,
        )
        dpi = int(cfg.get("dpi", 150))

        rows = _load_csv(summary_csv)
        if not rows:
            raise RuntimeError(f"No rows in {summary_csv} — run eval-fact-recall first")

        # ── index: (scenario, group) → {precision_mean, recall_mean, precision_std, recall_std}
        index: Dict[tuple, Dict[str, float]] = {}
        for r in rows:
            scen  = r["scenario"]
            grp   = r["group"]
            if grp not in GROUPS:
                continue
            index[(scen, grp)] = {
                "precision": float(r.get("precision_mean") or 0),
                "recall":    float(r.get("recall_mean")    or 0),
                "precision_std": float(r.get("precision_std") or 0),
                "recall_std":    float(r.get("recall_std")    or 0),
                "n_runs":    int(r.get("n_runs") or 1),
            }

        # ── layout ───────────────────────────────────────────────────────
        fig, (axR, axP) = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
        fig.subplots_adjust(wspace=0.08)

        n_scenarios = len(SCENARIOS)
        width       = 0.18
        x           = np.arange(len(GROUPS))  # one cluster per group

        for panel_idx, (ax, metric, title) in enumerate([
            (axR, "recall",    "(A) Recall  (|retrieved ∩ GT| / |GT|)"),
            (axP, "precision", "(B) Precision  (|retrieved ∩ GT| / |retrieved|)"),
        ]):
            for i, scen in enumerate(SCENARIOS):
                vals = [index.get((scen, g), {}).get(metric, 0) for g in GROUPS]
                errs = [index.get((scen, g), {}).get(f"{metric}_std", 0) for g in GROUPS]
                n_runs = [index.get((scen, g), {}).get("n_runs", 1) for g in GROUPS]
                offset = (i - (n_scenarios - 1) / 2) * width
                bars = ax.bar(
                    x + offset, vals, width,
                    color=COLORS[scen], label=LABELS[scen],
                    zorder=3, alpha=0.90, edgecolor="white", linewidth=0.7,
                )
                # error bars only when n_runs > 1 (std is meaningful)
                for xi, (v, e, n) in enumerate(zip(vals, errs, n_runs)):
                    if n > 1 and e > 0:
                        ax.errorbar(
                            x[xi] + offset, v, yerr=e,
                            fmt="none", ecolor="black", elinewidth=1.2, capsize=3,
                        )
                    # value annotation
                    ax.text(
                        x[xi] + offset, v + 0.03, f"{v:.2f}",
                        ha="center", va="bottom", fontsize=7.5, fontweight="bold",
                        color="black",
                    )

            ax.set_xticks(x)
            ax.set_xticklabels([f"k{g[1]} queries" for g in GROUPS], fontsize=10)
            ax.set_ylim(0, 1.32)
            ax.set_yticks([0, 0.25, 0.50, 0.75, 1.0])
            ax.set_yticklabels(["0", "0.25", "0.50", "0.75", "1.0"], fontsize=9)
            ax.yaxis.set_tick_params(left=(panel_idx == 0), labelleft=(panel_idx == 0))
            ax.axhline(1.0, color="black", lw=0.7, alpha=0.25, zorder=1)
            ax.grid(True, axis="y", alpha=0.25, zorder=0)
            ax.set_title(title, fontsize=10, fontweight="bold", pad=8)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

        axR.set_ylabel("Score (n=1 run)", fontsize=9)

        # shared legend below both panels
        handles = [
            mpatches.Patch(facecolor=COLORS[s], label=LABELS[s])
            for s in SCENARIOS
        ]
        fig.legend(
            handles=handles,
            loc="lower center",
            ncol=4,
            fontsize=8.5,
            frameon=False,
            bbox_to_anchor=(0.5, -0.08),
        )

        fig.suptitle(
            "Memory backend comparison \u2014 Lab 3 smoke test (n=1 run)\n"
            "Same agent \u00b7 same dataset \u00b7 same store \u00b7 different overlay",
            fontsize=11,
            y=1.02,
        )

        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        logger.info("FigureRecallSummaryStep: wrote %s", output_path)

        return StepOutput(
            data={"output": str(output_path)},
            files=[output_path],
            metadata={"output": str(output_path)},
        )


register_step_type("figure_recall_summary", FigureRecallSummaryStep)

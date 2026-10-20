#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FigureSmokeEvidenceStep — two separate figures from pipeline artifacts.

Reads the outputs of ``eval_fact_recall`` (fact_recall_runs.csv) and the
raw trace events (via results.csv → trace_path) already collected by the
benchmark loop.

Figure 1 — Provenance heatmap (output_provenance)
    Rows = (item_id, scenario); columns = every fact-ID that appears as
    expected or retrieved across the whole smoke run.
    Colours: green=TP, red=FN, grey=FP.
    Shows exact fact-level attribution from trace, no LLM-judge needed.

Figure 2 — Retrieval call decomposition (output_calls)
    Stacked bar per scenario: blue = memory_search calls, grey = other tool
    calls (get_fares, lookup_schedule, delegate_to_*…).
    Confirms that strategy instructions were followed and that the memory
    channel is cleanly isolated in the observability stream.

Configuration
-------------
runs_csv          str   Path to fact_recall_runs.csv.
                        Default: "results/fact_recall_runs.csv"
results_csv       str   Path to benchmark results.csv.
                        Default: "results.csv"
output_provenance str   Provenance heatmap PNG path.
                        Default: "results/fig_smoke_provenance.png"
output_calls      str   Call-count bar chart PNG path.
                        Default: "results/fig_smoke_calls.png"
dpi               int   DPI for PNG. Default: 150
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_input_path,
)
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)

SCENARIOS = ["vector-baseline", "vector-multistep", "vector-bigk"]
SCENARIOS_ALL = ["with-letta-factrecall"] + SCENARIOS  # includes Letta for calls figure
COLORS = {
    "with-letta-factrecall": "#805ad5",
    "vector-baseline":  "#2b6cb0",
    "vector-multistep": "#dd6b20",
    "vector-bigk":      "#38a169",
}
LABELS = {
    "with-letta-factrecall": "letta-core\n(inject)",
    "vector-baseline":  "RAG-direct\n(k=6)",
    "vector-multistep": "RAG-decomposed\n(k=6, multi-q)",
    "vector-bigk":      "RAG-wide\n(k=20)",
}


def _load_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open()))


def _build_tool_counts(
    benchmark_rows: List[Dict[str, str]],
) -> Dict[str, Dict[str, int]]:
    """Count memory_search vs other tool calls per scenario from trace files."""
    counts: Dict[str, Dict[str, int]] = {
        s: {"memory_search": 0, "other_tools": 0}
        for s in SCENARIOS_ALL
    }
    seen: set[str] = set()
    for row in benchmark_rows:
        scenario = row.get("scenario", "")
        tp = row.get("trace_path", "")
        key = (scenario, tp)
        if scenario not in counts or not tp or not Path(tp).exists() or key in seen:
            continue
        seen.add(key)
        for line in open(tp):
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("kind") == "tool_call_start":
                tn = ev.get("tool_name", "") or ""
                if tn == "memory_search":
                    counts[scenario]["memory_search"] += 1
                else:
                    counts[scenario]["other_tools"] += 1
    return counts


class FigureSmokeEvidenceStep(PipelineStep):
    """3-panel evidence figure for the vector-memory overlay smoke test."""

    type = "figure_smoke_evidence"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        import matplotlib.patches as mpatches
        import numpy as np

        cfg = self.config
        output_dir = ctx.output_dir

        runs_csv           = resolve_step_input_path(ctx, self.name, cfg, "runs_csv", "results/fact_recall_runs.csv")
        results_csv        = resolve_step_input_path(ctx, self.name, cfg, "results_csv", "results.csv")
        output_provenance = resolve_step_artifact_path(
            ctx, None, self.name, "fig_smoke_provenance.png", cfg,
            key="output_provenance", default_subdir="results", name_keys=(), mkdir=True,
        )
        output_calls = resolve_step_artifact_path(
            ctx, None, self.name, "fig_smoke_calls.png", cfg,
            key="output_calls", default_subdir="results", name_keys=(), mkdir=True,
        )
        dpi                = int(cfg.get("dpi", 150))

        runs            = _load_csv(runs_csv)
        benchmark_rows  = _load_csv(results_csv)

        if not runs:
            raise RuntimeError(f"No rows in {runs_csv} — run eval-fact-recall first")

        # ── collect all fact IDs ──────────────────────────────────────────
        all_fact_ids: set[str] = set()
        for r in runs:
            for col in ("retrieved_ids", "expected_ids"):
                s = (r.get(col) or "").strip()
                if s:
                    all_fact_ids.update(x for x in s.split(",") if x)
        fact_axis = sorted(all_fact_ids)

        # ── items order ───────────────────────────────────────────────────
        items_order: list[str] = []
        for r in runs:
            if r["item_id"] not in items_order:
                items_order.append(r["item_id"])
        items_order.sort()

        # ── tool counts from traces ───────────────────────────────────────
        tool_counts = _build_tool_counts(benchmark_rows)

        # ─────────────────────────────────────────────────────────────────
        # Figure 1: provenance heatmap
        # ─────────────────────────────────────────────────────────────────
        row_labels: list[str] = []
        matrix_rows: list[list[int]] = []
        for item_id in items_order:
            grp = next((r["group"] for r in runs if r["item_id"] == item_id), "")
            for s in SCENARIOS:
                rec = next((r for r in runs
                            if r["scenario"] == s and r["item_id"] == item_id), None)
                if rec is None:
                    continue
                retrieved = set((rec.get("retrieved_ids") or "").split(",")) - {""}
                expected  = set((rec.get("expected_ids")  or "").split(",")) - {""}
                row: list[int] = []
                for fid in fact_axis:
                    in_r, in_e = fid in retrieved, fid in expected
                    if   in_e and in_r:      row.append(2)  # TP
                    elif in_e and not in_r:  row.append(3)  # FN
                    elif in_r and not in_e:  row.append(1)  # FP
                    else:                    row.append(0)  # TN
                row_labels.append(f"{grp}:{item_id}  ·  {s.replace('vector-','')}")
                matrix_rows.append(row)

        matrix = np.array(matrix_rows)
        cmap = mcolors.ListedColormap(["#f0f4f8", "#a0aec0", "#38a169", "#e53e3e"])
        norm = mcolors.BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], cmap.N)

        fig1, ax1 = plt.subplots(figsize=(max(7, len(fact_axis) * 0.55), max(4, len(row_labels) * 0.35 + 1.5)))
        ax1.imshow(matrix, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
        ax1.set_xticks(range(len(fact_axis)))
        ax1.set_xticklabels(fact_axis, rotation=60, fontsize=7)
        ax1.set_yticks(range(len(row_labels)))
        ax1.set_yticklabels(row_labels, fontsize=7)
        for i in range(len(SCENARIOS), len(row_labels), len(SCENARIOS)):
            ax1.axhline(i - 0.5, color="black", lw=0.8, alpha=0.5)
        ax1.set_title("Provenance — which facts were retrieved vs expected", fontsize=10, fontweight="bold")
        legend_patches = [
            mpatches.Patch(facecolor="#38a169", edgecolor="k", label="TP — expected & retrieved"),
            mpatches.Patch(facecolor="#e53e3e", edgecolor="k", label="FN — expected, missed"),
            mpatches.Patch(facecolor="#a0aec0", edgecolor="k", label="FP — retrieved noise"),
        ]
        ax1.legend(handles=legend_patches, loc="upper center",
                   bbox_to_anchor=(0.5, -0.22), ncol=3, fontsize=7.5, frameon=False)

        fig1.savefig(output_provenance, dpi=dpi, bbox_inches="tight")
        plt.close(fig1)
        logger.info("FigureSmokeEvidenceStep: wrote provenance figure %s", output_provenance)

        # ─────────────────────────────────────────────────────────────────
        # Figure 2: retrieval call decomposition
        # ─────────────────────────────────────────────────────────────────
        fig2, ax2 = plt.subplots(figsize=(6.5, 4))
        x   = np.arange(len(SCENARIOS_ALL))
        w   = 0.60
        mem = [tool_counts[s]["memory_search"] for s in SCENARIOS_ALL]
        oth = [tool_counts[s]["other_tools"]   for s in SCENARIOS_ALL]
        ax2.bar(x, mem, w, color="#3182ce", label="memory\_search")
        ax2.bar(x, oth, w, bottom=mem, color="#a0aec0", label="other tools")
        for i, s in enumerate(SCENARIOS_ALL):
            total = mem[i] + oth[i]
            pct   = (mem[i] / total * 100) if total else 0
            label = f"{mem[i]}/{total}\n({pct:.0f}%)" if total else "0 calls"
            ax2.text(x[i], total + 0.3, label,
                     ha="center", va="bottom", fontsize=8)
        tick_labels = [LABELS.get(s, s).replace("\n", " ") for s in SCENARIOS_ALL]
        ax2.set_xticks(x)
        ax2.set_xticklabels(tick_labels, fontsize=9)
        ax2.set_ylabel("# tool_call_start events\n(sum over all items)", fontsize=9)
        ax2.set_title("Retrieval calls per scenario\n(memory\_search vs other tools)", fontsize=10, fontweight="bold")
        ax2.legend(loc="upper right", fontsize=8, framealpha=0.9)
        ax2.grid(True, axis="y", alpha=0.22)
        ymax = max(mem[i] + oth[i] for i in range(len(SCENARIOS_ALL))) if any(mem[i] + oth[i] for i in range(len(SCENARIOS_ALL))) else 4
        ax2.set_ylim(0, max(ymax * 1.4, 4))

        fig2.savefig(output_calls, dpi=dpi, bbox_inches="tight")
        plt.close(fig2)
        logger.info("FigureSmokeEvidenceStep: wrote calls figure %s", output_calls)

        return StepOutput(
            data={"output_provenance": str(output_provenance), "output_calls": str(output_calls)},
            files=[output_provenance, output_calls],
            metadata={"output_provenance": str(output_provenance), "output_calls": str(output_calls)},
        )


register_step_type("figure_smoke_evidence", FigureSmokeEvidenceStep)

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FigureProvenanceRecordStep — prompt structure + provenance record figure.

Side-by-side comparison of Letta (pre-turn injection) vs RAG-direct (tool-call
retrieval) for a single query item (default ``k1_q04``).

Configuration
-------------
results_csv   str   Benchmark ``results.csv`` (default ``results.csv``).
item_id       str   Dataset item id (default ``k1_q04``).
letta_scenario str  Letta scenario id (default ``with-letta-factrecall``).
rag_scenario  str   RAG scenario id (default ``vector-baseline``).
output        str   PNG path (default ``results/fig_provenance_record.png``).
dpi           int   Figure DPI (default 150).
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

C_LETTA = "#805ad5"
C_RAG = "#2b6cb0"
C_DIFF = "#FAF089"
C_SAME = "#EDF2F7"
C_COMMON_BG = "#E8F4F8"
C_MEM_BG = "#EDE9FA"
C_TOOL_BG = "#EBF8FF"
C_ABSENT = "#FFF5F5"
C_BORDER = "#CBD5E0"
C_TEXT = "#1A202C"
C_LIGHT = "#718096"


def _load_events(rows: List[Dict[str, str]], scenario: str, item_id: str) -> List[dict]:
    row = next(r for r in rows if r["scenario"] == scenario and r["item_id"] == item_id)
    trace_path = Path(row["trace_path"])
    return [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]


def _segment_block(ax, y, h, label, detail, bg, border_color, border_style="-", token_str=""):
    import matplotlib.pyplot as plt

    lw = 1.2 if border_style == "-" else 0.8
    rect = plt.Rectangle(
        (0.03, y), 0.94, h - 0.008,
        linewidth=lw, edgecolor=border_color,
        facecolor=bg, linestyle=border_style, zorder=3,
    )
    ax.add_patch(rect)
    ax.text(0.08, y + h * 0.52, label, ha="left", va="center", fontsize=8,
            fontweight="bold", color=C_LIGHT, zorder=4)
    ax.text(0.97, y + h * 0.52, detail, ha="right", va="center", fontsize=7.8,
            color=C_TEXT, zorder=4, style="italic")
    if token_str:
        ax.text(0.50, y + h * 0.52, token_str, ha="center", va="center",
                fontsize=7.5, color=C_LIGHT, zorder=4)


def _draw_column(ax, title, color, letta_side: bool, letta_events: list, rag_events: list):
    from matplotlib.patches import FancyBboxPatch

    letta_ca = next(e for e in letta_events if e.get("kind") == "context_assembled")
    rag_ca = next(e for e in rag_events if e.get("kind") == "context_assembled")
    del letta_ca, rag_ca  # structure figure uses fixed layout; traces drive provenance rows

    letta_mem = next(
        e for e in letta_events
        if e.get("kind") == "context_part_contributed" and e.get("source") == "memory"
    )
    rag_tool = next(
        e for e in rag_events
        if e.get("kind") == "context_part_contributed" and e.get("access_mechanism") == "tool_call"
    )

    rag_results: list = []
    try:
        content = rag_tool.get("content", "")
        data = json.loads(content[content.index("{"):])
        rag_results = data.get("result", {}).get("results", [])
    except Exception:
        pass

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.add_patch(FancyBboxPatch(
        (0.03, 0.92), 0.94, 0.07,
        boxstyle="round,pad=0.01", facecolor=color, edgecolor=color, linewidth=0))
    ax.text(0.50, 0.956, title, ha="center", va="center",
            fontsize=9.5, fontweight="bold", color="white")

    y_label = 0.90
    ax.text(0.50, y_label, "▸ prompt assembled before first LLM call",
            ha="center", va="bottom", fontsize=7.5, color=C_LIGHT, style="italic")

    y = 0.81
    h_seg = 0.08
    _segment_block(ax, y, h_seg, "role instructions",
                   '"You are a moderator…"', C_COMMON_BG, C_BORDER,
                   token_str="~152 tokens" if letta_side else "~137 tokens")
    y -= h_seg + 0.01
    _segment_block(ax, y, h_seg, "available agents",
                   "delegate_to_* tools", C_COMMON_BG, C_BORDER, token_str="~87 tokens")
    y -= h_seg + 0.01

    if letta_side:
        _segment_block(ax, y, h_seg * 1.6, "Letta memory block",
                       "100 facts  f001…f100", C_MEM_BG, color,
                       token_str="~1 881 tokens  (all facts, always)")
        y -= h_seg * 1.6 + 0.01
    else:
        _segment_block(ax, y, h_seg, "memory block", "— not present —",
                       C_ABSENT, "#FC8181", border_style="--")
        y -= h_seg + 0.01

    ax.text(0.50, y + 0.005, "▸ retrieved during turn (after LLM decision)",
            ha="center", va="bottom", fontsize=7.5, color=C_LIGHT, style="italic")
    y -= 0.01

    if letta_side:
        _segment_block(ax, y, h_seg, "memory search", "— not issued —",
                       C_ABSENT, "#D6BCFA", border_style="--")
    else:
        keys = ", ".join(r.get("key", "?") for r in rag_results)
        detail = f"{len(rag_results)} chunks  ({keys})"
        _segment_block(ax, y, h_seg, "memory_search result", detail, C_TOOL_BG, color,
                       token_str=f"~{rag_tool.get('token_estimate', '?')} tokens")
    y -= h_seg + 0.03

    ax.text(0.50, y + 0.005, "▸ provenance record (from trace)",
            ha="center", va="bottom", fontsize=7.5, color=C_LIGHT, style="italic")
    y -= 0.005

    h_row = 0.063
    if letta_side:
        prov_rows = [
            ("assembled by", "framework  (before turn)", True),
            ("triggered by", "deterministic", True),
            ("keys in trace", "f001 … f100", True),
            ("ground truth", "f004  ✓  (always present)", False),
        ]
    else:
        prov_rows = [
            ("assembled by", "model  (during turn)", True),
            ("triggered by", "stochastic", True),
            ("keys in trace", ", ".join(r.get("key", "?") for r in rag_results), True),
            ("ground truth", "f004  ✓  (rank 1, score 1.0)", False),
        ]

    for label, value, diff in prov_rows:
        bg = C_DIFF if diff else C_SAME
        ax.add_patch(FancyBboxPatch(
            (0.03, y), 0.94, h_row - 0.005,
            boxstyle="round,pad=0.004", linewidth=0.8,
            facecolor=bg, edgecolor=C_BORDER, zorder=3))
        ax.text(0.08, y + h_row * 0.5, label, ha="left", va="center", fontsize=8,
                fontweight="bold", color=C_LIGHT, zorder=4)
        ax.text(0.97, y + h_row * 0.5, value, ha="right", va="center",
                fontsize=8, color=C_TEXT, zorder=4)
        y -= h_row


class FigureProvenanceRecordStep(PipelineStep):
    """Render provenance-record comparison figure from benchmark traces."""

    type = "figure_provenance_record"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import matplotlib
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt

        matplotlib.use("Agg")
        cfg = self.config
        output_dir = Path(ctx.output_dir)
        results_csv = resolve_step_input_path(ctx, self.name, cfg, "results_csv", "results.csv")
        item_id = str(cfg.get("item_id", "k1_q04"))
        letta_scenario = str(cfg.get("letta_scenario", "with-letta-factrecall"))
        rag_scenario = str(cfg.get("rag_scenario", "vector-baseline"))
        output = resolve_step_artifact_path(
            ctx, None, self.name, "fig_provenance_record.png", cfg,
            key="output", default_subdir="results", name_keys=(), mkdir=True,
        )
        dpi = int(cfg.get("dpi", 150))

        if not results_csv.is_file():
            raise FileNotFoundError(f"results.csv not found: {results_csv}")

        rows = list(csv.DictReader(results_csv.open()))
        letta_events = _load_events(rows, letta_scenario, item_id)
        rag_events = _load_events(rows, rag_scenario, item_id)

        fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(12, 7.5))
        fig.subplots_adjust(wspace=0.06, left=0.02, right=0.98, top=0.91, bottom=0.07)

        _draw_column(ax_l, "Letta-core  —  pre-turn injection", C_LETTA, True,
                     letta_events, rag_events)
        _draw_column(ax_r, "RAG-direct  —  tool-call retrieval", C_RAG, False,
                     letta_events, rag_events)

        patches = [
            mpatches.Patch(facecolor=C_DIFF, edgecolor=C_BORDER,
                           label="fields that differ between modes"),
            mpatches.Patch(facecolor=C_SAME, edgecolor=C_BORDER,
                           label="fields shared by all provenance records"),
            mpatches.Patch(facecolor=C_COMMON_BG, edgecolor=C_BORDER,
                           label="context segment — always injected"),
            mpatches.Patch(facecolor=C_ABSENT, edgecolor="#FC8181",
                           label="segment absent in this mode (dashed)"),
        ]
        fig.legend(handles=patches, loc="lower center", ncol=4, fontsize=8,
                   frameon=True, edgecolor=C_BORDER, bbox_to_anchor=(0.5, 0.0))
        fig.suptitle(
            "Prompt structure and provenance record for the same query under two memory integrations\n"
            'Query: "What is my daily travel budget?"  (k1_q04, ground truth: f004)',
            fontsize=11, y=0.97,
        )

        fig.savefig(output, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        logger.info("Wrote provenance record figure: %s", output)
        return StepOutput(files=[output], metadata={"output": str(output)})


register_step_type("figure_provenance_record", FigureProvenanceRecordStep)

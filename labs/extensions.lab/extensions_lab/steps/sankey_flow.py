#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""sankey_flow — Render a Sankey (flow) diagram from a tidy DataFrame.

Given a DataFrame and an ordered list of categorical columns, draws a Sankey
diagram where each adjacent pair of columns becomes one stage of links. Link
width is proportional to the count of rows taking that path (or to a
configurable ``value_col`` if provided).

Useful for attribution analyses — e.g. tracing how a query group flows
through (memory_used → tool_outcome → answer_correctness) to localize where
quality is gained or lost.

Configuration
-------------

.. code-block:: yaml

    - name: figure-attribution-sankey
      type: sankey_flow
      depends_on: [attribution-data]
      config:
        data: "@attribution-data"           # in-memory df from upstream
        # or:
        # csv: "attribution.csv"
        stages: [query_group, memory_outcome, tool_outcome, answer_outcome]
        value_col: count                    # optional; defaults to row count
        title: "Lab 3.5 — Attribution Sankey: query → context → answer"
        output: "results/figure-attribution-sankey.png"

Uses matplotlib only (no plotly dependency).  Renders nodes as vertical
rectangles per stage with widths summed, and link ribbons as filled
quadrilaterals between consecutive stages.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List

from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_input_path,
)
from mas.lab.benchmark.pipeline import (
    PipelineStep,
    StepOutput,
    register_step_type,
)

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline import ExecutionContext

logger = logging.getLogger(__name__)


def _resolve_df(ctx, config):
    """Return DataFrame from `data: '@step'` ref, `csv: path`, or upstream."""
    import pandas as pd

    ref = config.get("data")
    if isinstance(ref, str) and ref.startswith("@"):
        step_name = ref[1:]
        upstream = ctx.step_outputs.get(step_name) if hasattr(ctx, "step_outputs") else None
        if upstream is None:
            raise ValueError(f"sankey_flow: upstream step '{step_name}' not found")
        df = upstream.data.get("df") if isinstance(upstream.data, dict) else None
        if df is None:
            raise ValueError(f"sankey_flow: upstream '{step_name}' has no .data['df']")
        return df.copy()
    path = resolve_step_input_path(ctx, "sankey_flow", config, "csv")
    if path is not None:
        return pd.read_csv(path)
    raise ValueError("sankey_flow: provide either 'data: @step' or 'csv: path'")


def _draw_sankey(ax, df, stages: List[str], value_col: str | None,
                 cmap_name: str = "tab10", node_pad: float = 0.02):
    import numpy as np
    import matplotlib.cm as cm
    import matplotlib.patches as patches
    from matplotlib.path import Path as MPath

    # Aggregate flows for each adjacent pair
    if value_col and value_col in df.columns:
        weights = df[value_col].astype(float).to_numpy()
    else:
        weights = None  # row count = 1 each

    # Stage totals per category, in stable observed order
    stage_categories: list[list[str]] = []
    for s in stages:
        cats = list(dict.fromkeys(df[s].astype(str).tolist()))
        stage_categories.append(cats)

    # Compute node sizes (sum of weights per category per stage)
    def _sum_weights(mask):
        return float(mask.sum()) if weights is None else float(weights[mask].sum())

    total = float(len(df)) if weights is None else float(weights.sum())

    cmap = cm.get_cmap(cmap_name)
    n_stages = len(stages)
    stage_x = np.linspace(0.05, 0.95, n_stages)
    node_width = 0.025

    # Per-stage layout: stack categories vertically with padding.
    layout: dict[tuple[int, str], tuple[float, float]] = {}  # (stage_i, cat) -> (y0, y1)
    for si, cats in enumerate(stage_categories):
        sizes = []
        for c in cats:
            mask = (df[stages[si]].astype(str) == c).to_numpy()
            sizes.append(_sum_weights(mask))
        norm = np.array(sizes) / total
        pad_total = node_pad * (len(cats) - 1)
        scale = 1.0 - pad_total
        y = 0.0
        for c, h in zip(cats, norm):
            h_scaled = h * scale
            layout[(si, c)] = (y, y + h_scaled)
            y += h_scaled + node_pad

    # Draw nodes
    color_map: dict[str, tuple] = {}
    palette_idx = 0
    for (si, c), (y0, y1) in layout.items():
        if c not in color_map:
            color_map[c] = cmap(palette_idx % cmap.N)
            palette_idx += 1
        ax.add_patch(patches.Rectangle(
            (stage_x[si] - node_width / 2, y0), node_width, max(y1 - y0, 1e-6),
            facecolor=color_map[c], edgecolor="black", linewidth=0.5,
        ))
        # label
        ax.text(stage_x[si] + node_width / 2 + 0.005, (y0 + y1) / 2, c,
                ha="left", va="center", fontsize=8)

    # Draw links between consecutive stages
    for si in range(n_stages - 1):
        left_col, right_col = stages[si], stages[si + 1]
        # For each (a,b) flow compute proportional ribbon
        # Track running offsets within each node
        left_offsets: dict[str, float] = {c: layout[(si, c)][0] for c in stage_categories[si]}
        right_offsets: dict[str, float] = {c: layout[(si + 1, c)][0] for c in stage_categories[si + 1]}
        # Order pairs by left category then by right category (stable)
        pair_groups = df.groupby([left_col, right_col], sort=False)
        for (a, b), grp in pair_groups:
            w = float(len(grp)) if weights is None else float(grp[value_col].astype(float).sum())
            if w <= 0:
                continue
            h_frac = (w / total)
            # Scale to scaled-stage height: account for padding scale
            la_y0, la_y1 = layout[(si, str(a))]
            ra_y0, ra_y1 = layout[(si + 1, str(b))]
            left_h = (la_y1 - la_y0) * (w / max(_sum_weights((df[left_col].astype(str) == str(a)).to_numpy()), 1e-9))
            right_h = (ra_y1 - ra_y0) * (w / max(_sum_weights((df[right_col].astype(str) == str(b)).to_numpy()), 1e-9))

            y_l0 = left_offsets[str(a)]
            y_l1 = y_l0 + left_h
            y_r0 = right_offsets[str(b)]
            y_r1 = y_r0 + right_h
            left_offsets[str(a)] = y_l1
            right_offsets[str(b)] = y_r1

            x0 = stage_x[si] + node_width / 2
            x1 = stage_x[si + 1] - node_width / 2
            xm = (x0 + x1) / 2
            # Cubic bezier ribbon (top + bottom curves)
            verts = [
                (x0, y_l1),
                (xm, y_l1), (xm, y_r1), (x1, y_r1),  # top curve
                (x1, y_r0),
                (xm, y_r0), (xm, y_l0), (x0, y_l0),  # bottom curve
                (x0, y_l1),
            ]
            codes = [
                MPath.MOVETO,
                MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
                MPath.LINETO,
                MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
                MPath.CLOSEPOLY,
            ]
            path = MPath(verts, codes)
            ax.add_patch(patches.PathPatch(
                path, facecolor=color_map[str(a)], alpha=0.35,
                edgecolor="none",
            ))

    # Stage titles
    for si, name in enumerate(stages):
        ax.text(stage_x[si], 1.04, name, ha="center", va="bottom",
                fontsize=10, fontweight="bold")

    ax.set_xlim(0, 1)
    ax.set_ylim(-0.02, 1.08)
    ax.axis("off")


class SankeyFlowStep(PipelineStep):
    """Render a multi-stage Sankey diagram from a tidy DataFrame."""

    type = "sankey_flow"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        cfg = self.config
        df = _resolve_df(ctx, cfg)
        stages: List[str] = list(cfg["stages"])
        missing = [s for s in stages if s not in df.columns]
        if missing:
            raise ValueError(f"sankey_flow: missing stage columns {missing}; have {list(df.columns)}")
        df = df.dropna(subset=stages).copy()
        if df.empty:
            logger.warning("sankey_flow '%s': empty df after dropna", self.name)
            return StepOutput(data={}, metadata={"warning": "empty"})

        value_col = cfg.get("value_col")
        title = cfg.get("title", "Sankey flow")

        fig, ax = plt.subplots(figsize=cfg.get("figsize", (12, 7)))
        _draw_sankey(ax, df, stages, value_col,
                     cmap_name=cfg.get("cmap", "tab10"))
        fig.suptitle(title, fontsize=12)
        fig.tight_layout()

        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "sankey.png", cfg, name_keys=(), mkdir=True
        )
        fig.savefig(output_path, dpi=cfg.get("dpi", 150), bbox_inches="tight")
        plt.close(fig)

        logger.info("SankeyFlowStep '%s': %d rows, stages=%s → %s",
                    self.name, len(df), stages, output_path)
        return StepOutput(
            data={"df": df},
            files=[output_path],
            metadata={"stages": stages, "output": str(output_path)},
        )


register_step_type("sankey_flow", SankeyFlowStep)

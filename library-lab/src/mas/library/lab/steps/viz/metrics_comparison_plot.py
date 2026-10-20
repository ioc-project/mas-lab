#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""MetricsComparisonPlotStep — aggregate metrics.json across scenarios into a bar chart.

Reads ``<output_dir>/<scenario>/item*/r*/metrics.json`` for each scenario,
computes per-scenario mean ± std for each metric, and writes a comparison
PNG with one subplot per metric.

Configuration
-------------
output         str           Relative path for the output PNG (default topology-comparison.png).
scenarios      list[str]     Scenario IDs to compare (optional, auto-detected).
metrics        list[str]     Metric IDs to plot (default: all found in metrics.json).
"""

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_root,
)

logger = logging.getLogger(__name__)


class MetricsComparisonPlotStep(PipelineStep):
    """Produce a comparison bar chart from metrics.json files across scenarios."""

    type = "metrics_comparison_plot"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        config = self.config

        output_dir = resolve_step_root(ctx, config, step=self.name)
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "topology-comparison.png", config, mkdir=True
        )

        scenario_filter: Optional[List[str]] = config.get("scenarios")

        # Discover scenarios from subdirectories
        if scenario_filter:
            scenarios = scenario_filter
        else:
            scenarios = sorted(
                d.name for d in output_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name != "data"
            )

        # Collect metrics per scenario
        # Structure: {scenario: {metric_id: [score, ...]}}
        data: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        latencies: Dict[str, List[float]] = defaultdict(list)
        all_metric_ids: set = set()

        for sid in scenarios:
            scenario_dir = output_dir / sid
            for metrics_file in sorted(scenario_dir.glob("item*/r*/metrics.json")):
                try:
                    doc = json.loads(metrics_file.read_text(encoding="utf-8"))
                    # MCE metrics.json schema: session → {metric_id: {value, reasoning, error}}
                    session = doc.get("session", {})
                    for mid, entry in session.items():
                        score = entry.get("value") if isinstance(entry, dict) else None
                        if mid and score is not None:
                            data[sid][mid].append(float(score))
                            all_metric_ids.add(mid)
                except Exception as exc:
                    logger.warning("Skipping %s: %s", metrics_file, exc)

            # Also collect latencies from run_info.json
            for run_info_file in sorted(scenario_dir.glob("item*/r*/run_info.json")):
                try:
                    info = json.loads(run_info_file.read_text(encoding="utf-8"))
                    elapsed = info.get("elapsed_ms", 0)
                    if elapsed:
                        latencies[sid].append(elapsed / 1000.0)
                except Exception:
                    logger.debug('suppressed', exc_info=True)

        if not data:
            logger.warning("MetricsComparisonPlotStep: no metrics.json found")
            return StepOutput(data={"scenarios": 0}, metadata={"error": "no data"})

        # Filter metric_ids if configured
        metric_filter = config.get("metrics")
        if metric_filter:
            all_metric_ids = {m for m in all_metric_ids if m in set(metric_filter)}
        metric_ids = sorted(all_metric_ids)

        # Include latency as a panel
        include_latency = bool(latencies) and config.get("include_latency", True)
        n_panels = len(metric_ids) + (1 if include_latency else 0)

        if n_panels == 0:
            logger.warning("MetricsComparisonPlotStep: no metrics to plot")
            return StepOutput(data={"scenarios": len(scenarios)})

        # Generate plot
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        colors = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
                  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"]

        fig, axes = plt.subplots(1, n_panels, figsize=(5 * n_panels, 5))
        if n_panels == 1:
            axes = [axes]

        for ax_idx, mid in enumerate(metric_ids):
            ax = axes[ax_idx]
            means, stds = [], []
            for sid in scenarios:
                vals = data[sid].get(mid, [])
                means.append(np.mean(vals) if vals else 0)
                stds.append(np.std(vals) if vals else 0)
            bars = ax.bar(range(len(scenarios)), means, yerr=stds,
                          color=colors[:len(scenarios)], capsize=8, alpha=0.85)
            ax.set_xticks(range(len(scenarios)))
            ax.set_xticklabels(scenarios, fontsize=9)
            ax.set_ylabel("Score")
            ax.set_title(mid.replace("_", " ").title())
            ax.set_ylim(0, 1.1)

        if include_latency:
            ax = axes[-1]
            means, stds = [], []
            for sid in scenarios:
                vals = latencies.get(sid, [])
                means.append(np.mean(vals) if vals else 0)
                stds.append(np.std(vals) if vals else 0)
            ax.bar(range(len(scenarios)), means, yerr=stds,
                   color=colors[:len(scenarios)], capsize=8, alpha=0.85)
            ax.set_xticks(range(len(scenarios)))
            ax.set_xticklabels(scenarios, fontsize=9)
            ax.set_ylabel("Seconds")
            ax.set_title("Latency")

        n_runs = max(len(v) for sid_data in data.values() for v in sid_data.values()) if data else 0
        fig.suptitle(
            f"Topology Comparison (n={n_runs})",
            fontsize=13, fontweight="bold",
        )
        plt.tight_layout()
        plt.savefig(str(output_path), dpi=150)
        plt.close(fig)

        logger.info("MetricsComparisonPlotStep: saved %s", output_path)
        return StepOutput(
            data={"scenarios": len(scenarios), "metrics": metric_ids, "n_runs": n_runs},
            files=[output_path],
            metadata={"output": str(output_path)},
        )

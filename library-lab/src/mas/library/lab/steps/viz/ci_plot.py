#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""CIPlotStep — compute confidence intervals across runs and render a plot.

Reads a tidy CSV produced by ``collect_metrics`` (one row per run × item ×
metric), aggregates by the requested ``groupby`` columns, computes confidence
intervals, writes a summary CSV, and renders a publication-ready figure with
mean bars and error bars.

This step is the standard way to show reproducibility in every lab experiment
that uses ``n_runs > 1``.  The summary CSV is also directly usable for paper
tables.

Statistical methods
-------------------
``t``           Student's t-distribution (recommended for n < 30).
                half-width = t(α/2, df=n-1) × SE,  SE = std / sqrt(n)
``bootstrap``   Percentile bootstrap (2000 resamples).  More accurate for
                skewed distributions but slower.
``normal``      Normal approximation (z × SE).  Use only for n ≥ 30.

Output files
------------
<output_dir>/ci_summary.csv    Aggregated table: group columns + mean, std,
                               n, ci_low, ci_high, ci_half.
<output>                       PNG/SVG figure with geom_col + geom_errorbar.

Configuration
-------------
data         str            Path to input CSV (from collect_metrics).
value_col    str            Column to aggregate (default: "value").
metric_col   str            Column that names each metric (default: "metric").
metrics      list[str]      Optional: restrict to these metric values.
groupby      list[str]      Grouping columns (default: ["scenario", "metric"]).
ci_method    str            "t" | "bootstrap" | "normal" (default: "t").
ci_level     float          Confidence level, e.g. 0.95 (default: 0.95).
min_runs     int            Warn when a group has fewer runs (default: 2).
output       str            Figure path (png/svg/pdf).
summary_out  str            Optional: override path for ci_summary.csv.
mapping      dict           Extra aes() overrides passed to the plot.
facet        str            Optional facet formula: "~ metric" or "metric ~ .".
labels       dict           {title, x, y, fill, subtitle}.
width        float          Figure width in inches (default: 8).
height       float          Figure height in inches (default: 5).
dpi          int            Figure DPI (default: 150).

Example YAML — Lab 3 reproducibility figure::

    - name: ci-plot
      type: ci_plot
      config:
        data: "{output_dir}/results.csv"
        metrics: [goal_success_rate, response_completeness]
        groupby: [scenario, item_group, metric]
        ci_method: t
        ci_level: 0.95
        facet: "metric ~ item_group"
        labels:
          title: "Result stability across 3 runs"
          subtitle: "Mean ± 95% CI (Student's t,  n=3)"
          x: ""
          y: "MCE score"
        output: "{output_dir}/results/figure-ci.png"
      depends_on: [collect-metrics]

Example YAML — minimal, single metric::

    - name: ci-plot
      type: ci_plot
      config:
        data: "{output_dir}/results.csv"
        metrics: [goal_success_rate]
        groupby: [scenario]
        output: "{output_dir}/results/figure-ci.png"
      depends_on: [collect-metrics]
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.library.lab.steps.services.plot_stream import image_html_shell, post_plot_tick

logger = logging.getLogger(__name__)

_DEFAULT_GROUPBY = ["scenario", "metric"]
_DEFAULT_CI_METHOD = "t"
_DEFAULT_CI_LEVEL = 0.95
_DEFAULT_VALUE_COL = "value"
_DEFAULT_METRIC_COL = "metric"


def _compute_ci(
    series,
    method: str,
    level: float,
    n_bootstrap: int = 2000,
):
    """Return (mean, std, n, ci_low, ci_high, ci_half) for *series*.

    Parameters
    ----------
    series      : array-like of float values
    method      : "t" | "bootstrap" | "normal"
    level       : confidence level (0–1)
    n_bootstrap : number of resamples for bootstrap method
    """
    import numpy as np

    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    mean = float(np.mean(arr)) if n > 0 else float("nan")
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0

    if n == 0:
        return mean, std, n, float("nan"), float("nan"), float("nan")
    if n == 1:
        return mean, std, n, mean, mean, 0.0

    alpha = 1.0 - level

    if method == "bootstrap":
        import numpy.random as rng
        samples = rng.default_rng(seed=42).choice(arr, size=(n_bootstrap, n), replace=True)
        boot_means = samples.mean(axis=1)
        ci_low = float(np.percentile(boot_means, 100 * alpha / 2))
        ci_high = float(np.percentile(boot_means, 100 * (1 - alpha / 2)))
        ci_half = (ci_high - ci_low) / 2

    elif method == "normal":
        from scipy.stats import norm
        se = std / (n ** 0.5)
        z = float(norm.ppf(1 - alpha / 2))
        ci_half = z * se
        ci_low = mean - ci_half
        ci_high = mean + ci_half

    else:  # "t" — default, recommended for n < 30
        from scipy.stats import t as t_dist
        se = std / (n ** 0.5)
        t_val = float(t_dist.ppf(1 - alpha / 2, df=n - 1))
        ci_half = t_val * se
        ci_low = mean - ci_half
        ci_high = mean + ci_half

    return mean, std, n, float(ci_low), float(ci_high), float(ci_half)


class CIPlotStep(PipelineStep):
    """Compute confidence intervals across runs and render a figure."""

    type = "ci_plot"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import pandas as pd

        config = self.config

        # ── Input ──────────────────────────────────────────────────────
        data_path_raw = config.get("data", "")
        if not data_path_raw:
            raise ValueError(f"CIPlotStep '{self.name}': 'data' (CSV path) required")
        data_path = Path(data_path_raw)
        if not data_path.exists():
            raise FileNotFoundError(f"CIPlotStep '{self.name}': {data_path} not found")

        df = pd.read_csv(data_path)
        logger.info("CIPlotStep '%s': loaded %d rows from %s", self.name, len(df), data_path)

        # ── Config ─────────────────────────────────────────────────────
        value_col: str = config.get("value_col", _DEFAULT_VALUE_COL)
        metric_col: str = config.get("metric_col", _DEFAULT_METRIC_COL)
        metrics_filter: Optional[List[str]] = config.get("metrics")
        groupby: List[str] = config.get("groupby", list(_DEFAULT_GROUPBY))
        ci_method: str = config.get("ci_method", _DEFAULT_CI_METHOD)
        ci_level: float = float(config.get("ci_level", _DEFAULT_CI_LEVEL))
        min_runs: int = int(config.get("min_runs", 2))

        # ── Filter metrics ─────────────────────────────────────────────
        if metrics_filter and metric_col in df.columns:
            df = df[df[metric_col].isin(metrics_filter)]
            if df.empty:
                raise ValueError(
                    f"CIPlotStep '{self.name}': no rows remain after filtering "
                    f"metrics={metrics_filter!r} on column '{metric_col}'"
                )

        # ── Validate columns ───────────────────────────────────────────
        missing = [c for c in groupby if c not in df.columns]
        if missing:
            raise ValueError(
                f"CIPlotStep '{self.name}': groupby columns not found in CSV: {missing}"
            )
        if value_col not in df.columns:
            raise ValueError(
                f"CIPlotStep '{self.name}': value column '{value_col}' not found in CSV"
            )

        # ── Aggregate with CI ──────────────────────────────────────────
        rows = []
        for group_keys, group_df in df.groupby(groupby, sort=True):
            if not isinstance(group_keys, tuple):
                group_keys = (group_keys,)
            values = group_df[value_col].dropna()
            n = len(values)
            if n < min_runs:
                logger.warning(
                    "CIPlotStep '%s': group %s has only %d run(s) — CI may be unreliable",
                    self.name,
                    dict(zip(groupby, group_keys)),
                    n,
                )
            mean, std, n, ci_low, ci_high, ci_half = _compute_ci(values, ci_method, ci_level)
            row = dict(zip(groupby, group_keys))
            row.update(
                mean=mean,
                std=std,
                n=int(n),
                ci_low=ci_low,
                ci_high=ci_high,
                ci_half=ci_half,
                ci_method=ci_method,
                ci_level=ci_level,
            )
            rows.append(row)

        summary_df = pd.DataFrame(rows)

        # ── Write summary CSV ──────────────────────────────────────────
        summary_out_raw = config.get("summary_out", "")
        if summary_out_raw:
            summary_path = Path(summary_out_raw)
        else:
            summary_path = data_path.parent / "ci_summary.csv"

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_df.to_csv(summary_path, index=False)
        logger.info("CIPlotStep '%s': wrote CI summary to %s (%d rows)", self.name, summary_path, len(summary_df))

        # ── Render figure ──────────────────────────────────────────────
        output_raw = config.get("output", "")
        files = [summary_path]

        if output_raw:
            output_path = Path(output_raw)
            if not output_path.is_absolute():
                output_path = data_path.parent / output_path

            _render_ci_figure(
                summary_df=summary_df,
                groupby=groupby,
                config=config,
                output_path=output_path,
                step_name=self.name,
            )
            files.append(output_path)
            self._stream_plot_tick(output_path, config=config, summary_df=summary_df, ctx=ctx)

        return StepOutput(
            data={"ci_summary": str(summary_path), "summary_df": summary_df},
            files=files,
            metadata={
                "ci_method": ci_method,
                "ci_level": ci_level,
                "groups": len(summary_df),
                "summary_csv": str(summary_path),
            },
        )

    def _stream_plot_tick(
        self, output_path: Path, *, config: Dict[str, Any], summary_df, ctx: "ExecutionContext"
    ) -> None:
        """Publish the figure just written to disk as a ``custom.{owner}.plot`` tick.

        Non-image formats (PDF, say) are skipped because they cannot embed as
        an ``<img>``.
        """
        renderable = {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"}
        if output_path.suffix.lower() not in renderable:
            logger.debug(
                "CIPlotStep '%s': output format %s is not browser-renderable, skipping live plot tick",
                self.name,
                output_path.suffix,
            )
            return
        if not output_path.is_file():
            return
        owner = str(config.get("plot_owner") or self.name)
        title = str((config.get("labels") or {}).get("title") or self.name)
        scope = getattr(getattr(ctx, "scope_context", None), "scenario", None)
        ref = {
            "library": config.get("library"),
            "experiment": config.get("experiment"),
            "scenario": config.get("scenario") or scope,
            "run": getattr(ctx, "run_id", None),
        }
        try:
            html = image_html_shell(output_path, title=title)
            post_plot_tick(
                owner,
                kind="image",
                title=title,
                data={"groups": len(summary_df), "columns": list(summary_df.columns)},
                html=html,
                url=config.get("demo_ui_url"),
                ref=ref,
            )
        except Exception:
            # Same "optional, never fatal" contract as FileTailStep.on_event's
            # transform errors -- a broken live-plot path must never fail the
            # pipeline step whose real job is the static artefact.
            logger.warning("CIPlotStep '%s': failed to publish live plot tick", self.name, exc_info=True)


def _render_ci_figure(
    summary_df,
    groupby: List[str],
    config: Dict[str, Any],
    output_path: Path,
    step_name: str,
) -> None:
    """Render a bar + errorbar figure from the CI summary dataframe."""
    from plotnine import (
        ggplot, aes,
        geom_col, geom_errorbar,
        facet_wrap, facet_grid,
        labs, theme_minimal, theme, element_text,
        scale_fill_brewer,
        position_dodge,
    )

    # ── Aesthetic mapping ──────────────────────────────────────────────
    # Default x = first groupby column, fill = first groupby column.
    # The caller can override via config.mapping.
    x_col = groupby[0]
    fill_col = groupby[0]

    base_mapping: Dict[str, str] = {"x": x_col, "y": "mean", "fill": fill_col}
    extra_mapping: Dict[str, str] = config.get("mapping", {})
    base_mapping.update(extra_mapping)

    p = ggplot(summary_df, aes(**base_mapping))

    # ── Bars (mean) ────────────────────────────────────────────────────
    p = p + geom_col(alpha=0.85)

    # ── Error bars (CI) ────────────────────────────────────────────────
    error_mapping = {k: v for k, v in base_mapping.items() if k not in ("y", "fill")}
    error_mapping.update(ymin="ci_low", ymax="ci_high")
    p = p + geom_errorbar(aes(**error_mapping), width=0.3)

    # ── Facet ──────────────────────────────────────────────────────────
    facet_spec = config.get("facet", "")
    if facet_spec:
        if "~" in facet_spec and facet_spec.strip().split("~")[0].strip():
            p = p + facet_grid(facet_spec, scales="free_y")
        else:
            var = facet_spec.replace("~", "").strip()
            p = p + facet_wrap(var, scales="free_y")

    # ── Labels ─────────────────────────────────────────────────────────
    labels_cfg = config.get("labels", {})
    if labels_cfg:
        p = p + labs(**labels_cfg)

    # ── Theme ──────────────────────────────────────────────────────────
    p = p + theme_minimal()
    p = p + theme(
        figure_size=(config.get("width", 8), config.get("height", 5)),
        axis_text_x=element_text(rotation=30, ha="right"),
    )

    if config.get("scales", {}).get("fill") == "brewer":
        p = p + scale_fill_brewer(type="qual", palette="Set2")

    # ── Save ───────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dpi = config.get("dpi", 150)
    p.save(str(output_path), dpi=dpi, verbose=False)
    logger.info("CIPlotStep '%s': saved figure to %s", step_name, output_path)

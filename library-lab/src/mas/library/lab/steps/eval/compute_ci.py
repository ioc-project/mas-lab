#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ComputeCIStep — aggregate a tidy CSV over N runs, computing confidence intervals.

Pure data-transform step: reads a ``collect_metrics`` output CSV, groups by
arbitrary columns, and writes a CI summary CSV.  Does not render any figure.

The output CSV is a tidy dataframe with all ``groupby`` columns preserved plus:

    mean      float   per-group mean of *value_col*
    std       float   sample standard deviation (ddof=1)
    n         int     number of non-null observations
    ci_low    float   lower bound of the confidence interval
    ci_high   float   upper bound of the confidence interval
    ci_half   float   half-width of the CI  (ci_high - ci_low) / 2
    ci_method str     method used ("t", "bootstrap", "normal")
    ci_level  float   confidence level (e.g. 0.95)

This output can be consumed directly by ``plotnine`` using ``y: mean``,
``ymin: ci_low``, ``ymax: ci_high`` in the mapping — plotnine will
automatically render error bars when those keys are present.

Statistical methods
-------------------
``t``           Student's t-distribution.  **Default — recommended for n < 30.**
                half-width = t(α/2, df=n-1) × SE,  SE = std / √n
``bootstrap``   Percentile bootstrap (2000 resamples, seed=42).  More accurate
                for skewed distributions.
``normal``      Normal approximation (z × SE).  Use for n ≥ 30 only.

Configuration
-------------
data         str            Path to input CSV (from collect_metrics).
value_col    str            Column to aggregate (default: "value").
metric_col   str            Column that names each metric (default: "metric").
metrics      list[str]      Optional: restrict to these metric values.
groupby      list[str]      Grouping columns (default: ["scenario", "metric"]).
ci_method    str            "t" | "bootstrap" | "normal" (default: "t").
ci_level     float          Confidence level (default: 0.95).
min_runs     int            Warn when a group has fewer runs (default: 2).
output       str            Optional relative output path; persists the table
                            (default name: ci_summary.csv; ``subdir`` / ``filename`` also persist).
pre_aggregate dict          Optional: pre-aggregate observations before computing CI.
                            Keys: ``by`` (list[str]) — group columns for pre-
                            aggregation; ``agg`` (str, default ``"mean"``) —
                            aggregation function; ``value_col`` (str) — column to
                            pre-aggregate (defaults to top-level value_col).
                            Example: ``{by: [scenario, metric, item_id], agg: mean}``
                            collapses N runs into one item-level mean per item, then CI
                            is computed across items.  Use when items are the true
                            experimental unit rather than individual runs.
levels       list[dict]     Optional: compute CI at multiple granularities in one step.
                            Each entry: ``{name, groupby?, extra_cols?, pre_aggregate?}``.
                            Fields inherit from the top-level config when omitted.
                            Output DataFrames are stored under ``df_<name>`` in
                            StepOutput.data; ``df`` aliases the first level (backward
                            compat).  When ``levels`` is present, top-level ``groupby``
                            / ``extra_cols`` / ``pre_aggregate`` serve as per-level
                            defaults.

                            Example::

                                levels:
                                  - name: by_run
                                    groupby: [scenario, metric]
                                    extra_cols: [latency_s]
                                  - name: by_item       # CI of item-level means
                                    groupby: [scenario, metric]
                                    pre_aggregate:
                                      by: [scenario, metric, item_id]
                                      agg: mean

                            Reference downstream: ``@compute-ci:df_by_item``.

The ``data`` field accepts:

* a file path (CSV, Parquet, or JSON — format auto-detected from extension)
* ``@step-name`` — read the DataFrame from a prior step's in-memory output
  (key ``"df"`` by default)
* ``@step-name:field`` — read a specific named field from a prior step's output

The ``"df"`` key in ``StepOutput.data`` is the canonical in-memory channel.
The optional ``output`` file is a persistence copy for debugging; it is NOT
required by downstream steps — they should use ``@compute-ci`` instead.

Example YAML::

    # Step 1 — compute CI (reusable, format-agnostic)
    - name: compute-ci
      type: compute_ci
      config:
        data: "@collect-metrics"          # in-memory from prior step
        metrics: [goal_success_rate, response_completeness]
        groupby: [scenario, item_group, metric]
        ci_method: t
        ci_level: 0.95
        output: ci_summary.csv   # optional persistence
      depends_on: [collect-metrics]

    # Step 2 — render (reads DataFrame from memory, not from disk)
    - name: ci-figure
      type: plotnine
      config:
        data: "@compute-ci"               # in-memory DataFrame
        mapping:
          x: scenario
          y: mean
          fill: scenario
          ymin: ci_low                    # auto-triggers geom_errorbar
          ymax: ci_high
        geom: col
        facet: "metric ~ item_group"
        labels:
          title: "Mean ± 95% CI across 3 runs"
          y: "MCE score"
        output: results/figure-ci.png
      depends_on: [compute-ci]
"""

import logging
from pathlib import Path
from typing import List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path
from mas.lab.benchmark.pipeline.lib.data_source import resolve_dataframe, write_dataframe

logger = logging.getLogger(__name__)

_DEFAULT_GROUPBY = ["scenario", "metric"]
_DEFAULT_CI_METHOD = "t"
_DEFAULT_CI_LEVEL = 0.95
_DEFAULT_VALUE_COL = "value"
_DEFAULT_METRIC_COL = "metric"


def compute_ci(
    values,
    method: str = _DEFAULT_CI_METHOD,
    level: float = _DEFAULT_CI_LEVEL,
    n_bootstrap: int = 2000,
) -> tuple[float, float, int, float, float, float]:
    """Return ``(mean, std, n, ci_low, ci_high, ci_half)`` for *values*.

    Parameters
    ----------
    values      : array-like of float values (NaNs are dropped)
    method      : "t" | "bootstrap" | "normal"
    level       : confidence level in [0, 1]
    n_bootstrap : resamples for bootstrap method (ignored otherwise)

    This function is importable by any step that needs CI computation::

        from mas.library.lab.steps.eval.compute_ci import compute_ci
    """
    import math
    import numpy as np

    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = int(len(arr))
    mean = float(np.mean(arr)) if n > 0 else float("nan")
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0

    if n == 0:
        nan = float("nan")
        return mean, std, n, nan, nan, nan
    if n == 1:
        return mean, std, n, mean, mean, 0.0

    alpha = 1.0 - level

    if method == "bootstrap":
        rng = np.random.default_rng(seed=42)
        samples = rng.choice(arr, size=(n_bootstrap, n), replace=True)
        boot_means = samples.mean(axis=1)
        ci_low = float(np.percentile(boot_means, 100 * alpha / 2))
        ci_high = float(np.percentile(boot_means, 100 * (1 - alpha / 2)))

    elif method == "normal":
        from scipy.stats import norm
        se = std / math.sqrt(n)
        z = float(norm.ppf(1 - alpha / 2))
        ci_low = mean - z * se
        ci_high = mean + z * se

    else:  # "t" — default
        from scipy.stats import t as t_dist
        se = std / math.sqrt(n)
        t_val = float(t_dist.ppf(1 - alpha / 2, df=n - 1))
        ci_low = mean - t_val * se
        ci_high = mean + t_val * se

    ci_half = (ci_high - ci_low) / 2
    return mean, std, n, float(ci_low), float(ci_high), float(ci_half)


def _compute_level(
    df: "pd.DataFrame",
    groupby: list,
    extra_cols: list,
    pre_aggregate: Optional[dict],
    value_col: str,
    metric_col: str,
    ci_method: str,
    ci_level: float,
    min_runs: int,
    step_name: str,
    level_name: str,
) -> "pd.DataFrame":
    """Compute a CI summary for one aggregation level.

    Parameters
    ----------
    df            : Tidy DataFrame (already metric-filtered by the caller).
    groupby       : Columns defining the groups for CI computation.
    extra_cols    : Secondary columns for 1-D CI (metric-dedup applied automatically).
    pre_aggregate : Optional ``{by, agg, value_col}`` — pre-aggregate before CI.
                    Useful to compute CI of item-level means instead of raw run
                    observations when items are the true experimental unit.
    value_col     : Column containing the metric value.
    metric_col    : Column naming each metric (used for extra-col metric-dedup).
    ci_method     : "t" | "bootstrap" | "normal".
    ci_level      : Confidence level.
    min_runs      : Warn when a group has fewer observations than this.
    step_name     : For log messages.
    level_name    : For log messages.
    """
    import pandas as pd

    work_df = df.copy()

    # ── Pre-aggregation ────────────────────────────────────────────────
    # Collapse observations (e.g. runs) into a coarser unit (e.g. items)
    # before computing CI, so the CI reflects variation at that coarser level.
    if pre_aggregate:
        pre_by: list = list(pre_aggregate["by"])
        pre_agg_fn: str = pre_aggregate.get("agg", "mean")
        pre_vc: str = pre_aggregate.get("value_col", value_col)

        missing_pre = [c for c in pre_by if c not in work_df.columns]
        if missing_pre:
            raise ValueError(
                f"ComputeCIStep '{step_name}' level '{level_name}': "
                f"pre_aggregate.by columns not in DataFrame: {missing_pre}"
            )
        # Aggregate value_col + any extra_cols that are present
        agg_cols = list(dict.fromkeys(
            [c for c in [pre_vc] + extra_cols if c in work_df.columns]
        ))
        work_df = (
            work_df
            .groupby(pre_by, sort=True)[agg_cols]
            .agg(pre_agg_fn)
            .reset_index()
        )
        logger.info(
            "ComputeCIStep '%s' level '%s': pre-aggregated to %d rows (by=%s, agg=%s)",
            step_name, level_name, len(work_df), pre_by, pre_agg_fn,
        )

    # ── Validate columns ───────────────────────────────────────────────
    missing_cols = [c for c in groupby if c not in work_df.columns]
    if missing_cols:
        raise ValueError(
            f"ComputeCIStep '{step_name}' level '{level_name}': "
            f"groupby columns not in DataFrame: {missing_cols}"
        )
    if value_col not in work_df.columns:
        raise ValueError(
            f"ComputeCIStep '{step_name}' level '{level_name}': "
            f"value column '{value_col}' not in DataFrame"
        )

    # ── CI per group ───────────────────────────────────────────────────
    rows = []
    for group_keys, group_df in work_df.groupby(groupby, sort=True):
        if not isinstance(group_keys, tuple):
            group_keys = (group_keys,)
        values = group_df[value_col].dropna()
        n = len(values)
        if n < min_runs:
            logger.warning(
                "ComputeCIStep '%s' level '%s': group %s has only %d obs "
                "— CI may be unreliable",
                step_name, level_name,
                dict(zip(groupby, group_keys)), n,
            )
        mean, std, n_obs, ci_low, ci_high, ci_half = compute_ci(
            values, method=ci_method, level=ci_level
        )
        row = dict(zip(groupby, group_keys))
        row.update(
            mean=mean, std=std, n=int(n_obs),
            ci_low=ci_low, ci_high=ci_high, ci_half=ci_half,
            ci_method=ci_method, ci_level=ci_level,
        )
        rows.append(row)

    summary_df = pd.DataFrame(rows)

    # ── Extra cols — 1-D CI on secondary columns ───────────────────────
    # These cols appear once per (item_id, run_idx) in tidy format but are
    # duplicated across metrics.  Strip the metric dimension and deduplicate
    # so the CI reflects independent observations only.
    if extra_cols:
        extra_groupby = [c for c in groupby if c != metric_col] or groupby
        id_cols = [c for c in ("item_id", "run_idx") if c in work_df.columns]
        dedup_key = list(dict.fromkeys(extra_groupby + id_cols))

        for col in extra_cols:
            if col not in work_df.columns:
                logger.warning(
                    "ComputeCIStep '%s' level '%s': extra_col '%s' not in DataFrame — skipped",
                    step_name, level_name, col,
                )
                continue

            dedup_df = (
                work_df[dedup_key + [col]]
                .drop_duplicates(subset=dedup_key)
            )
            extra_ci: list[dict] = []
            for group_keys, grp in dedup_df.groupby(extra_groupby, sort=True):
                if not isinstance(group_keys, tuple):
                    group_keys = (group_keys,)
                mean_e, std_e, n_e, ci_lo_e, ci_hi_e, _ = compute_ci(
                    grp[col].dropna(), method=ci_method, level=ci_level
                )
                extra_ci.append({
                    **dict(zip(extra_groupby, group_keys)),
                    f"{col}_mean":    mean_e,
                    f"{col}_std":     std_e,
                    f"{col}_n":       int(n_e),
                    f"{col}_ci_low":  ci_lo_e,
                    f"{col}_ci_high": ci_hi_e,
                })

            extra_df = pd.DataFrame(extra_ci)
            summary_df = summary_df.merge(extra_df, on=extra_groupby, how="left")
            logger.info(
                "ComputeCIStep '%s' level '%s': merged extra_col '%s' CI",
                step_name, level_name, col,
            )

    return summary_df


class ComputeCIStep(PipelineStep):
    """Aggregate a tidy CSV over N runs, computing confidence intervals.

    Pure data-transform step — no rendering.  Supports single-level and
    multi-level (``levels``) aggregation and optional pre-aggregation
    (``pre_aggregate``) within each level.
    """

    type = "compute_ci"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import pandas as pd

        config = self.config

        # ── Input ──────────────────────────────────────────────────────
        data_source: str = config.get("data", "")
        if not data_source:
            raise ValueError(f"ComputeCIStep '{self.name}': 'data' required")

        df = resolve_dataframe(data_source, ctx, config=config, step=self.name)
        logger.info(
            "ComputeCIStep '%s': loaded %d rows from '%s'",
            self.name, len(df), data_source,
        )

        # ── Shared config ──────────────────────────────────────────────
        value_col: str = config.get("value_col", _DEFAULT_VALUE_COL)
        metric_col: str = config.get("metric_col", _DEFAULT_METRIC_COL)
        metrics_filter: Optional[List[str]] = config.get("metrics")
        ci_method: str = config.get("ci_method", _DEFAULT_CI_METHOD)
        ci_level: float = float(config.get("ci_level", _DEFAULT_CI_LEVEL))
        min_runs: int = int(config.get("min_runs", 2))

        # ── Filter metrics ─────────────────────────────────────────────
        if metrics_filter and metric_col in df.columns:
            df = df[df[metric_col].isin(metrics_filter)]
            if df.empty:
                raise ValueError(
                    f"ComputeCIStep '{self.name}': no rows remain after filtering "
                    f"metrics={metrics_filter!r} on column '{metric_col}'"
                )

        # ── Resolve levels ─────────────────────────────────────────────
        # Mode A (backward compat): groupby / extra_cols / pre_aggregate at top level
        # Mode B (multi-level):     levels: list[{name, groupby?, extra_cols?,
        #                                         pre_aggregate?}]
        # Top-level keys serve as per-level defaults in Mode B.
        default_groupby = list(config.get("groupby", _DEFAULT_GROUPBY))
        default_extra_cols = list(config.get("extra_cols", []))
        default_pre_agg = config.get("pre_aggregate")

        levels_cfg = config.get("levels")
        if levels_cfg:
            levels = []
            for lc in levels_cfg:
                if "name" not in lc:
                    raise ValueError(
                        f"ComputeCIStep '{self.name}': each entry in 'levels' "
                        "must have a 'name'"
                    )
                levels.append({
                    "name":          str(lc["name"]),
                    "groupby":       list(lc.get("groupby", default_groupby)),
                    "extra_cols":    list(lc.get("extra_cols", default_extra_cols)),
                    "pre_aggregate": lc.get("pre_aggregate", default_pre_agg),
                })
        else:
            levels = [{
                "name":          "default",
                "groupby":       default_groupby,
                "extra_cols":    default_extra_cols,
                "pre_aggregate": default_pre_agg,
            }]

        # ── Compute each level ─────────────────────────────────────────
        level_dfs: dict[str, pd.DataFrame] = {}
        for lv in levels:
            level_dfs[lv["name"]] = _compute_level(
                df=df,
                groupby=lv["groupby"],
                extra_cols=lv["extra_cols"],
                pre_aggregate=lv.get("pre_aggregate"),
                value_col=value_col,
                metric_col=metric_col,
                ci_method=ci_method,
                ci_level=ci_level,
                min_runs=min_runs,
                step_name=self.name,
                level_name=lv["name"],
            )

        # ── Build StepOutput.data ──────────────────────────────────────
        # "df"        → first level (backward compat key)
        # "df_<name>" → every named level
        first_name = levels[0]["name"]
        data_out: dict = {"df": level_dfs[first_name]}
        for name, ldf in level_dfs.items():
            data_out[f"df_{name}"] = ldf

        # ── Write output ───────────────────────────────────────────────
        files = []
        if any(config.get(k) for k in ("output", "subdir", "filename")):
            output_path = resolve_step_artifact_path(
                ctx, None, self.name, "ci_summary.csv", config, mkdir=True
            )
            write_dataframe(level_dfs[first_name], output_path)
            files.append(output_path)
            # Write per-level CSVs when there are multiple levels
            for name, ldf in list(level_dfs.items())[1:]:
                p = output_path.parent / f"{output_path.stem}_{name}{output_path.suffix}"
                write_dataframe(ldf, p)
                files.append(p)
                logger.info(
                    "ComputeCIStep '%s': wrote level '%s' → %s (%d groups)",
                    self.name, name, p, len(ldf),
                )
            logger.info(
                "ComputeCIStep '%s': persisted %d level(s) (method=%s, level=%s)",
                self.name, len(files), ci_method, ci_level,
            )
        else:
            logger.info(
                "ComputeCIStep '%s': %d level(s) computed "
                "(no output path — reference via @%s or @%s:df_<name>)",
                self.name, len(levels), self.name, self.name,
            )

        return StepOutput(
            data=data_out,
            files=files,
            metadata={
                "ci_method": ci_method,
                "ci_level":  ci_level,
                "levels":    {name: len(ldf) for name, ldf in level_dfs.items()},
                "output":    str(files[0]) if files else "",
            },
        )

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""PlotNineStep — render a plotnine (ggplot2-style) chart from a DataFrame.

The ``data`` field accepts a file path (CSV, Parquet, JSON) or an in-memory
reference to a prior step's DataFrame output::

    data: "@compute-ci"          # read df from ctx.step_outputs["compute-ci"]
    data: "@step-name:my_field"  # read a specific field
    data: "results.csv"   # file path, relative to the level folder

Error bars are added automatically when the mapping contains ``ymin``
and ``ymax`` — no ``error_bars: true`` flag needed::

    mapping:
      x: scenario
      y: mean
      fill: scenario
      ymin: ci_low     # ← auto-triggers geom_errorbar
      ymax: ci_high

Configuration
-------------
data         str            DataFrame source (see above).
output       str            Relative output path (png/svg/pdf); default plot.png.
mapping      dict           aes() mapping: {x, y, fill, color, ymin, ymax, ...}.
geom         str            Geometry: "bar", "col", "point", "boxplot", "line".
                            Default: "col".
geom_kwargs  dict           Optional kwargs passthrough to the main geom
                            (e.g. {linetype: "dashed", color: "#666666"}).
stat         str            "summary" / "mean" → stat_summary(mean).
                            "sum" → stat_summary(sum).
                            Omit for pre-aggregated data.
facet        str            Optional facet formula: "~ metric" or "metric ~ .".
labels       dict           {title, subtitle, x, y, fill, ...}.
x_limits     [min,max]      Optional x-axis viewport limits (coord_cartesian).
y_limits     [min,max]      Optional y-axis viewport limits (coord_cartesian).
legend_position str|[x,y]   Legend position. Examples: "right", "none",
                            or [0.98, 0.02] (inside plot, NPC coords).
legend_justification [x,y]  Optional legend anchor when using inside coords.
oov_x        dict           Optional out-of-viewport x indicator.
                            Keys: enabled(bool), max(float), shape(str),
                            size(float), alpha(float).
                            Draws marker(s) at x=max for rows whose mapped x
                            is greater than max.
scales       dict           Optional scale overrides.
width        float          Figure width in inches (default: 8).
height       float          Figure height in inches (default: 5).
dpi          int            DPI for raster output (default: 150).
df_schema    dict           Optional: validate the input DataFrame before
                            rendering.  Raises ValueError on hard failures,
                            logs warnings on soft failures (missing groups).
                            Keys:
                            ``required_columns`` list[str] — columns that
                            must exist in the DataFrame (hard error).
                            ``required_groups`` dict[str, list] — for each
                            column, the group values that should be present
                            (soft warning if any are absent).

                            Example::

                                df_schema:
                                  required_columns: [scenario, metric, mean, ci_low, ci_high]
                                  required_groups:
                                    metric: [answer_relevancy, goal_success_rate]
                                    scenario: [pattern-cot, pattern-react]

Example YAML — CI figure from compute_ci step::

    - name: ci-figure
      type: plotnine
      config:
        data: "@compute-ci"
        mapping:
          x: scenario
          y: mean
          fill: scenario
          ymin: ci_low
          ymax: ci_high
        geom: col
        facet: "metric ~ item_group"
        labels:
          title: "Mean ± 95% CI across 3 runs"
          y: "MCE score"
        output: results/figure-ci.png
      depends_on: [compute-ci]

Example YAML — raw tidy CSV with stat_summary::

    - name: quality-plot
      type: plotnine
      config:
        data: "@collect-metrics"
        mapping: { x: scenario, y: value, fill: scenario }
        geom: col
        stat: summary
        facet: "~ metric"
        labels:
          title: "Topology Comparison"
          x: ""
          y: "Score"
        output: results/figure-quality.png
      depends_on: [collect-metrics]
"""

from __future__ import annotations

import logging
from typing import Any

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path
from mas.lab.benchmark.pipeline.lib.data_source import resolve_dataframe

logger = logging.getLogger(__name__)


class PlotNineStep(PipelineStep):
    """Render a plotnine chart from a file or in-memory DataFrame."""

    type = "plotnine"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:  # noqa: F821
        # Force non-interactive backend before any matplotlib/plotnine import so
        # the step works from worker threads (benchmark run) as well as the main
        # thread (benchmark pipeline run).
        import matplotlib
        matplotlib.use("Agg")

        import numpy as np
        from plotnine import (
            aes,
            coord_cartesian,
            element_text,
            facet_grid,
            facet_wrap,
            geom_bar,
            geom_boxplot,
            geom_col,
            geom_crossbar,
            geom_errorbar,
            geom_errorbarh,
            geom_jitter,
            geom_line,
            geom_point,
            geom_tile,
            ggplot,
            labs,
            scale_fill_brewer,
            stat_summary,
            theme,
            theme_minimal,
        )

        config = self.config

        # ── Input — file path or @step-name reference ──────────────────
        data_source: str = config.get("data", "")
        if not data_source:
            raise ValueError(f"PlotNineStep '{self.name}': 'data' required")

        df = resolve_dataframe(data_source, ctx, config=config, step=self.name)
        logger.info("PlotNineStep '%s': loaded %d rows from '%s'", self.name, len(df), data_source)

        # ── DataFrame schema validation ────────────────────────────────
        # df_schema:
        #   required_columns: [scenario, metric, mean, ci_low, ci_high]
        #   required_groups:
        #     metric: [answer_relevancy, goal_success_rate]
        #     scenario: [pattern-cot, pattern-react]
        schema_cfg: dict = config.get("df_schema", {})
        if schema_cfg:
            required_cols: list = schema_cfg.get("required_columns", [])
            missing_cols = [c for c in required_cols if c not in df.columns]
            if missing_cols:
                raise ValueError(
                    f"PlotNineStep '{self.name}': df_schema validation failed — "
                    f"required column(s) missing from input DataFrame: {missing_cols}. "
                    f"Available: {list(df.columns)}"
                )
            required_groups: dict = schema_cfg.get("required_groups", {})
            for col, expected_values in required_groups.items():
                if col not in df.columns:
                    logger.warning(
                        "PlotNineStep '%s': df_schema.required_groups: column '%s' "
                        "not in DataFrame — skipping group check",
                        self.name, col,
                    )
                    continue
                actual = set(df[col].dropna().unique())
                missing_groups = [v for v in expected_values if v not in actual]
                if missing_groups:
                    logger.warning(
                        "PlotNineStep '%s': df_schema.required_groups: column '%s' "
                        "is missing expected value(s) %s (found: %s)",
                        self.name, col, missing_groups, sorted(actual),
                    )
            logger.info("PlotNineStep '%s': df_schema validation passed", self.name)

        # ── Optional row filter ────────────────────────────────────────
        filter_cfg = config.get("filter", "")
        if isinstance(filter_cfg, dict):
            for col, values in filter_cfg.items():
                if col in df.columns:
                    if isinstance(values, list):
                        df = df[df[col].isin(values)]
                    else:
                        df = df[df[col] == values]
            logger.info("PlotNineStep '%s': filtered to %d rows (filter=%r)", self.name, len(df), filter_cfg)
        elif filter_cfg:
            df = df.query(filter_cfg)
            logger.info("PlotNineStep '%s': filtered to %d rows (filter=%r)", self.name, len(df), filter_cfg)

        # ── Optional derived columns via pandas eval ───────────────────
        # transform: { new_col: "expr using other cols" }
        # Special key "__assign__": list of literal {col: scalar} pairs
        transform_cfg: dict = config.get("transform", {})
        if transform_cfg:
            df = df.copy()
            for new_col, expr in transform_cfg.items():
                try:
                    df[new_col] = df.eval(expr)
                except Exception as e:
                    logger.warning("PlotNineStep '%s': transform %s=%r failed: %s",
                                   self.name, new_col, expr, e)
            logger.info("PlotNineStep '%s': added %d derived columns", self.name, len(transform_cfg))

        # ── Optional value renames ─────────────────────────────────────
        rename_values_cfg: dict = config.get("rename_values", {})
        for col, mapping in rename_values_cfg.items():
            if col in df.columns:
                df = df.copy()
                df[col] = df[col].map(lambda v, m=mapping: m.get(v, v))

        # ── Optional factor ordering (controls axis/facet order) ───────
        factor_levels_cfg: dict = config.get("factor_levels", {})
        if factor_levels_cfg:
            import pandas as pd
            df = df.copy()
            for col, levels in factor_levels_cfg.items():
                if col in df.columns:
                    df[col] = pd.Categorical(df[col], categories=levels, ordered=True)

        # ── Output path ────────────────────────────────────────────────
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "plot.png", config, mkdir=True
        )

        # ── Mapping ────────────────────────────────────────────────────
        mapping_cfg: dict = config.get("mapping", {})
        if not mapping_cfg:
            raise ValueError(
                f"PlotNineStep '{self.name}': 'mapping' required "
                "(e.g. {x: scenario, y: value})"
            )

        # Separate ymin/ymax/xmin/xmax from the base aes — they go to error bars only
        has_errorbars = "ymin" in mapping_cfg and "ymax" in mapping_cfg
        has_h_errorbars = "xmin" in mapping_cfg and "xmax" in mapping_cfg
        base_mapping = {k: v for k, v in mapping_cfg.items() if k not in ("ymin", "ymax", "xmin", "xmax")}
        v_error_mapping = {k: v for k, v in mapping_cfg.items() if k not in ("y", "xmin", "xmax")}
        h_error_mapping = {k: v for k, v in mapping_cfg.items() if k not in ("x", "ymin", "ymax")}

        # ── Drop rows where any mapped column is NaN/None ──────────────
        mapped_cols = [v for v in mapping_cfg.values() if isinstance(v, str) and v in df.columns]
        if mapped_cols:
            before = len(df)
            df = df.dropna(subset=mapped_cols)
            if len(df) < before:
                logger.warning("PlotNineStep '%s': dropped %d rows with NaN in mapped columns %s",
                               self.name, before - len(df), mapped_cols)
        if df.empty:
            logger.warning("PlotNineStep '%s': no data after NaN drop — skipping plot", self.name)
            return StepOutput(data={"output": None}, metadata={"warning": "empty dataframe"})

        p = ggplot(df, aes(**base_mapping))

        # ── Optional x out-of-viewport indicators for main layer ─────
        oov_cfg: dict = config.get("oov_x", {})
        oov_enabled: bool = bool(oov_cfg.get("enabled", False))
        oov_max: float | None = oov_cfg.get("max", None)
        oov_shape: str = str(oov_cfg.get("shape", ">"))
        oov_size: float = float(oov_cfg.get("size", 2.2))
        oov_alpha: float = float(oov_cfg.get("alpha", 0.7))
        x_col_main = base_mapping.get("x") if isinstance(base_mapping, dict) else None
        oov_main_df = None
        if oov_enabled and oov_max is not None and isinstance(x_col_main, str) and x_col_main in df.columns:
            oov_main_df = df[df[x_col_main] > float(oov_max)].copy()
            if not oov_main_df.empty:
                oov_main_df[x_col_main] = float(oov_max)

        # ── Background layers (rendered before main geom) ──────────────
        # Each layer has its own data source, geom, mapping, and alpha.
        # Use this to place a faint raw-scatter behind aggregated CI points:
        #
        #   layers:
        #     - data: "@collect-metrics"
        #       geom: point
        #       mapping: {x: latency_s, y: value, color: scenario}
        #       alpha: 0.12
        #       size: 1.5
        #
        # rename_values and factor_levels from the step config are applied
        # to each layer's DataFrame as well, so colours stay consistent.
        for layer_cfg in config.get("layers", []):
            layer_src = layer_cfg.get("data", "")
            layer_df = resolve_dataframe(layer_src, ctx, config=config, step=self.name) if layer_src else df.copy()

            # Apply per-layer filter
            layer_filter = layer_cfg.get("filter", {})
            if isinstance(layer_filter, dict):
                for col, vals in layer_filter.items():
                    if col in layer_df.columns:
                        mask = layer_df[col].isin(vals) if isinstance(vals, list) else layer_df[col] == vals
                        layer_df = layer_df[mask]
            elif layer_filter:
                layer_df = layer_df.query(layer_filter)

            # Apply same rename_values so colors match the main layer
            for col, mapping in rename_values_cfg.items():
                if col in layer_df.columns:
                    layer_df = layer_df.copy()
                    layer_df[col] = layer_df[col].map(lambda v, m=mapping: m.get(v, v))

            # Apply same factor_levels
            if factor_levels_cfg:
                import pandas as pd
                layer_df = layer_df.copy()
                for col, levels in factor_levels_cfg.items():
                    if col in layer_df.columns:
                        layer_df[col] = pd.Categorical(layer_df[col], categories=levels, ordered=True)

            layer_mapping_cfg: dict = layer_cfg.get("mapping", base_mapping)
            layer_aes = aes(**layer_mapping_cfg)
            layer_geom_name: str = layer_cfg.get("geom", "point")
            layer_alpha: float = float(layer_cfg.get("alpha", 0.2))
            layer_size: float = float(layer_cfg.get("size", 1.5))
            layer_stroke: float = float(layer_cfg.get("stroke", 0.0))

            # Drop NaN in mapped cols
            layer_mapped_cols = [v for v in layer_mapping_cfg.values() if isinstance(v, str) and v in layer_df.columns]
            if layer_mapped_cols:
                layer_df = layer_df.dropna(subset=layer_mapped_cols)

            layer_geom_kwargs: dict = dict(layer_cfg.get("geom_kwargs", {}))

            if layer_df.empty:
                logger.warning("PlotNineStep '%s': layer data empty after filter — skipped", self.name)
            elif layer_geom_name == "point":
                point_kwargs = {"alpha": layer_alpha, "size": layer_size, "stroke": layer_stroke}
                point_kwargs.update(layer_geom_kwargs)
                p = p + geom_point(data=layer_df, mapping=layer_aes, **point_kwargs)
                # Optional x-out-of-viewport marker for this layer
                x_col_layer = (
                    layer_mapping_cfg.get("x") if isinstance(layer_mapping_cfg, dict) else None
                )
                if (
                    oov_enabled
                    and oov_max is not None
                    and isinstance(x_col_layer, str)
                    and x_col_layer in layer_df.columns
                ):
                    oov_layer_df = layer_df[layer_df[x_col_layer] > float(oov_max)].copy()
                    if not oov_layer_df.empty:
                        oov_layer_df[x_col_layer] = float(oov_max)
                        oov_layer_mapping = {
                            k: v for k, v in layer_mapping_cfg.items() if k != "shape"
                        }
                        p = p + geom_point(
                            data=oov_layer_df,
                            mapping=aes(**oov_layer_mapping),
                            alpha=oov_alpha,
                            size=oov_size,
                            shape=oov_shape,
                        )
            elif layer_geom_name == "line":
                line_kwargs: dict = {"alpha": layer_alpha, "size": layer_size}
                if "color" in layer_cfg:
                    line_kwargs["color"] = layer_cfg["color"]
                if "linetype" in layer_cfg:
                    line_kwargs["linetype"] = layer_cfg["linetype"]
                line_kwargs.update(layer_geom_kwargs)
                p = p + geom_line(data=layer_df, mapping=layer_aes, **line_kwargs)
            else:
                logger.warning("PlotNineStep '%s': unknown layer geom '%s' — skipped", self.name, layer_geom_name)

        # ── Geometry ───────────────────────────────────────────────────
        geom_name: str = config.get("geom", "col")
        stat_cfg: str = config.get("stat", "")
        use_stat_summary: bool = stat_cfg in ("summary", "mean", "sum")

        _GEOMS = {
            "col": geom_col,
            "bar": geom_bar,
            "crossbar": geom_crossbar,
            "point": geom_point,
            "boxplot": geom_boxplot,
            "jitter": geom_jitter,
            "line": geom_line,
            "tile": geom_tile,
        }

        point_size: float = float(config.get("point_size", 2.5))
        point_alpha: float = float(config.get("alpha", 0.85))
        main_geom_kwargs: dict = dict(config.get("geom_kwargs", {}))

        if use_stat_summary:
            import pandas as pd
            fun_y = np.sum if stat_cfg == "sum" else np.mean
            p = p + stat_summary(fun_y=fun_y, geom="col", alpha=point_alpha, position="dodge")
        else:
            geom_cls = _GEOMS.get(geom_name, geom_col)
            if geom_name == "crossbar":
                # geom_crossbar draws a rectangle from ymin→ymax with a line at y,
                # so the CI bounds must be part of its own aes() — unlike geom_col
                # which draws from 0→y and uses a separate geom_errorbar overlay.
                crossbar_mapping = dict(base_mapping)
                crossbar_mapping["ymin"] = mapping_cfg["ymin"]
                crossbar_mapping["ymax"] = mapping_cfg["ymax"]
                cb_kwargs = {"alpha": point_alpha, "stat": "identity"}
                cb_kwargs.update(main_geom_kwargs)
                p = p + geom_cls(aes(**crossbar_mapping), **cb_kwargs)
            elif geom_name == "tile":
                p = p + geom_cls(**main_geom_kwargs)
            elif geom_name == "point":
                point_kwargs = {"alpha": point_alpha, "size": point_size, "stat": "identity"}
                point_kwargs.update(main_geom_kwargs)
                p = p + geom_cls(**point_kwargs)
            else:
                geom_kwargs = {"alpha": point_alpha, "stat": "identity"}
                geom_kwargs.update(main_geom_kwargs)
                p = p + geom_cls(**geom_kwargs)

        # Auto-add vertical error bars when ymin/ymax are in the mapping
        # (skip for crossbar — the rectangle already represents the CI range)
        if has_errorbars and geom_name != "crossbar":
            p = p + geom_errorbar(aes(**v_error_mapping), width=0.0)

        # Auto-add horizontal error bars when xmin/xmax are in the mapping
        if has_h_errorbars:
            h_cap_height: float = config.get("errorbarh_height", 0.02)
            p = p + geom_errorbarh(aes(**h_error_mapping), height=h_cap_height)

        # Main-layer out-of-viewport marker (after main geom so marker is visible)
        if oov_main_df is not None and not oov_main_df.empty:
            oov_main_mapping = {k: v for k, v in base_mapping.items() if k != "shape"}
            p = p + geom_point(
                data=oov_main_df,
                mapping=aes(**oov_main_mapping),
                alpha=oov_alpha,
                size=oov_size,
                shape=oov_shape,
            )

        # ── Facet ──────────────────────────────────────────────────────
        facet_spec: str = config.get("facet", "")
        if facet_spec:
            facet_ncol = config.get("ncol", None)
            facet_scales: str = config.get("facet_scales", "free_y")
            if "~" in facet_spec and facet_spec.strip().split("~")[0].strip():
                p = p + facet_grid(facet_spec, scales=facet_scales)
            else:
                var = facet_spec.replace("~", "").strip()
                wrap_kwargs = {"scales": facet_scales}
                if facet_ncol is not None:
                    wrap_kwargs["ncol"] = int(facet_ncol)
                p = p + facet_wrap(var, **wrap_kwargs)

        # ── Labels ─────────────────────────────────────────────────────
        labels_cfg: dict = config.get("labels", {})
        if labels_cfg:
            p = p + labs(**labels_cfg)

        # ── Theme ──────────────────────────────────────────────────────
        from plotnine import element_blank
        p = p + theme_minimal()
        hide_x = config.get("hide_x_labels", False)
        hide_legend = config.get("hide_legend", False)

        legend_pos_cfg = config.get("legend_position", "right")
        legend_pos: Any = legend_pos_cfg
        if isinstance(legend_pos_cfg, list) and len(legend_pos_cfg) == 2:
            legend_pos = (float(legend_pos_cfg[0]), float(legend_pos_cfg[1]))

        theme_kwargs = {
            "figure_size": (config.get("width", 8), config.get("height", 5)),
            "axis_text_x": element_blank() if hide_x else element_text(rotation=30, ha="right"),
            "legend_position": "none" if hide_legend else legend_pos,
        }
        legend_just_cfg = config.get("legend_justification", None)
        if isinstance(legend_just_cfg, list) and len(legend_just_cfg) == 2:
            theme_kwargs["legend_justification"] = (float(legend_just_cfg[0]), float(legend_just_cfg[1]))

        p = p + theme(
            **theme_kwargs,
        )

        x_limits_cfg = config.get("x_limits", None)
        y_limits_cfg = config.get("y_limits", None)
        if isinstance(x_limits_cfg, list) and len(x_limits_cfg) == 2:
            x_limits_tuple = (float(x_limits_cfg[0]), float(x_limits_cfg[1]))
            y_limits_tuple = None
            if isinstance(y_limits_cfg, list) and len(y_limits_cfg) == 2:
                y_limits_tuple = (float(y_limits_cfg[0]), float(y_limits_cfg[1]))
            p = p + coord_cartesian(xlim=x_limits_tuple, ylim=y_limits_tuple)
        elif isinstance(y_limits_cfg, list) and len(y_limits_cfg) == 2:
            y_limits_tuple = (float(y_limits_cfg[0]), float(y_limits_cfg[1]))
            p = p + coord_cartesian(ylim=y_limits_tuple)

        scale_cfg: dict = config.get("scales", {})
        palette_vals: list = config.get("palette", [])
        if palette_vals:
            from plotnine import scale_fill_manual
            p = p + scale_fill_manual(values=palette_vals)
        elif scale_cfg.get("fill") == "brewer":
            p = p + scale_fill_brewer(type="qual", palette="Set2")

        # ── Save ───────────────────────────────────────────────────────
        dpi: int = config.get("dpi", 150)
        p.save(str(output_path), dpi=dpi, verbose=False)

        logger.info("PlotNineStep '%s': saved %s", self.name, output_path)
        return StepOutput(
            data={"output": str(output_path)},
            files=[output_path],
            metadata={"output": str(output_path), "rows": len(df)},
        )

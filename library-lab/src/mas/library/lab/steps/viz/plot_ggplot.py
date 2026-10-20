#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""PlotPipelineStep — wrap any MASPlot as a pipeline step.

Returned by :meth:`MASPlot.to_step()`.  Can also be used directly via
YAML configuration with the ``"ggplot"`` step type::

    pipeline:
      - name: my-figure
        type: ggplot
        depends_on: [analysis]
        config:
          class: "my.package.plots:MyCustomPlot"
          target: paper
          formats: [svg, pdf]
          stem: fig_my_figure

When ``class`` points to a :class:`~mas.lab.plots.mas_plot.MASPlot` subclass,
the step instantiates it, injects upstream DataFrame data (if the plot has
no ``data`` set), calls :meth:`~mas.lab.plots.mas_plot.MASPlot.save`, and
returns the written file paths.

.. note::
    For new experiments prefer the declarative ``type: plotnine`` step which
    expresses the full chart inline in YAML without a Python class.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mas.lab.benchmark.pipeline.artifact_paths import (
    check_simple_name,
    resolve_step_folder,
    resolve_step_input_path,
)
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput

if TYPE_CHECKING:
    from mas.lab.plots.mas_plot import MASPlot
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


class PlotPipelineStep(PipelineStep):
    """Execute a :class:`~mas.lab.plots.mas_plot.MASPlot` and write its output
    to the pipeline's output directory.

    Construction (programmatic)
    ---------------------------
    Use :meth:`MASPlot.to_step` for the most concise form::

        step = my_plot.to_step("quality_bar", depends_on=["analysis"])

    Or construct directly::

        from mas.library.lab.steps.viz.plot_ggplot import PlotPipelineStep

        step = PlotPipelineStep(
            name="quality_bar",
            config={"formats": ["svg", "pdf"], "stem": "quality_bar"},
            depends_on=["analysis"],
            plot=my_plot,
        )

    Construction (YAML)
    -------------------
    ::

        - name: quality_bar
          type: ggplot
          depends_on: [analysis]
          config:
            class: "my.package.plots:MyCustomPlot"
            target: paper
    """

    type = "ggplot"

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        depends_on: list[str] | None = None,
        phase: str = "post",
        plot: "MASPlot | None" = None,
    ) -> None:
        super().__init__(name, config, depends_on, phase)
        self._plot = plot

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        from mas.lab.plots.mas_plot import MASPlot

        # Resolve plot instance
        plot: MASPlot = self._plot or self._import_plot(ctx)

        # Inject upstream data when the plot has no static data
        if plot.data is None and self.depends_on:
            upstream_data = self._collect_data(ctx)
            if upstream_data is not None:
                plot.data = upstream_data
                logger.debug(
                    "PlotPipelineStep '%s': injected upstream data (%d rows)",
                    self.name, len(upstream_data) if hasattr(upstream_data, "__len__") else "?",
                )

        out_dir = resolve_step_folder(ctx, self.name, self.config, "plots", mkdir=True)
        stem    = check_simple_name(self.name, "stem", self.config.get("stem", self.name))
        formats = self.config.get("formats", plot.default_formats)
        width   = self.config.get("width")
        height  = self.config.get("height")

        paths = plot.save(
            out_dir, stem=stem, formats=formats,
            width=width, height=height,
        )
        logger.info(
            "PlotPipelineStep '%s': wrote %d file(s) → %s",
            self.name, len(paths), out_dir,
        )
        return StepOutput(
            data={
                "plot_paths": [str(p) for p in paths],
                "stem": stem,
            },
            files=paths,
            metadata={
                "plot_class": type(plot).__name__,
                "target":     plot.target,
                "formats":    formats,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _import_plot(self, ctx: "ExecutionContext") -> "MASPlot":
        """Import a MASPlot subclass from ``config["class"]`` and instantiate."""
        import importlib
        from mas.lab.plots.mas_plot import MASPlot

        class_path = self.config.get("class")
        if not class_path:
            raise ValueError(
                f"PlotPipelineStep '{self.name}': requires either a 'plot' "
                "instance (via to_step()) or a 'class' config key "
                "(e.g. 'my.package.plots:MyCustomPlot')."
            )
        module_path, _, class_name = class_path.rpartition(":")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        if not (isinstance(cls, type) and issubclass(cls, MASPlot)):
            raise TypeError(
                f"'{class_path}' is not a MASPlot subclass."
            )
        target = self.config.get("target", cls.default_target)
        results_dir = resolve_step_input_path(ctx, self.name, self.config, "results_dir")
        data = None
        if results_dir is not None:
            data = results_dir
        elif ctx.output_dir and ctx.output_dir.exists():
            data = ctx.output_dir
        return cls(data=data, target=target)

    def _collect_data(self, ctx: "ExecutionContext"):
        """Collect the first usable DataFrame from upstream step outputs."""
        import pandas as pd

        for dep_name in self.depends_on:
            dep_out = ctx.step_outputs.get(dep_name)
            if dep_out is None:
                continue
            for key in ("dataframe", "df", "data", "records"):
                val = dep_out.data.get(key)
                if isinstance(val, pd.DataFrame) and not val.empty:
                    return val
                if isinstance(val, list) and val:
                    try:
                        return pd.DataFrame(val)
                    except Exception:
                        logger.debug('suppressed', exc_info=True)
        return None

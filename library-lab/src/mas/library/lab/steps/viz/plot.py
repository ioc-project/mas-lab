#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""
PlotStep — declarative plot pipeline step (plot library specs only).

Config
------
spec : str
    Name of a plot spec in ``plot_library/`` (e.g. ``shapley_bars``).

data_key : str
    Override the data key defined in the spec.

params : dict
    Deep-merged into the spec before rendering.

filename : str
    Override the output filename.

Example YAML
------------
.. code-block:: yaml

    - name: shapley
      type: plot
      depends_on: [annotate]
      config:
        spec: shapley_bars
        params:
          title: "MCE Shapley contributions"
"""


import logging
from pathlib import Path
from typing import Any, Dict

from mas.lab.benchmark.pipeline.artifact_paths import check_simple_name, resolve_step_folder
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


class PlotStep(PipelineStep):
    """Generate a visualisation from a plot-library spec and upstream step data."""

    type = "plot"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        config = self.config
        spec_name = config.get("spec")
        if not spec_name:
            raise ValueError(
                f"Step '{self.name}': plot step requires config.spec "
                "(plot library spec name). Use type: plotnine for ggplot-style figures."
            )
        return await self._execute_spec(str(spec_name), config, ctx)

    async def _execute_spec(
        self,
        spec_name: str,
        config: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> StepOutput:
        from mas.lab.benchmark.pipeline.lib.plot_lib import PlotRegistry, render_plot

        spec = PlotRegistry.load(spec_name)
        params = config.get("params", {})
        if params:
            spec = spec.override(params)

        data_key_override = config.get("data_key")
        if data_key_override:
            spec.data["key"] = data_key_override

        data: Any = None
        for dep_name in self.depends_on:
            dep_out = ctx.step_outputs.get(dep_name)
            if dep_out and dep_out.data:
                data = dep_out.data
                logger.info(
                    "Step '%s': using data from dependency '%s'",
                    self.name, dep_name,
                )
                break

        if data is None:
            raise ValueError(
                f"Step '{self.name}': no dependency data found for plot spec '{spec_name}'"
            )

        output_dir = resolve_step_folder(ctx, self.name, config, "plots", mkdir=True)
        filename = config.get("filename")
        if filename:
            check_simple_name(self.name, "filename", filename)
        output_file = render_plot(spec, data, output_dir, filename=filename)

        return StepOutput(
            data={"plot_path": str(output_file)},
            files=[output_file],
            metadata={
                "spec": spec_name,
                "plot_type": spec.plot_type,
                "title": spec.title,
            },
        )

    def outputs_exist(self, output_dir: Path) -> bool:
        plot_dir = output_dir / "plots"
        if not plot_dir.exists():
            return False
        return len(list(plot_dir.glob(f"{self.name}.*"))) > 0

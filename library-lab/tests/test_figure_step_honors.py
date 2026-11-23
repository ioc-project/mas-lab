#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.lab.benchmark.pipeline import FigureStep
from mas.library.lab.steps.viz.ci_plot import CIPlotStep
from mas.library.lab.steps.viz.metrics_comparison_plot import MetricsComparisonPlotStep
from mas.library.lab.steps.viz.pipeline_diagram import PipelineDiagramStep
from mas.library.lab.steps.viz.plot import PlotStep
from mas.library.lab.steps.viz.plot_communication_flow import PlotCommunicationFlowStep
from mas.library.lab.steps.viz.plot_ggplot import PlotPipelineStep
from mas.library.lab.steps.viz.plot_message_graph import PlotMessageGraphStep
from mas.library.lab.steps.viz.plot_multilevel_trajectory import PlotMultilevelTrajectoryStep
from mas.library.lab.steps.viz.plot_trajectory import PlotTrajectoryStep
from mas.library.lab.steps.viz.plotnine import PlotNineStep


def test_plot_steps_are_figure_steps() -> None:
    steps = [
        CIPlotStep("ci", {}),
        MetricsComparisonPlotStep("cmp", {}),
        PipelineDiagramStep("diag", {}),
        PlotStep("plot", {}),
        PlotCommunicationFlowStep("flow", {}),
        PlotPipelineStep("gg", {}),
        PlotMessageGraphStep("msg", {}),
        PlotMultilevelTrajectoryStep("ml", {}),
        PlotTrajectoryStep("tr", {}),
        PlotNineStep("pn", {}),
    ]
    assert all(isinstance(step, FigureStep) for step in steps)
    assert "format" in PlotTrajectoryStep.HONORS
    assert "title" in PlotCommunicationFlowStep.HONORS
    assert "theme" in PipelineDiagramStep.HONORS
    assert "title" in CIPlotStep.HONORS
    assert "palette" in PlotNineStep.HONORS

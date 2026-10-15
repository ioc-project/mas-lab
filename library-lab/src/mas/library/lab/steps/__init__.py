#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OSS pipeline step library — one step class per module, grouped by category.

Categories: ``extract/``, ``eval/``, ``viz/``, ``data/``, ``services/``.
Shared helpers live in ``mas.lab.benchmark.pipeline.lib`` (the bench pipeline
*engine*, which these steps depend on but which contains no step
implementations of its own).

These classes are registered as ``step`` plugins through the runtime
registry via ``library-lab/library.yaml``'s ``types:``/``plugins:`` block
(see ``mas.runtime.registry.bootstrap`` and ``runtime/docs/plugin-registry-
manifests.md``); this module's exports exist for direct/typed imports and
for the manifest's ``module:``/``class:`` targets to resolve against.

Internal-only steps (``embed_states``, ``list_clickhouse_sessions``, …) ship in
``mas-lab-internal/lab-components/bench-steps`` and are declared in manifest
YAML the same way, so they load through the same runtime registry mechanism.
"""

from mas.library.lab.steps.data.analysis import AnalysisStep
from mas.library.lab.steps.data.collect_dataframe import CollectDataFrameStep
from mas.library.lab.steps.data.dataset import DatasetStep
from mas.library.lab.steps.data.deserialize import DeserializeStep
from mas.library.lab.steps.data.diff_trajectories import DiffTrajectoriesStep
from mas.library.lab.steps.data.embed_trajectories import EmbedStep
from mas.library.lab.steps.data.experiment import ExperimentStep
from mas.library.lab.steps.data.gather_level import GatherLevelStep
from mas.library.lab.steps.data.generate_dataset import GenerateDatasetStep
from mas.library.lab.steps.data.join_dataframe import JoinDataFrameStep
from mas.library.lab.steps.data.processor import ProcessorStep
from mas.library.lab.steps.data.serialize import SerializeStep
from mas.library.lab.steps.data.to_dataframe import ToDataFrameStep
from mas.library.lab.steps.eval.annotate_metrics import AnnotateMetricsStep
from mas.library.lab.steps.eval.collect_metrics import CollectMetricsStep
from mas.library.lab.steps.eval.compute_ci import ComputeCIStep
from mas.library.lab.steps.eval.compute_drift import ComputeDriftStep
from mas.library.lab.steps.eval.mce import EvalMceStep
from mas.library.lab.steps.eval.metrics_to_dataframe import MetricsToDataFrameStep
from mas.library.lab.steps.extract.mealy_stats import ExtractMealyStatsStep
from mas.library.lab.steps.extract.sys_stats import ExtractSysStatsStep
from mas.library.lab.steps.extract.trace_stats import ExtractTraceStatsStep
from mas.library.lab.steps.extract.trajectories import ExtractTrajectoriesStep
from mas.library.lab.steps.services.events_to_otel import EventsToOtelStep
from mas.library.lab.steps.services.export_otel import ExportOtelStep
from mas.library.lab.steps.services.service_start import ServiceStartStep
from mas.library.lab.steps.services.shell import ShellStep
from mas.library.lab.steps.services.service_stop import ServiceStopStep
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

__all__ = [
    "AnalysisStep",
    "AnnotateMetricsStep",
    "CIPlotStep",
    "CollectDataFrameStep",
    "CollectMetricsStep",
    "ComputeCIStep",
    "ComputeDriftStep",
    "DatasetStep",
    "DeserializeStep",
    "DiffTrajectoriesStep",
    "EmbedStep",
    "EvalMceStep",
    "MetricsToDataFrameStep",
    "EventsToOtelStep",
    "ExperimentStep",
    "ExportOtelStep",
    "ExtractMealyStatsStep",
    "ExtractSysStatsStep",
    "ExtractTraceStatsStep",
    "ExtractTrajectoriesStep",
    "GatherLevelStep",
    "GenerateDatasetStep",
    "JoinDataFrameStep",
    "MetricsComparisonPlotStep",
    "PipelineDiagramStep",
    "PlotCommunicationFlowStep",
    "PlotMessageGraphStep",
    "PlotMultilevelTrajectoryStep",
    "PlotNineStep",
    "PlotPipelineStep",
    "PlotStep",
    "PlotTrajectoryStep",
    "ProcessorStep",
    "SerializeStep",
    "ServiceStartStep",
    "ShellStep",
    "ServiceStopStep",
    "ToDataFrameStep",
]

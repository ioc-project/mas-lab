#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab configuration for MAS runs — interactive demo and batch experiment.

Two concrete run types share a common base (``MASRunBase``):

- ``LabConfig``          (lab-config.yaml)  MAS live UI, one run at a time
- ``MASExperimentConfig``(mas-experiment.yaml)  MAS batch benchmarking, all scenarios × flavours

Scenario resolution
-------------------
Use :func:`discover_scenario_stems` and :func:`load_scenario_config` for any
code that needs to enumerate or load scenarios from a directory.  Scenario
manifests are YAML overlays (``*.yaml`` under ``overlays/`` or an explicit
``configs_dir`` path).

Both share EvaluationSpec and the dataset loading conventions from the benchmark
package, so evaluation criteria, emulator manifests, etc. are identical.

LabConfig YAML structure::

    lab:
      name: "trip-planner"
      description: "Trip planner MAS demo"

      applications:
        - app: trip-planner
          configs_dir: "./overlays"
          base_scenario: "baseline"

      scenarios:
        - id: "baseline"
          description: "Standard baseline"
        - id: "challenge-communication"
          description: "Agents with divergent ontologies"

      dataset:
        path: "./datasets/prompts.yaml"

      evaluation:
        method: "metrics"
        config:
          criteria: [correctness, response_time, collaboration_quality]

      ui:
        port: 8088
        mode: "interactive"          # "interactive" | "automated"
        layout: "dag"
        node_positions:
          planner:   {x: 400, y: 100}
          search:    {x: 200, y: 250}

MASExperimentConfig YAML structure::

    experiment:
      name: "trip-planner-benchmark"
      description: "Batch evaluation of trip-planner scenarios"
      default_flavour: local

      application:
        app: trip-planner
        configs_dir: "./overlays"

      scenarios:
        - id: "baseline"
        - id: "challenge-communication"

      dataset:
        path: "./datasets/benchmark.yaml"

      evaluation:
        method: "llm_judge"
        config:
          criteria: [correctness, collaboration_quality]

      run:
        n_runs: 3

      schedule:
        parallel_scenarios: 4
        timeout: 300
        ordering: coverage

      bench_emulation:                    # layered resource emulation
        infra:                             # L1: backing resources
          llm: live                        # live | mock | replay
          tools: live                      # live | mock | stub
          memory: live                     # live | mock | snapshot | seeded | ephemeral
          embeddings: live                 # live | mock | replay
          state: live                      # live | snapshot | seeded | ephemeral | mock
        runtime:                           # L3: execution engine
          transport: local                 # local | grpc | emulated
          cache: content-addressed         # content-addressed | disabled | forced
        intercept:                         # L3–L6: hook-level interception
          mitm: false
          hooks: []

      application:
        post:
          - id: analysis
"""

from .artifact_types import list_artifact_types, register_artifact_type
from .execution import (
    EmulationSpec,
    ExperimentDesignSpec,
    ExperimentScheduleSpec,
    InfraEmulationSpec,
    InterceptSpec,
    MASExecutionSpec,
    PipelineEngineSpec,
    ReplaySpec,
    RuntimeEmulationSpec,
    split_legacy_execution,
)
from .experiment_base import MASRunBase
from .lab_config import LabConfig
from .lab_context import LabContext, discover_lab_context, inject_lab_context, inject_lab_libraries
from .mas_experiment import MASExperimentConfig
from .pipeline import (
    ArtifactSpec,
    LevelSpec,
    PipelineStepSpec,
)
from .scenario import MASScenarioSpec, MASSpec, OverlayStack
from .scenario_loading import (
    discover_scenario_stems,
    load_scenario_config,
    load_stacked_config,
)
from .ui import UISpec

__all__ = [
    "ArtifactSpec",
    "EmulationSpec",
    "ExperimentDesignSpec",
    "ExperimentScheduleSpec",
    "InfraEmulationSpec",
    "InterceptSpec",
    "LabConfig",
    "LabContext",
    "LevelSpec",
    "MASExecutionSpec",
    "MASExperimentConfig",
    "MASRunBase",
    "MASScenarioSpec",
    "MASSpec",
    "OverlayStack",
    "PipelineEngineSpec",
    "PipelineStepSpec",
    "ReplaySpec",
    "RuntimeEmulationSpec",
    "UISpec",
    "split_legacy_execution",
    "discover_lab_context",
    "discover_scenario_stems",
    "inject_lab_context",
    "inject_lab_libraries",
    "list_artifact_types",
    "load_scenario_config",
    "load_stacked_config",
    "register_artifact_type",
]

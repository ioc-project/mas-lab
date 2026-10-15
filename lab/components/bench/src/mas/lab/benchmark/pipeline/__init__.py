#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""
Pipeline system for declarative evaluation workflows.
"""


from mas.lab.benchmark.pipeline.cache import CacheManager
from mas.lab.benchmark.pipeline.core import (
    BatchPipelineStep,
    Pipeline,
    PipelineStep,
    YAMLIncludeLoader,
    get_step,
    include_constructor,
    list_steps,
    register_step,
    register_step_type,
    resolve_step_class,
)
from mas.lab.benchmark.pipeline.executor import (
    BoundPipeline,
    ExecutionContext,
    ExecutionPlan,
    PipelineExecutor,
)
from mas.lab.benchmark.pipeline.models import (
    _CONFIG_KNOWN_KEYS,
    _MISSING,
    _STEP_KNOWN_KEYS,
    ArtifactSpec,
    ConfigParam,
    PipelineConfig,
    StepManifest,
    StepOutput,
)
from mas.lab.benchmark.pipeline.resolver import DependencyResolver
from mas.lab.benchmark.pipeline.artifact import Artifact, ArtifactContext, ArtifactType
from mas.lab.benchmark.pipeline.channel import (
    InprocChannel,
    LogChannel,
    StreamFrame,
    TelemetryChannel,
    get_channel,
    install_channel,
    make_channel,
    native_body,
    set_channel,
)
from mas.lab.benchmark.pipeline.config import PipelineEngineSpec, pipeline_settings
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.scheduler import Scheduler
from mas.lab.benchmark.pipeline.store import ArtifactStore

# Register built-in artifact types.
from mas.lab.benchmark.pipeline import types as _types  # noqa: F401

__all__ = [
    "_MISSING",
    "_STEP_KNOWN_KEYS",
    "_CONFIG_KNOWN_KEYS",
    "ConfigParam",
    "Pipeline",
    "PipelineStep",
    "BatchPipelineStep",
    "StepManifest",
    "ArtifactSpec",
    "StepOutput",
    "PipelineConfig",
    "register_step",
    "register_step_type",
    "get_step",
    "list_steps",
    "resolve_step_class",
    "YAMLIncludeLoader",
    "include_constructor",
    "PipelineExecutor",
    "BoundPipeline",
    "ExecutionContext",
    "ExecutionPlan",
    "DependencyResolver",
    "CacheManager",
    "Artifact",
    "ArtifactContext",
    "ArtifactStore",
    "ArtifactType",
    "InprocChannel",
    "LogChannel",
    "PipelineEngineSpec",
    "RunKey",
    "Scheduler",
    "StreamFrame",
    "TelemetryChannel",
    "get_channel",
    "install_channel",
    "make_channel",
    "native_body",
    "pipeline_settings",
    "set_channel",
]

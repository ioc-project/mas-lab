#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas.library.eval.mce — MCE integration (session metrics via telemetry-hub)."""

from mas.library.eval.mce.catalog import ALL_SESSION_METRICS, METRIC_MAP, METRIC_REGISTRY
from mas.library.eval.mce.judge_model import (
    ResolvedJudgeModel,
    apply_eval_mce_model_defaults,
    resolve_judge_model,
    unique_application_model,
)
from mas.library.eval.mce.registry_api import (
    build_session_from_trace,
    compute_session_metrics,
)
from mas.library.eval.mce.runner import (
    METRICS_SCHEMA_VERSION,
    build_metrics_document,
    get_effective_judge_model,
    install_openai_llm_service,
    judge_client,
    llm_service_config,
)
from mas.library.eval.mce.runner import (
    compute_session_metrics as compute_trace_metrics,
)
from mas.library.eval.mce.trace_provider import MASTraceProvider

__all__ = [
    "MASTraceProvider",
    "ALL_SESSION_METRICS",
    "METRIC_MAP",
    "METRIC_REGISTRY",
    "METRICS_SCHEMA_VERSION",
    "ResolvedJudgeModel",
    "apply_eval_mce_model_defaults",
    "build_metrics_document",
    "build_session_from_trace",
    "compute_session_metrics",
    "compute_trace_metrics",
    "get_effective_judge_model",
    "install_openai_llm_service",
    "judge_client",
    "llm_service_config",
    "resolve_judge_model",
    "unique_application_model",
]

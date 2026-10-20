#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Single MCE metric catalog.

User-facing metric ids (CLI ``--metric``, YAML ``metrics:``, ``metrics.json``)
are snake_case. CamelCase is the plugin class name used by the SessionEntity API.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MceMetricSpec:
    """One MCE plugin class. ``snake`` is the metric id; ``camel`` is the class name."""

    camel: str
    spec: str
    snake: str | None = None
    unit: str = "mas"
    evidence: str = "io"


_SESSION: tuple[MceMetricSpec, ...] = (
    MceMetricSpec(
        "AnswerRelevancy",
        "mce_metrics_plugin.session.answer_relevancy:AnswerRelevancy",
        snake="answer_relevancy",
    ),
    MceMetricSpec(
        "GoalSuccessRate",
        "mce_metrics_plugin.session.goal_success_rate:GoalSuccessRate",
        snake="goal_success_rate",
    ),
    MceMetricSpec(
        "Groundedness",
        "mce_metrics_plugin.session.groundedness:Groundedness",
        snake="groundedness",
    ),
    MceMetricSpec(
        "ResponseCompleteness",
        "mce_metrics_plugin.session.response_completeness:ResponseCompleteness",
        snake="response_completeness",
    ),
    MceMetricSpec(
        "WorkflowCohesionIndex",
        "mce_metrics_plugin.session.workflow_cohesion_index:WorkflowCohesionIndex",
        snake="workflow_cohesion_index",
    ),
    MceMetricSpec(
        "WorkflowEfficiency",
        "mce_metrics_plugin.session.workflow_efficiency:WorkflowEfficiency",
        snake="workflow_efficiency",
    ),
    MceMetricSpec(
        "Consistency",
        "mce_metrics_plugin.session.consistency:Consistency",
        snake="consistency",
    ),
    MceMetricSpec(
        "ContextPreservation",
        "mce_metrics_plugin.session.context_preservation:ContextPreservation",
        snake="context_preservation",
    ),
    MceMetricSpec(
        "InformationRetention",
        "mce_metrics_plugin.session.information_retention:InformationRetention",
        snake="information_retention",
    ),
    MceMetricSpec(
        "IntentRecognitionAccuracy",
        "mce_metrics_plugin.session.intent_recognition_accuracy:IntentRecognitionAccuracy",
        snake="intent_recognition_accuracy",
    ),
    MceMetricSpec(
        "ComponentConflictRate",
        "mce_metrics_plugin.session.component_conflict_rate:ComponentConflictRate",
        snake="component_conflict_rate",
    ),
)

_CORE: tuple[MceMetricSpec, ...] = (
    MceMetricSpec(
        "ToolUtilizationAccuracy",
        "metrics_computation_engine.metrics.span.tool_utilization_accuracy:ToolUtilizationAccuracy",
        snake="tool_utilization_accuracy",
        unit="call",
        evidence="io",
    ),
    MceMetricSpec(
        "ToolError",
        "metrics_computation_engine.metrics.span.tool_error:ToolError",
        snake="tool_error",
        unit="call",
        evidence="io",
    ),
    MceMetricSpec(
        "ToolErrorRate",
        "metrics_computation_engine.metrics.session.tool_error_rate:ToolErrorRate",
        snake="tool_error_rate",
    ),
    MceMetricSpec(
        "AgentToAgentInteractions",
        "metrics_computation_engine.metrics.session.agent_to_agent_interactions:AgentToAgentInteractions",
        snake="agent_to_agent_interactions",
        evidence="trajectory",
    ),
    MceMetricSpec(
        "AgentToToolInteractions",
        "metrics_computation_engine.metrics.session.agent_to_tool_interactions:AgentToToolInteractions",
        snake="agent_to_tool_interactions",
        evidence="trajectory",
    ),
    MceMetricSpec(
        "CyclesCount",
        "metrics_computation_engine.metrics.session.cycles:CyclesCount",
        snake="cycles_count",
        evidence="trajectory",
    ),
)

CATALOG: tuple[MceMetricSpec, ...] = _SESSION + _CORE

METRIC_MAP: Dict[str, str] = {item.snake: item.spec for item in _SESSION if item.snake}
METRIC_REGISTRY: Dict[str, str] = {item.camel: item.spec for item in CATALOG}
ALL_SESSION_METRICS: List[str] = [item.snake for item in _SESSION if item.snake]
_BY_SNAKE: Dict[str, MceMetricSpec] = {item.snake: item for item in CATALOG if item.snake}
_BY_CAMEL: Dict[str, MceMetricSpec] = {item.camel: item for item in CATALOG}
_BY_SPEC: Dict[str, MceMetricSpec] = {**_BY_SNAKE, **_BY_CAMEL}


def listed_ids(*, session_only: bool = False) -> List[str]:
    """Snake_case metric ids accepted by the CLI and YAML ``metrics:`` lists."""
    pool = _SESSION if session_only else CATALOG
    return [item.snake for item in pool if item.snake]


def lookup(name: str) -> MceMetricSpec | None:
    """Return the catalog entry for a snake_case id or CamelCase class name."""
    return _BY_SPEC.get(name)


def resolve_metric(name: str, *, session_only: bool = False) -> MceMetricSpec:
    """Resolve a snake_case metric id or raise ``ValueError``.

    CamelCase class names and other spellings are rejected; the error lists
    the valid ids and the closest match. With *session_only* only the
    LLM-judge session metrics are accepted.
    """
    import difflib

    pool = _SESSION if session_only else CATALOG
    spec = _BY_SNAKE.get(name)
    if spec is not None and spec in pool:
        return spec
    valid = listed_ids(session_only=session_only)
    close = difflib.get_close_matches(name, valid, n=1, cutoff=0.5)
    if not close:
        camel = _BY_CAMEL.get(name)
        if camel is not None and camel in pool and camel.snake:
            close = [camel.snake]
    msg = f"Unknown metric {name!r}."
    if close:
        msg += f" Did you mean {close[0]!r}?"
    msg += f" Valid metrics: {', '.join(valid)}."
    raise ValueError(msg)


def session_scope(name: str) -> tuple[str, str]:
    """``(unit, evidence)`` for a catalog id. Stock session ids are MAS I/O."""
    spec = lookup(name)
    if spec is None:
        return "mas", "io"
    return spec.unit, spec.evidence


def import_metric(name: str) -> Any | None:
    """Import the MCE class for a snake_case id or CamelCase class name, or ``None``."""
    spec = lookup(name)
    if spec is None:
        logger.warning("Unknown MCE metric: %r. Known: %s", name, list(METRIC_MAP) + list(METRIC_REGISTRY))
        return None
    module_path, cls_name = spec.spec.rsplit(":", 1)
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, cls_name)
    except Exception as exc:
        logger.error("Import error for metric %s (%s): %s", name, spec.spec, exc)
        return None


async def run_mce_class(
    name: str,
    session: Any,
    *,
    jury: Any = None,
    llm_config: Any = None,
) -> Any | None:
    """Instantiate a catalog metric, bind the judge, and ``compute(session)``."""
    metric_cls = import_metric(name)
    if metric_cls is None:
        return None
    metric = metric_cls()
    if llm_config is not None and hasattr(metric, "create_model"):
        model = metric.create_model(llm_config)
        metric.init_with_model(model)
    elif jury is not None and hasattr(metric, "init_with_model"):
        metric.init_with_model(jury)
    return await metric.compute(session)

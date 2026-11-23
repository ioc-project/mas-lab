#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCE CamelCase API — session metrics + wired span/session core metrics."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.library.eval.errors import judge_error_text
from mas.library.eval.mce.catalog import METRIC_REGISTRY as METRIC_REGISTRY
from mas.library.eval.mce.catalog import run_mce_class
from metrics_computation_engine.entities.models.session import SessionEntity
from metrics_computation_engine.models.eval import MetricResult
from metrics_computation_engine.models.requests import LLMJudgeConfig

logger = logging.getLogger(__name__)

__all__ = ["METRIC_REGISTRY", "build_session_from_trace", "compute_session_metrics"]


async def compute_session_metrics(
    session: SessionEntity,
    metrics: List[str],
    llm_config: Dict[str, Any] | LLMJudgeConfig,
    *,
    include_reasoning: bool = True,
) -> List[MetricResult]:
    """Compute MCE metrics on a :class:`SessionEntity`."""
    del include_reasoning  # reserved for future judge options
    if isinstance(llm_config, dict):
        llm_config = LLMJudgeConfig(**llm_config)

    results: List[MetricResult] = []
    for metric_name in metrics:
        try:
            result = await run_mce_class(metric_name, session, llm_config=llm_config)
        except Exception as exc:
            error = judge_error_text(exc)
            logger.error("Metric %s failed: %s", metric_name, error)
            results.append(
                MetricResult(
                    metric_name=metric_name,
                    value=None,
                    aggregation_level="session",
                    category="error",
                    app_name=getattr(session, "app_name", "unknown"),
                    description=f"Computation failed: {error}",
                    unit="",
                    reasoning="",
                    span_id="",
                    session_id=[session.session_id],
                    source="native",
                    entities_involved=[],
                    edges_involved=[],
                    success=False,
                    metadata={},
                    error_message=error,
                )
            )
            continue
        if result is None:
            logger.error("Unknown metric: %s", metric_name)
            continue
        results.append(result)

    return results


def build_session_from_trace(
    trace_path: str | Path,
    *,
    session_id_filter: Optional[str] = None,
) -> SessionEntity:
    """Build a :class:`SessionEntity` from ``events.jsonl``."""
    from mas.library.eval.mce.trace_provider import MASTraceProvider
    from metrics_computation_engine.entities.core.trace_processor import (
        TraceProcessor,
        create_pseudo_grouped_sessions_from_file,
    )

    trace_path = Path(trace_path)
    if not trace_path.exists():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")

    raw_traces: List[Dict[str, Any]] = []
    with trace_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw_traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not raw_traces:
        raise ValueError(f"No valid traces found in {trace_path}")

    grouped_sessions = create_pseudo_grouped_sessions_from_file(raw_traces)
    processor = TraceProcessor()
    session_set = processor.process_grouped_sessions(
        grouped_sessions,
        session_id_filter=session_id_filter,
    )
    if not session_set.sessions:
        raise ValueError(f"No sessions found in trace {trace_path}")

    session = session_set.sessions[0]
    provider = MASTraceProvider()
    ctx = provider.fetch(str(trace_path), requirements=None)
    tool_spans = ctx.get("tool_spans") or []
    if tool_spans and not getattr(session, "spans", None):
        session.spans = tool_spans
    return session

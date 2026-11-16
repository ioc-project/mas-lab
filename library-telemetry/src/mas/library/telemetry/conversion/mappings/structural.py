#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Structural-block handlers — the call-tree backbone.

Emits the spans that form the structural skeleton of a run: the top-level
``TaskCall`` (``mas_call``), each ``AgentCall`` (``execution``), and ``Worker``
presence points (``infrastructure_info``).

Ontology block: ``structural`` / ``execution`` (orchestrator summand).
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


@register("mas_call_start")
def h_mas_call_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "TaskCall",
        {
            "mas.boundary": "TaskCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.run.id": ev.get("run_id", ""),
            "mas.session.id": ev.get("session_id", ""),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("mas_call_end")
def h_mas_call_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    output = ev.get("state_out_content") or ev.get("result") or ev.get("output") or ev.get("content") or ""
    conv.close_span(
        ev.get("call_id"),
        {
            "mas.output": str(output),
        },
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


@register("execution_start")
def h_execution_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "AgentCall",
        {
            "mas.boundary": "AgentCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.run.id": ev.get("run_id", ""),
            "mas.input": str(ev.get("input", "")),
            **({} if not ev.get("dp_id") else {"mas.dp.id": ev["dp_id"]}),
            **conv.agent_start_state_attrs(ev),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("execution_end")
def h_execution_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    # The visit's final State is its output; the reply is not read from user_response.
    output = ev.get("state_in_content") or ev.get("output") or ev.get("result") or ev.get("content") or ""
    extra: Dict[str, Any] = {"mas.output": str(output), **conv.agent_end_state_attrs(ev)}
    if ev.get("failure_reason"):
        extra["mas.failure.reason"] = str(ev["failure_reason"])[:500]
    if ev.get("failure_category"):
        extra["mas.failure.category"] = str(ev["failure_category"])
    conv.close_span(
        ev.get("call_id"),
        extra,
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


@register("infrastructure_info")
def h_infrastructure_info(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "Worker",
        {
            "mas.boundary": "Worker",
            "mas.agent.id": conv.agent_id(ev),
            "mas.worker.id": str(ev.get("worker_id", "")),
            "mas.worker.pid": int(ev.get("worker_pid") or 0),
            "mas.worker.durable_backend": ev.get("durable_backend") or "",
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


__all__ = [
    "h_mas_call_start",
    "h_mas_call_end",
    "h_execution_start",
    "h_execution_end",
    "h_infrastructure_info",
]

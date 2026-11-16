#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trajectory & annotation handlers.

Routing decisions, user I/O and wait-state markers emit one ``CallAnnotation``
point span per record. Parallel-group fork/join events emit a ``ParallelGroup``
interval span (only when those events exist). ``user_response`` is no node (the
reply is the visit's final State), so it is not exported.

Ontology blocks: ``trajectory`` (parallel groups) and ``execution``
(routing / user I/O annotations).
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


@register("routing", "routing_result", "user_input", "user_output", "client_response")
def h_point_annotation(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """Routing decisions and user I/O: one CallAnnotation point per record."""
    conv.emit_annotation(ev, "CallAnnotation", {"mas.annotation.kind": str(ev.get("kind") or "")})


@register("parallel_group_start")
def h_parallel_group_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """Interval ParallelGroup span. Emitted only when a fork event exists."""
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "ParallelGroup",
        {
            "mas.boundary": "ParallelGroup",
            "mas.agent.id": conv.agent_id(ev),
            "mas.parallel.group_id": ev.get("group_id", ""),
            "mas.parallel.phase": ev.get("phase", ""),
            "mas.parallel.agent_ids": ",".join(str(a) for a in (ev.get("agent_ids") or [])),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("parallel_group_end", "parallel_group_merge")
def h_parallel_group_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        ev.get("call_id") or conv.require_call_id(ev),
        extra={
            "mas.parallel.group_id": ev.get("group_id", ""),
            "mas.parallel.phase": ev.get("phase", ""),
        },
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


@register("wait_state_start", "wait_state_end")
def h_wait_state(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """A wait marker carries no data: one CallAnnotation point per record."""
    conv.emit_annotation(ev, "CallAnnotation", {"mas.annotation.kind": str(ev.get("kind") or "")})


__all__ = [
    "h_point_annotation",
    "h_parallel_group_start",
    "h_parallel_group_end",
    "h_wait_state",
    "h_user_response",
]


@register("user_response")
def h_user_response(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """No span: the reply is the visit's final State (``execution_end``), not an annotation."""

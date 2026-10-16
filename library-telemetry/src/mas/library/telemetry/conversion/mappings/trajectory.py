#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trajectory & annotation handlers.

Routing decisions, user I/O, parallel-group fork/join markers, and the
``obs_wrap_gov_*`` observability wrapper events.  Most emit ``CallAnnotation``
point spans that record *what happened around* the structural calls.

Ontology blocks: ``trajectory`` (parallel groups) and ``execution``
(routing / user I/O annotations).
"""

from __future__ import annotations

import json
from typing import Any, Dict

from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


@register("routing")
def h_routing(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "routing",
            "mas.annotation.content": json.dumps(
                {
                    "routing_type": ev.get("routing_type", ""),
                    "selected_agent": ev.get("selected_agent", ""),
                    "confidence": ev.get("confidence"),
                    "candidates": ev.get("candidates", []),
                }
            ),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("routing_result")
def h_routing_result(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "routing_result",
            "mas.annotation.content": json.dumps(
                {
                    "selected_agent": ev.get("selected_agent", ""),
                    "target_agent_id": ev.get("target_agent_id", ""),
                    "routing_type": ev.get("routing_type", ""),
                }
            ),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("user_input")
def h_user_input(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "user_input",
            "mas.annotation.content": str(ev.get("content") or ev.get("input", ""))[
                :2000
            ],
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("user_output", "client_response", "user_response")
def h_user_output(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    content = str(ev.get("content") or ev.get("output", ""))
    ensure = getattr(conv, "ensure_resumed_agent_call", None)
    dest = ev.get("parent_call_id") or ev.get("call_id")
    if callable(ensure):
        dest = ensure(ev) or dest
    conv.record_agent_output(dest, content)
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": ev.get("kind", "user_output"),
            "mas.annotation.content": content,
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("parallel_group_start", "parallel_group_end", "parallel_group_merge")
def h_parallel_group(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": ev.get("kind", "parallel_group"),
            "mas.parallel.group_id": ev.get("group_id", ""),
            "mas.parallel.phase": ev.get("phase", ""),
            "mas.parallel.agent_ids": ",".join(
                str(a) for a in (ev.get("agent_ids") or [])
            ),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
        call_id=ev.get("call_id") or None,
    )


@register("wait_state_start")
def h_wait_state_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.open_span(
        conv.span_key(ev),
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "wait_state",
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("wait_state_end")
def h_wait_state_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        conv.span_key(ev),
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


def h_obs_wrap_gov(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """``obs_wrap_gov_*`` wrapper events → CallAnnotation point spans.

    Matched by ``kind`` *prefix* rather than exact name, so it is dispatched
    from the converter's fallback path rather than the exact-match registry.
    """
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": ev.get("kind", "obs_wrap_gov"),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
        call_id=ev.get("call_id"),
    )


__all__ = [
    "h_routing",
    "h_routing_result",
    "h_user_input",
    "h_user_output",
    "h_parallel_group",
    "h_wait_state_start",
    "h_wait_state_end",
    "h_obs_wrap_gov",
]

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Context / semantic-block handlers.

Context assembly, per-part context contributions, state updates, and
compaction.  These are mostly point-in-time annotations that enrich the
structural tree with what the agent knew and when.

Ontology block: ``context`` (context summand) → OTel export layer ``semantic``.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


@register("context_assembled")
def h_context_assembled(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    attach = getattr(conv, "attach_assembled_llm_context", None)
    if callable(attach):
        attach(ev)
    segments = ev.get("segments") or []
    if isinstance(segments, int):
        segment_count = segments
        total_tokens = int(ev.get("total_tokens") or 0)
    else:
        segment_count = len(segments)
        total_tokens = ev.get("total_tokens") or sum(
            int(s.get("tokens") or 0) for s in segments
        )
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "context_assembled",
            "mas.annotation.content": json.dumps(
                {
                    "segments": segment_count,
                    "total_tokens": total_tokens,
                }
            ),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("context_part_contributed")
def h_context_part_contributed(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    attrs: Dict[str, Any] = {
        "mas.boundary": "ContextContribution",
        "mas.agent.id": conv.agent_id(ev),
        "mas.context.part_id": ev.get("part_id") or ev.get("call_id", ""),
        "mas.context.source": ev.get("source", ""),
        "mas.context.section_id": ev.get("section_id", ""),
        "mas.context.source_type": ev.get("source_type", "unknown"),
        "mas.context.access_mechanism": ev.get("access_mechanism", "inject"),
        "mas.context.cause": ev.get("cause", "context_manager"),
        "mas.context.cause_type": ev.get("cause_type", "deterministic"),
        "mas.context.token_estimate": int(
            ev.get("token_estimate") or ev.get("tokens") or 0
        ),
        "mas.context.retained": ev.get("retained", True),
    }
    llm_call_id = ev.get("llm_call_id") or ""
    if llm_call_id:
        attrs["mas.context.llm_call_id"] = llm_call_id
    conv.point_span(
        "ContextContribution", attrs, ev.get("parent_call_id"), ts_ns=conv.ts_ns(ev)
    )


@register("state_update_start")
def h_state_update_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    _state_update_annotation(conv, ev, "state_update_start")


@register("state_update_end")
def h_state_update_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    _state_update_annotation(conv, ev, "state_update_end")


def _state_update_annotation(conv: SpanEmitter, ev: Dict[str, Any], kind: str) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": kind,
            "mas.annotation.content": json.dumps(
                {
                    "state_key": ev.get("state_key", ""),
                    "state_value": ev.get("state_value", ""),
                }
            ),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("compaction")
def h_compaction(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "compaction",
            "mas.compaction.total_messages": int(ev.get("total_messages") or 0),
            "mas.compaction.compressed_count": int(ev.get("compressed_count") or 0),
            "mas.compaction.tokens_before": int(ev.get("tokens_before") or 0),
            "mas.compaction.tokens_after": int(ev.get("tokens_after") or 0),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )


@register("context_steer")
def h_context_steer(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.point_span(
        "CallAnnotation",
        {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "context_steer",
            "mas.annotation.content": conv.enc(
                ev.get("content") or ev.get("steer") or ev.get("instruction") or "",
            ),
        },
        ev.get("parent_call_id"),
        ts_ns=conv.ts_ns(ev),
    )

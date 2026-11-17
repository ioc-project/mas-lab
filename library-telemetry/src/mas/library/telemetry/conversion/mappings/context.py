#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Context / semantic-block handlers.

Context assembly, per-part context contributions, state updates, and
compaction.  These are mostly point-in-time annotations that enrich the
structural tree with what the agent knew and when.

Ontology block: ``context`` (context summand) → OTel export layer ``semantic``.
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


@register("context_assembled")
def h_context_assembled(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    attach = getattr(conv, "attach_assembled_llm_context", None)
    if callable(attach):
        attach(ev)
    usage = ev.get("context_usage") if isinstance(ev.get("context_usage"), dict) else {}
    archive_view_id = str(ev.get("archive_view_id") or usage.get("archive_view_id") or "")
    attrs: Dict[str, Any] = {"mas.annotation.kind": "context_assembled"}
    if archive_view_id:
        attrs["mas.archive.view_id"] = archive_view_id
    conv.emit_annotation(ev, "CallAnnotation", attrs)


@register("context_part_contributed")
def h_context_part_contributed(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """One part of the context of an LLM call: it annotates that call (``llm_call_id``).

    The attributes are the ones the record states; none is defaulted.
    """
    attrs: Dict[str, Any] = {
        "mas.boundary": "ContextContribution",
        "mas.agent.id": conv.agent_id(ev),
        "mas.annotation.kind": str(ev.get("kind") or "context_part_contributed"),
        "mas.context.part_id": ev.get("part_id") or ev.get("call_id", ""),
    }
    for key, attr in (
        ("source", "mas.context.source"),
        ("access_mechanism", "mas.context.access_mechanism"),
        ("token_estimate", "mas.context.token_estimate"),
        ("retained", "mas.context.retained"),
    ):
        if ev.get(key) is not None and ev.get(key) != "":
            attrs[attr] = ev[key]
    payload = semconv.annotation_payload(ev, semconv.CONTEXT_PAYLOAD_FIELDS)
    if payload:
        attrs["mas.annotation.content"] = payload
    llm_call_id = str(ev.get("llm_call_id") or "")
    if not llm_call_id:
        conv.point_span("ContextContribution", attrs, ev.get("parent_call_id"), ts_ns=conv.ts_ns(ev))
        return
    attrs["mas.context.llm_call_id"] = llm_call_id
    # The part is contributed to that LLM call: it annotates the call's span
    # (``ioa_observe.annotates.span_id``, the exported span id).
    conv.point_span_annotating(llm_call_id, "ContextContribution", attrs, ev.get("parent_call_id"), conv.ts_ns(ev))


@register("state_update_start", "state_update_end")
def h_state_update(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.emit_annotation(ev, "CallAnnotation", {"mas.annotation.kind": str(ev.get("kind") or "")})


@register("compaction")
def h_compaction(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.emit_annotation(
        ev,
        "CallAnnotation",
        {
            "mas.annotation.kind": "compaction",
            "mas.compaction.total_messages": int(ev.get("total_messages") or 0),
            "mas.compaction.compressed_count": int(ev.get("compressed_count") or 0),
            "mas.compaction.tokens_before": int(ev.get("tokens_before") or 0),
            "mas.compaction.tokens_after": int(ev.get("tokens_after") or 0),
        },
    )


@register("context_steer")
def h_context_steer(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.emit_annotation(ev, "CallAnnotation", {"mas.annotation.kind": "context_steer"})

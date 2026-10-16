#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Memory & retrieval handlers — MemoryCall and RAGQuery spans.

Covers memory writes (``memory_store``), memory reads / retrievals
(``memory_read``, ``memory_retrieve``, legacy ``memory_call``), and
retrieval-augmented-generation queries (``rag_query``).

Ontology block: ``execution`` (tool / context summands).
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


# ── Memory writes ────────────────────────────────────────────────────────────


@register("memory_store_start")
def h_memory_store_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "MemoryCall",
        {
            "mas.boundary": "MemoryCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.memory.type": ev.get("memory_type") or "episodic",
            "mas.memory.operation": "write",
            "mas.memory.input": conv.enc(
                ev.get("data") or ev.get("content")
            ),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("memory_store_end")
def h_memory_store_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        ev.get("call_id"), status=ev.get("status", "success"), end_ns=conv.ts_ns(ev)
    )


# ── Memory reads / retrievals ────────────────────────────────────────────────
# memory_retrieve_* and legacy memory_call_* are aliases for memory_read_*.


@register("memory_read_start", "memory_retrieve_start", "memory_call_start")
def h_memory_read_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "MemoryCall",
        {
            "mas.boundary": "MemoryCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.memory.type": ev.get("memory_type") or "episodic",
            "mas.memory.operation": "read",
            "mas.memory.key": ev.get("key") or "",
            "mas.memory.query": ev.get("query") or "",
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("memory_read_end", "memory_retrieve_end", "memory_call_end")
def h_memory_read_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    result = ev.get("result", "")
    extra: Dict[str, Any] = {
        "mas.memory.output": result
        if isinstance(result, str)
        else conv.enc(result),
    }
    if ev.get("result_count"):
        extra["mas.memory.result_count"] = int(ev["result_count"])
    conv.close_span(
        ev.get("call_id"),
        extra,
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


# ── RAG queries ──────────────────────────────────────────────────────────────


@register("rag_query_start")
def h_rag_query_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "RAGQuery",
        {
            "mas.boundary": "RAGQuery",
            "mas.agent.id": conv.agent_id(ev),
            "mas.memory.type": "rag",
            "mas.memory.query": ev.get("query") or "",
            "mas.memory.collection": ev.get("collection") or "",
            "mas.memory.top_k": int(ev.get("top_k") or 0),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("rag_query_end")
def h_rag_query_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    result = ev.get("result", "")
    extra: Dict[str, Any] = {
        "mas.memory.output": result
        if isinstance(result, str)
        else conv.enc(result),
    }
    if ev.get("result_count"):
        extra["mas.memory.result_count"] = int(ev["result_count"])
    conv.close_span(
        ev.get("call_id"),
        extra,
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Memory & retrieval handlers — MemoryCall and RAGQuery spans.

Covers memory writes (``memory_store``), memory reads / retrievals
(``memory_read``, ``memory_retrieve``, ``memory_call``), and
retrieval-augmented-generation queries (``rag_query``).

``mas.memory.operation`` uses the ontology vocabulary (``store``,
``retrieve``, ``update``, ``delete``): a write is ``store``, a read and a RAG
query are ``retrieve``. A native record that names its own ``operation`` in
that vocabulary keeps it.

Every call closes with its output: what a read returned, or what a write
stored (the write's own ``output`` / ``result`` when it reports one, else the
data it was given). With the explicit ``state_*`` fields the output State
content comes from them as well.

Ontology block: ``execution`` (tool / context summands).
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register

_ONTOLOGY_OPERATIONS = frozenset(
    {
        semconv.MEMORY_OPERATION_STORE,
        semconv.MEMORY_OPERATION_RETRIEVE,
        semconv.MEMORY_OPERATION_UPDATE,
        semconv.MEMORY_OPERATION_DELETE,
    }
)


def _operation(ev: Dict[str, Any], default: str) -> str:
    stated = str(ev.get("operation") or "").strip().lower()
    return stated if stated in _ONTOLOGY_OPERATIONS else default


def _store(ev: Dict[str, Any]) -> str:
    """The memory store the record names (``memory_name``, else ``collection``, else
    ``memory_type``), as the native KG fold names its ``Memory``."""
    return str(ev.get("memory_name") or ev.get("collection") or ev.get("memory_type") or "")


def _text(conv: SpanEmitter, value: Any) -> str:
    if value in (None, ""):
        return ""
    return value if isinstance(value, str) else conv.enc(value)


def _first(ev: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if ev.get(key) not in (None, ""):
            return ev[key]
    return None


# ── Memory writes ────────────────────────────────────────────────────────────


@register("memory_store_start")
def h_memory_store_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "MemoryCall",
        {
            "mas.boundary": "MemoryCall",
            **conv.state_start_attrs(ev),
            "mas.agent.id": conv.agent_id(ev),
            "mas.memory.type": ev.get("memory_type") or "",
            "mas.memory.collection": _store(ev),
            "mas.memory.operation": _operation(ev, semconv.MEMORY_OPERATION_STORE),
            "mas.memory.key": ev.get("key") or "",
            "mas.memory.input": _text(conv, _first(ev, "data", "content", "input")),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("memory_store_end")
def h_memory_store_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    output = _first(ev, "output", "result", "data", "content")
    extra: Dict[str, Any] = {}
    if output is not None:
        extra["mas.memory.output"] = _text(conv, output)
    else:
        # The write reports nothing back: its output is the data it stored.
        stored = getattr(conv, "open_span_attr", lambda _c, _k: "")(ev.get("call_id"), "mas.memory.input")
        if stored:
            extra["mas.memory.output"] = stored
    extra.update(conv.state_end_attrs(ev))
    conv.close_span(
        ev.get("call_id"),
        extra,
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


# ── Memory reads / retrievals ────────────────────────────────────────────────


@register("memory_read_start", "memory_retrieve_start", "memory_call_start")
def h_memory_read_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "MemoryCall",
        {
            "mas.boundary": "MemoryCall",
            **conv.state_start_attrs(ev),
            "mas.agent.id": conv.agent_id(ev),
            "mas.memory.type": ev.get("memory_type") or "",
            "mas.memory.collection": _store(ev),
            "mas.memory.operation": _operation(ev, semconv.MEMORY_OPERATION_RETRIEVE),
            "mas.memory.key": ev.get("key") or "",
            "mas.memory.query": ev.get("query") or "",
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("memory_read_end", "memory_retrieve_end", "memory_call_end")
def h_memory_read_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    extra: Dict[str, Any] = {"mas.memory.output": _text(conv, _first(ev, "result", "output"))}
    if ev.get("result_count"):
        extra["mas.memory.result_count"] = int(ev["result_count"])
    extra.update(conv.state_end_attrs(ev))
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
            **conv.state_start_attrs(ev),
            "mas.agent.id": conv.agent_id(ev),
            "mas.memory.type": ev.get("memory_type") or "",
            "mas.memory.operation": semconv.MEMORY_OPERATION_RETRIEVE,
            "mas.memory.query": ev.get("query") or "",
            "mas.memory.collection": _store(ev),
            "mas.memory.top_k": int(ev.get("top_k") or 0),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("rag_query_end")
def h_rag_query_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    extra: Dict[str, Any] = {"mas.memory.output": _text(conv, _first(ev, "result", "output"))}
    if ev.get("result_count"):
        extra["mas.memory.result_count"] = int(ev["result_count"])
    extra.update(conv.state_end_attrs(ev))
    conv.close_span(
        ev.get("call_id"),
        extra,
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )

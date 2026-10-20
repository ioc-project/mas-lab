#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event transforms — pure boundary → native → otel (no I/O)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

_SYNTHETIC_SPAN_DURATION_S = 0.001


def _event_kind(record: dict) -> str:
    return str(record.get("kind") or "")


@runtime_checkable
class EventTransform(Protocol):
    """Map one or more input records to output records (chainable, side-effect free)."""

    transform_id: str

    def transform(self, record: dict, *, ctx: TransformContext) -> list[dict]: ...


@dataclass
class TransformContext:
    agent_id: str = "agent"
    run_id: str = ""
    turn_id: str = ""
    mas_call_id: str = ""
    exec_call_id: str = ""
    _seen_engine_ops: set[tuple[int, str]] = field(default_factory=set)
    _seen_engine_returns: set[tuple[int, str]] = field(default_factory=set)
    _call_ids: dict[tuple[int, str], str] = field(default_factory=dict)
    _op_seq: dict[tuple[int, str], int] = field(default_factory=dict)

    def next_interval_call_id(self, correlation_id: int, op_key: str) -> str:
        """UUID for ``(correlation_id, op_key)``; later events get ``op_key#n``."""
        n = self._op_seq.get((correlation_id, op_key), 0) + 1
        self._op_seq[(correlation_id, op_key)] = n
        key = op_key if n == 1 else f"{op_key}#{n}"
        return _interval_call_id(self, correlation_id, key)


def _parent_call_id(record: dict, ctx: TransformContext) -> str | None:
    if record.get("parent_call_id"):
        return str(record["parent_call_id"])
    if ctx.exec_call_id:
        return ctx.exec_call_id
    if ctx.mas_call_id:
        return ctx.mas_call_id
    return None


def _with_parent(base: dict, record: dict, ctx: TransformContext) -> dict:
    parent = _parent_call_id(record, ctx)
    if parent:
        base["parent_call_id"] = parent
    return base


def _resolve_call_id(record: dict, ctx: TransformContext, correlation_id: int, op_key: str) -> str:
    if record.get("call_id"):
        return str(record["call_id"])
    return _interval_call_id(ctx, correlation_id, op_key)


def _interval_call_id(ctx: TransformContext, correlation_id: int, op_key: str) -> str:
    """Stable UUID call_id per (correlation_id, op) for *_start / *_end pairing."""
    key = (correlation_id, op_key)
    if key not in ctx._call_ids:
        ctx._call_ids[key] = str(uuid.uuid4())
    return ctx._call_ids[key]


class BoundaryPassthroughTransform:
    """Emit v2 boundary audit events as JSON (schema: observability)."""

    transform_id = "boundary"

    def transform(self, record: dict, *, ctx: TransformContext) -> list[dict]:
        if record.get("_source") != "boundary":
            return [record]
        out = dict(record)
        out.pop("_source", None)
        out.setdefault("agent_id", ctx.agent_id)
        out.setdefault("run_id", ctx.run_id)
        return [out]


# ---------------------------------------------------------------------------
# Session event handlers — module-level functions for dispatch table
# ---------------------------------------------------------------------------

_SessionHandler = Callable[["dict", "TransformContext"], list[dict]]


def _sh_mas_call_start(record: dict, ctx: TransformContext) -> list[dict]:
    call_id = str(record.get("call_id") or ctx.mas_call_id or f"mas-{ctx.run_id}")
    ctx.mas_call_id = call_id
    return [{
        "kind": "mas_call_start",
        "agent_id": "mas",
        "run_id": ctx.run_id,
        "turn_id": ctx.turn_id,
        "call_id": call_id,
    }]


def _sh_mas_call_end(record: dict, ctx: TransformContext) -> list[dict]:
    call_id = str(record.get("call_id") or ctx.mas_call_id or f"mas-{ctx.run_id}")
    return [{
        "kind": "mas_call_end",
        "agent_id": "mas",
        "run_id": ctx.run_id,
        "turn_id": ctx.turn_id,
        "call_id": call_id,
        "status": record.get("status", "success"),
    }]


def _sh_user_input(record: dict, ctx: TransformContext) -> list[dict]:
    call_id = str(record.get("call_id") or "")
    turn_id = str(record.get("turn_id") or "")
    exec_id = call_id or f"{ctx.agent_id}-{turn_id or ctx.turn_id or 'turn'}-exec"
    ctx.exec_call_id = exec_id
    if turn_id:
        ctx.turn_id = turn_id
    # Reset per-turn tracking state
    if hasattr(ctx._seen_engine_ops, "clear"):
        ctx._seen_engine_ops.clear()
    if hasattr(ctx._seen_engine_returns, "clear"):
        ctx._seen_engine_returns.clear()
    if hasattr(ctx._call_ids, "clear"):
        ctx._call_ids.clear()
    if hasattr(ctx._op_seq, "clear"):
        ctx._op_seq.clear()
    rec: dict = {
        "kind": "execution_start",
        "agent_id": ctx.agent_id,
        "run_id": ctx.run_id,
        "turn_id": ctx.turn_id,
        "call_id": exec_id,
        "input": record.get("text", ""),
    }
    # A delegated sub-agent turn carries the delegating tool call's own
    # call_id explicitly (see RuntimeInstance.run_user_text); an explicit
    # parent always wins over the top-level mas_call_id fallback, so the
    # call tree can tell "this agent runs nested under that tool call" apart
    # from "this agent is a session-level sibling" (see _build_call_tree /
    # _is_delegation_tool, which depend on this distinction).
    parent = record.get("parent_call_id") or ctx.mas_call_id
    if parent:
        rec["parent_call_id"] = str(parent)
    return [rec]


def _sh_agent_response(record: dict, ctx: TransformContext) -> list[dict]:
    exec_id = ctx.exec_call_id or f"{ctx.agent_id}-{ctx.turn_id or 'turn'}-exec"
    base = {"agent_id": ctx.agent_id, "run_id": ctx.run_id, "turn_id": ctx.turn_id}
    parent = {"parent_call_id": exec_id} if exec_id else {}
    return [
        {
            "kind": "user_response",
            **base,
            "call_id": f"{ctx.turn_id}-resp",
            **parent,
            "content": record.get("text", ""),
            "finish_reason": record.get("finish_reason", "stop"),
        },
        {
            "kind": "execution_end",
            **base,
            "call_id": exec_id,
            **parent,
            "status": "ok",
        },
    ]


def _sh_control(record: dict, ctx: TransformContext) -> list[dict]:
    method = str(record.get("method") or "")
    category = str(record.get("category") or (f"control.{method}" if method else "control"))
    return [
        {
            "kind": "control",
            "category": category,
            "method": method,
            "control_kind": record.get("control_kind") or method,
            "actor": record.get("actor") or "",
            "surface": record.get("surface") or "",
            "denied": bool(record.get("denied")),
            "reason": record.get("reason") or "",
            "session_id": record.get("session_id") or "",
            "agent_id": ctx.agent_id,
            "run_id": ctx.run_id,
        }
    ]


_SESSION_KIND_HANDLERS: dict[str, _SessionHandler] = {
    "mas_call_start": _sh_mas_call_start,
    "mas_call_end": _sh_mas_call_end,
    "user_input": _sh_user_input,
    "agent_response": _sh_agent_response,
    "control": _sh_control,
}


class NativeObservabilityTransform:
    """Map v2 boundary + session records to mas-lab native events.jsonl shape.

    Note: requires a mutable ``TransformContext`` for dedup and session call-id
    tracking — an intentional exception to the side-effect-free ``EventTransform``
    protocol (state lives in ctx, not in transform-local fields).
    """

    transform_id = "native"

    def transform(self, record: dict, *, ctx: TransformContext) -> list[dict]:
        source = record.get("_source")
        if source == "session":
            return self._session(record, ctx)
        if source == "boundary":
            from mas.library.standard.lib.observability.native.boundary_handlers import (
                dispatch_boundary,
            )

            return dispatch_boundary(record, ctx=ctx)
        return []

    def _session(self, record: dict, ctx: TransformContext) -> list[dict]:
        kind = str(record.get("session_kind") or "")
        handler = _SESSION_KIND_HANDLERS.get(kind)
        if handler is None:
            return []
        return handler(record, ctx)

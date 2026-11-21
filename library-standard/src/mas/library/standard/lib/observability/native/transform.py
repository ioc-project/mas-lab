#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event transforms — pure boundary → native → otel (no I/O)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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
    # Explicit dataflow State chain of the current agent visit (see _state_open).
    _state_current: str = ""
    _state_seq: int = 0
    _state_by_call: dict[str, dict] = field(default_factory=dict)
    _state_group: dict | None = None
    # Ended parallel groups still waiting for a member to return (see _flush_closed_groups).
    _state_pending_groups: list = field(default_factory=list)
    # State id -> content, recorded when the producing element states its content.
    _state_contents: dict[str, str] = field(default_factory=dict)
    # Session-wide registry shared by every agent's context (one dict per run).
    # "tool_calls": tool call_id -> caller agent_id, exec_call_id and the call's State pair.
    shared: dict = field(default_factory=dict)
    # Set on a delegated visit: the delegating tool call (see _sh_user_input).
    _delegate: dict | None = None

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


# ---------------------------------------------------------------------------
# State identity — explicit dataflow chain per agent visit
#
# A visit (execution_start .. execution_end, keyed by exec_call_id) is a chain
# of States with ids ``{exec_call_id}#{n}``. n counts from 0 in emission order.
# Sequential data elements take a new State at START and chain from the current
# State. Elements inside a parallel group read the group's input State and do
# not move the current State; the group end allocates a join State.
# ---------------------------------------------------------------------------


_SHARED_LOCK = threading.Lock()
# How long a delegated visit waits for its caller's tool call to be registered.
_DELEGATION_WAIT_S = 2.0


def _shared_cond(shared: dict) -> threading.Condition:
    with _SHARED_LOCK:
        return shared.setdefault("cond", threading.Condition())


def register_tool_call(shared: dict, call_id: str, info: dict | None) -> None:
    """Record a tool call (``None``: a call that moves no data, such as a spawn) for delegated visits.

    Each agent dispatches its events on its own worker thread, so a callee's first event can
    be processed before its caller's tool call was. The callee waits for this registration
    (see :func:`find_tool_call`) instead of guessing.
    """
    cond = _shared_cond(shared)
    with cond:
        shared.setdefault("tool_calls", {})[call_id] = info
        cond.notify_all()


def find_tool_call(shared: dict, call_id: str, timeout: float = _DELEGATION_WAIT_S) -> dict | None:
    """The registered tool call ``call_id``, waiting up to ``timeout`` s for its registration."""
    cond = _shared_cond(shared)
    with cond:
        cond.wait_for(lambda: call_id in shared.get("tool_calls", {}), timeout=timeout)
        return shared.get("tool_calls", {}).get(call_id)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _state_new_id(ctx: TransformContext) -> str:
    sid = f"{ctx.exec_call_id}#{ctx._state_seq}"
    ctx._state_seq += 1
    return sid


def _state_open(ctx: TransformContext, call_id: str) -> dict:
    """Take the State pair of a data element at its START.

    The pair is stored under ``call_id`` so the END record of the same call
    carries the same ``state_in`` / ``state_out``.
    """
    group = ctx._state_group
    if group is None:
        state_in = ctx._state_current
        state_out = _state_new_id(ctx)
        ctx._state_current = state_out
    else:
        state_in = group["group_in"]
        state_out = _state_new_id(ctx)
    pair = {
        "state_in": state_in,
        "state_out": state_out,
        "state_out_content": "",
        # "" when the producer of state_in has not ended yet.
        "state_in_content": ctx._state_contents.get(state_in, ""),
    }
    if group is not None:
        group["members"].append(pair)
    if call_id:
        ctx._state_by_call[call_id] = pair
    return pair


def _state_set_content(ctx: TransformContext, pair: dict, content: str) -> None:
    pair["state_out_content"] = content
    pair["closed"] = True
    ctx._state_contents[pair["state_out"]] = content


def _state_fields(pair: dict, content: str | None = None) -> dict:
    fields: dict = {
        "state_in": pair["state_in"],
        "state_in_content": pair["state_in_content"],
        "state_out": pair["state_out"],
    }
    if content is not None:
        fields["state_out_content"] = content
    return fields


def _state_close(ctx: TransformContext, call_id: str, content: str) -> dict:
    """State fields for the END record of a data element.

    Falls back to opening the element here when its START was never seen.
    """
    pair = ctx._state_by_call.get(call_id) if call_id else None
    if pair is None:
        pair = _state_open(ctx, call_id)
    _state_set_content(ctx, pair, content)
    return _state_fields(pair, content)


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
    return [
        {
            "kind": "mas_call_start",
            "agent_id": "mas",
            "run_id": ctx.run_id,
            "turn_id": ctx.turn_id,
            "call_id": call_id,
        }
    ]


def _sh_mas_call_end(record: dict, ctx: TransformContext) -> list[dict]:
    call_id = str(record.get("call_id") or ctx.mas_call_id or f"mas-{ctx.run_id}")
    out = {
        "kind": "mas_call_end",
        "agent_id": "mas",
        "run_id": ctx.run_id,
        "turn_id": ctx.turn_id,
        "call_id": call_id,
        "status": record.get("status", "success"),
    }
    # The MAS call's boundary States are the root visits' first input and last final State.
    root = ctx.shared.get("root")
    if root:
        out["state_in"] = root["first_id"]
        out["state_in_content"] = root["first_content"]
        out["state_out"] = root["last_id"]
        out["state_out_content"] = root["last_content"]
    return [out]


def _sh_now(record: dict) -> float:
    """Timestamp of a session record: its own field, else the wall clock now."""
    return float(record.get("timestamp") or time.time())


def _agent_boundary_pair(
    ctx: TransformContext, *, call_id: str, caller: dict, ts: float, name: str, fields: dict
) -> list[dict]:
    """A ProcessingCall start/end pair for the CALLER of a delegation.

    Both records carry the same timestamp: the hand-off is one instant.
    """
    base = {
        "agent_id": caller["agent_id"],
        "run_id": ctx.run_id,
        "call_id": call_id,
        "parent_call_id": caller["exec_call_id"],
        "processing_name": name,
        "processing_type": "agent_boundary",
        "timestamp": ts,
    }
    return [
        {"kind": "processing_call_start", **base, **fields},
        {"kind": "processing_call_end", **base, **fields},
    ]


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
    # New visit: the State chain restarts at #0 (the agent input).
    ctx._state_seq = 0
    ctx._state_by_call.clear()
    ctx._state_group = None
    ctx._state_pending_groups.clear()
    ctx._state_contents.clear()
    input_state = _state_new_id(ctx)
    ctx._state_current = input_state
    ctx._state_contents[input_state] = record.get("text", "")
    text = record.get("text", "")
    # A delegated visit: its parent call is a tool call this session registered.
    # The caller's delegate pair is emitted before this visit starts.
    parent_call_id = str(record.get("parent_call_id") or "")
    tool_call = find_tool_call(ctx.shared, parent_call_id) if parent_call_id else None
    ctx._delegate = None
    out: list[dict] = []
    if tool_call is None:
        # A root visit (not delegated): the first one opens the MAS call's trajectory.
        ctx.shared.setdefault("root", {"first_id": input_state, "first_content": text})
    if tool_call is not None:
        ctx._delegate = {**tool_call, "call_id": parent_call_id}
        out = _agent_boundary_pair(
            ctx,
            call_id=f"{parent_call_id}:delegate",
            caller=tool_call,
            ts=_sh_now(record),
            name="delegate",
            fields={
                "input": tool_call["state_in_content"],
                "output": text,
                "state_in": tool_call["state_in"],
                "state_in_content": tool_call["state_in_content"],
                "state_out": input_state,
                "state_out_content": text,
            },
        )
    rec: dict = {
        "kind": "execution_start",
        "agent_id": ctx.agent_id,
        "run_id": ctx.run_id,
        "turn_id": ctx.turn_id,
        "call_id": exec_id,
        "input": record.get("text", ""),
        "state_out": input_state,
        "state_out_content": record.get("text", ""),
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
    # Set only when an agent loop other than the kernel loop took the turn.
    if record.get("loop"):
        rec["loop"] = str(record["loop"])
    return [*out, rec]


def _sh_agent_response(record: dict, ctx: TransformContext) -> list[dict]:
    exec_id = ctx.exec_call_id or f"{ctx.agent_id}-{ctx.turn_id or 'turn'}-exec"
    base = {"agent_id": ctx.agent_id, "run_id": ctx.run_id, "turn_id": ctx.turn_id}
    parent = {"parent_call_id": exec_id} if exec_id else {}
    text = record.get("text", "")
    final_content = ctx._state_contents.get(ctx._state_current, "")
    out: list[dict] = []
    delegate = ctx._delegate
    if delegate is None:
        # A root visit ends: its final State is the MAS call's last one so far.
        root = ctx.shared.get("root")
        if root is not None:
            root["last_id"] = ctx._state_current
            root["last_content"] = final_content
    if delegate is not None:
        # The caller's reply pair: this visit's final State flows back to the
        # delegating tool call's output State.
        ctx._delegate = None
        out = _agent_boundary_pair(
            ctx,
            call_id=f"{delegate['call_id']}:reply",
            caller=delegate,
            ts=_sh_now(record),
            name="reply",
            fields={
                "input": final_content,
                "output": text,
                "state_in": ctx._state_current,
                "state_in_content": final_content,
                "state_out": delegate["state_out"],
                "state_out_content": text,
            },
        )
    return [
        {
            "kind": "user_response",
            **base,
            "call_id": f"{ctx.turn_id}-resp",
            **parent,
            "content": text,
            "finish_reason": record.get("finish_reason", "stop"),
        },
        {
            "kind": "execution_end",
            **base,
            "call_id": exec_id,
            **parent,
            "status": "ok",
            # The visit's final State: no new id is allocated here.
            "state_in": ctx._state_current,
            "state_in_content": final_content,
            "state_out": ctx._state_current,
        },
        *out,
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

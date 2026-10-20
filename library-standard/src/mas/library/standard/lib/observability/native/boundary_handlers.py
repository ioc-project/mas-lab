#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Boundary event kind dispatch — one handler per ``ObsEventKind``."""

from __future__ import annotations

import time
from collections.abc import Callable

from mas.library.standard.lib.observability.native.tool_name import resolve_tool_name
from mas.library.standard.lib.observability.native.transform import (
    TransformContext,
    _SYNTHETIC_SPAN_DURATION_S,
    _event_kind,
    _interval_call_id,
    _resolve_call_id,
    _with_parent,
)
from mas.runtime.schema.observability import ObsEventKind


def dispatch_boundary(record: dict, *, ctx: TransformContext) -> list[dict]:
    """Route one boundary record to its kind handler."""
    kind = _event_kind(record)
    handler = _BOUNDARY_KIND_HANDLERS.get(kind)
    if handler is None:
        return []
    cid = int(record.get("correlation_id") or 0)
    base = {"agent_id": ctx.agent_id, "run_id": ctx.run_id, "correlation_id": cid}
    payload = record.get("payload") or {}
    # Use the real occurrence-time timestamp threaded through by
    # boundary_dict_from_transition (TransitionEvent.timestamp, captured
    # synchronously when the event fired) — time.time() here was a fresh
    # read taken whenever the async export pipeline happened to process this
    # record, which lags real occurrence order and previously made an
    # agent's own tool_call_end look like it landed after that agent's own
    # execution_end, even though it truly happened first.
    ts = record.get("timestamp") or time.time()
    return handler(record, ctx=ctx, cid=cid, base=base, payload=payload, ts=ts)


def _boundary_engine_io(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    out: list[dict] = []
    op = payload.get("op", "")
    key = (cid, op)
    if op == "MEMORY_OP" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        rec = {
            "kind": "memory_call_start",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "MEMORY_OP"),
            "timestamp": ts,
        }
        out.append(_with_parent(rec, record, ctx))
    if op == "LLM_CALL" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        messages = payload.get("messages") or []
        rec = {
            "kind": "llm_call_start",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "LLM_CALL"),
            "timestamp": ts,
            **({"messages": messages} if messages else {}),
        }
        out.append(_with_parent(rec, record, ctx))
    if op == "TOOL_CALL" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        tool_rec: dict = {
            "kind": "tool_call_start",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "TOOL_CALL"),
            "timestamp": ts,
            "tool_name": resolve_tool_name(payload),
        }
        args = payload.get("tool_arguments")
        if isinstance(args, dict) and args:
            tool_rec["arguments"] = args
        out.append(_with_parent(tool_rec, record, ctx))
    if op == "SPAWN" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        spawn_rec: dict = {
            "kind": "spawn_start",
            "category": payload.get("category") or "spawn.subagent",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "SPAWN"),
            "timestamp": ts,
            "tool_name": resolve_tool_name(payload) or payload.get("tool_name") or "create_subagent",
        }
        for key_name in ("template", "task", "child_id", "parent_call_id"):
            if payload.get(key_name):
                spawn_rec[key_name] = payload[key_name]
        args = payload.get("tool_arguments")
        if isinstance(args, dict) and args:
            spawn_rec["arguments"] = args
        out.append(_with_parent(spawn_rec, record, ctx))
    return out


def _boundary_engine_io_return(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    out: list[dict] = []
    op = payload.get("op", "LLM_CALL")
    key = (cid, op)
    if op == "LLM_CALL" and key not in ctx._seen_engine_returns:
        ctx._seen_engine_returns.add(key)
        rec = {
            "kind": "llm_call_end",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "LLM_CALL"),
            "timestamp": ts,
            "output": payload.get("text", ""),
            "next_step": payload.get("next_step", "STOP"),
        }
        usage = payload.get("usage")
        if usage:
            rec["tokens_used"] = usage
        finish_reason = payload.get("finish_reason")
        if finish_reason:
            rec["finish_reason"] = finish_reason
        model = payload.get("model")
        if model:
            rec["model"] = model
        if payload.get("max_tokens") is not None:
            rec["max_tokens"] = payload["max_tokens"]
        if payload.get("truncation_retries"):
            rec["truncation_retries"] = payload["truncation_retries"]
        if isinstance(payload.get("tools"), list):
            rec["tools"] = list(payload["tools"])
        for key in ("cache_status", "cache_layer", "cache_events"):
            if payload.get(key) is not None:
                rec[key] = payload[key]
        out.append(_with_parent(rec, record, ctx))
    if op == "TOOL_CALL" and key not in ctx._seen_engine_returns:
        ctx._seen_engine_returns.add(key)
        rec = {
            "kind": "tool_call_end",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "TOOL_CALL"),
            "timestamp": ts,
            "output": payload.get("text", ""),
            "tool_name": resolve_tool_name(payload),
        }
        out.append(_with_parent(rec, record, ctx))
    if op == "MEMORY_OP" and key not in ctx._seen_engine_returns:
        ctx._seen_engine_returns.add(key)
        rec = {
            "kind": "memory_call_end",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "MEMORY_OP"),
            "timestamp": ts,
            "output": payload.get("text", ""),
        }
        out.append(_with_parent(rec, record, ctx))
    if op == "SPAWN" and key not in ctx._seen_engine_returns:
        ctx._seen_engine_returns.add(key)
        rec = {
            "kind": "spawn_end",
            "category": payload.get("category") or "spawn.subagent",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "SPAWN"),
            "timestamp": ts,
            "output": payload.get("text", ""),
            "tool_name": resolve_tool_name(payload) or payload.get("tool_name") or "create_subagent",
        }
        for key_name in ("template", "child_id", "parent_call_id"):
            if payload.get(key_name):
                rec[key_name] = payload[key_name]
        out.append(_with_parent(rec, record, ctx))
    return out


def _boundary_envelope_activity(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    activity = payload.get("activity", "")
    boundary = payload.get("boundary", "")
    op_name = payload.get("op", "")
    if boundary not in ("start", "end"):
        return []
    native_kind = f"{activity}_{boundary}"
    # "contract_call" (CONTRACT_START/CONTRACT_END) deliberately does NOT
    # translate to llm_call_*/tool_call_*/memory_call_* here: those native
    # kinds are already produced, once each, by _boundary_engine_io /
    # _boundary_engine_io_return from the ENGINE_IO / ENGINE_IO_RETURN boundary
    # events (which the operator emits for the same call and which already
    # dedup driver.py's kernel-level record_egress against the envelope's own
    # record_engine_io via ctx._seen_engine_ops/_seen_engine_returns). Mapping
    # "contract_call" to the same kind here duplicated every call in the native
    # trace under a second, unrelated correlation/call-id pairing — the
    # duplicate had no dedup key shared with the ENGINE_IO path, so it always
    # survived, corrupting boundary alignment (a stray zero-content "call" that
    # inherits its neighbour's timestamps). Left as the default
    # "contract_call_start"/"contract_call_end" kind, which _KIND_BASE_TO_TYPE
    # does not map, so records.py silently ignores it.
    if activity == "parallel_group":
        native_kind = f"parallel_group_{boundary}"
    elif activity == "wait_state":
        native_kind = f"wait_state_{boundary}"
    elif activity in ("gov_authorize", "gov_validate"):
        native_kind = f"governance_{activity.replace('gov_', '')}_{boundary}"
    elif activity.startswith("obs_wrap"):
        native_kind = f"{activity}_{boundary}"
    call_id = _resolve_call_id(record, ctx, cid, op_name or activity)
    rec = {
        "kind": native_kind,
        **base,
        "timestamp": ts,
        "call_id": call_id,
        "activity": activity,
        "op": op_name,
        **(
            {"group_id": payload.get("group_id", ""), "tools": payload.get("tools", [])}
            if activity == "parallel_group"
            else {}
        ),
    }
    if activity == "wait_state":
        wait_link_id = str(payload.get("wait_link_id") or f"wait-{cid}")
        wait_role = str(payload.get("wait_role") or ("WAIT" if boundary == "start" else "RESUME")).upper()
        wait_note = str(payload.get("wait_note") or "")
        wait_scope = str(payload.get("wait_scope") or "")
        wait_call_id = _interval_call_id(
            ctx,
            cid,
            f"PROCESSING_CALL:wait_state:{wait_role}:{wait_link_id}",
        ) if cid else ""
        p_start = {
            "kind": "processing_call_start",
            **base,
            "timestamp": ts,
            "call_id": wait_call_id,
            "processing_name": "wait" if wait_role == "WAIT" else "resume",
            "processing_type": "wait_state",
            "wait_link_id": wait_link_id,
            "wait_role": wait_role,
            "wait_scope": wait_scope,
            "wait_note": wait_note,
            **({"input": wait_note} if wait_note else {}),
        }
        p_end = {
            "kind": "processing_call_end",
            **base,
            "timestamp": ts + _SYNTHETIC_SPAN_DURATION_S,
            "call_id": wait_call_id,
            "processing_name": "wait" if wait_role == "WAIT" else "resume",
            "processing_type": "wait_state",
            "wait_link_id": wait_link_id,
            "wait_role": wait_role,
            "wait_scope": wait_scope,
            "wait_note": wait_note,
            **({"output": wait_note} if wait_note else {}),
            "synthetic": True,
        }
        return [
            _with_parent(p_start, record, ctx),
            _with_parent(rec, record, ctx),
            _with_parent(p_end, record, ctx),
        ]
    if activity != "parallel_group":
        return [_with_parent(rec, record, ctx)]

    group_id = str(payload.get("group_id") or "")
    tools = list(payload.get("tools") or [])
    tool_count = len(tools)
    proc_call_id = _interval_call_id(
        ctx,
        cid,
        f"PROCESSING_CALL:parallel_group:{group_id}",
    ) if cid else ""

    if boundary == "start":
        proc_start = {
            "kind": "processing_call_start",
            **base,
            "timestamp": ts,
            "call_id": proc_call_id,
            "processing_name": "parallel fork",
            "processing_type": "parallel_group",
            "group_id": group_id,
            "tool_count": tool_count,
            "tools": tools,
            "input": f"fork {tool_count} parallel tool call{'s' if tool_count != 1 else ''}",
        }
        return [
            _with_parent(proc_start, record, ctx),
            _with_parent(rec, record, ctx),
        ]

    proc_end = {
        "kind": "processing_call_end",
        **base,
        "timestamp": ts,
        "call_id": proc_call_id,
        "processing_name": "parallel aggregation",
        "processing_type": "parallel_group",
        "group_id": group_id,
        "tool_count": tool_count,
        "tools": tools,
        "output": f"aggregate {tool_count} parallel tool result{'s' if tool_count != 1 else ''}",
    }
    return [
        _with_parent(rec, record, ctx),
        _with_parent(proc_end, record, ctx),
    ]


def _context_assembly_operation(messages: list[dict]) -> str:
    """Determine if context operation is PREPEND (has system msg) or APPEND."""
    return "PREPEND" if any(
        str(m.get("role") or "").strip().lower() == "system" for m in messages
    ) else "APPEND"


def _add_provenance_fields(event: dict, seg: dict, is_rag: bool) -> None:
    """Add optional provenance fields from segment to context_part_contributed event."""
    # Plugin/Origin identification
    origin_id = seg.get("origin_id") or seg.get("plugin_id") or seg.get("skill_name")
    if origin_id:
        event["origin_id"] = origin_id
    
    # Retrieval metadata
    if is_rag or seg.get("mechanism") == "retrieval":
        for key in ("retrieval_query", "retrieval_source", "retrieval_rank"):
            if seg.get(key):
                event[key] = seg[key]
        if seg.get("retrieval_confidence") is not None:
            event["retrieval_confidence"] = seg["retrieval_confidence"]
    
    # Decision metadata
    if seg.get("decision_rank"):
        event["decision_rank"] = seg["decision_rank"]
    if seg.get("decision_score") is not None:
        event["decision_score"] = seg["decision_score"]
    
    # Synthesis metadata
    if seg.get("synthesis_rule_id"):
        event["synthesis_rule_id"] = seg["synthesis_rule_id"]


def _boundary_context_assembled(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    llm_call_id = _resolve_call_id(record, ctx, cid, "LLM_CALL") if cid else ""
    segments = payload.get("segments") or []
    # Prompt text lives here; InvokeEngineIo does not include messages.
    _asm_msgs = payload.get("messages") or []
    _operation = _context_assembly_operation(_asm_msgs)
    _proc_call_id = (
        ctx.next_interval_call_id(cid, "PROCESSING_CALL:context_assembly") if cid else ""
    )
    _proc_common = {
        **base,
        "call_id": _proc_call_id,
        "processing_name": "context assembly",
        "processing_type": "context_assembly",
        "context_operation": _operation,
        "messages": _asm_msgs,
    }
    _proc_start = {"kind": "processing_call_start", "timestamp": ts, **_proc_common}
    _proc_end = {
        "kind": "processing_call_end",
        "timestamp": ts + _SYNTHETIC_SPAN_DURATION_S,
        "synthetic": True,
        **_proc_common,
    }
    
    _ca: dict = {
        "kind": "context_assembled",
        **base,
        "timestamp": ts,
        "call_id": llm_call_id,
        "llm_call_id": llm_call_id,
        "correlation_id": cid,
        "segments": len(segments),
        "total_tokens": payload.get("total_tokens", 0),
        "message_count": payload.get("message_count", 0),
    }
    if _asm_msgs:
        _ca["messages"] = _asm_msgs
    if isinstance(payload.get("tools"), list):
        _ca["tools"] = list(payload["tools"])
    if isinstance(payload.get("context_usage"), dict):
        _ca["context_usage"] = dict(payload["context_usage"])
    out: list[dict] = [
        _with_parent(_proc_start, record, ctx),
        _ca,
        _with_parent(_proc_end, record, ctx),
    ]
    agent_id = payload.get("agent_id") or ctx.agent_id
    for seg in segments:
        mechanism = str(seg.get("mechanism") or "")
        source = str(seg.get("source") or "")
        is_rag = mechanism == "rag" or "rag" in source.lower()
        
        # Build context_part_contributed event with full provenance
        cpc_event: dict = {
            "kind": "context_part_contributed",
            "agent_id": agent_id,
            "timestamp": ts,
            "llm_call_id": llm_call_id,
            "part_id": seg.get("part_id", ""),
            "source": seg.get("source", ""),
            "section_id": seg.get("section_id", ""),
            "mechanism": seg.get("mechanism") or ("rag" if is_rag else "inject"),
            "token_estimate": seg.get("tokens", 0),
            "retained": seg.get("retained", True),
            "content_preview": seg.get("content_preview")
            or (seg.get("content", "")[:120] if seg.get("content") else ""),
            "role": seg.get("role", ""),
        }
        _add_provenance_fields(cpc_event, seg, is_rag)
        out.append(cpc_event)
        
        # Emit RAG query events if mechanism is RAG
        if is_rag:
            rag_call_id = f"rag-{cid}-{seg.get('part_id', '')[:8]}"
            out.extend(
                [
                    _with_parent(
                        {
                            "kind": "rag_query_start",
                            **base,
                            "timestamp": ts,
                            "call_id": rag_call_id,
                            "query": seg.get("content_preview", ""),
                        },
                        record,
                        ctx,
                    ),
                    _with_parent(
                        {
                            "kind": "rag_query_end",
                            **base,
                            "timestamp": ts + _SYNTHETIC_SPAN_DURATION_S,
                            "call_id": rag_call_id,
                            "results_count": 1,
                            "synthetic": True,
                        },
                        record,
                        ctx,
                    ),
                ]
            )
    return out


def _boundary_cache_lookup(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    event = {
        "kind": "cache_lookup",
        **base,
        "call_id": _resolve_call_id(record, ctx, cid, "LLM_CALL"),
        "timestamp": ts,
        "cache_layer": str(payload.get("cache_layer") or "unknown"),
        "cache_status": str(payload.get("cache_status") or "unknown"),
    }
    return [_with_parent(event, record, ctx)]


def _boundary_context_mutation(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    action = payload.get("action", "mutation")
    state_call_id = f"state-{cid}-{action}-{payload.get('turn_index', 0)}"
    return [
        _with_parent(
            {
                "kind": "state_update_start",
                **base,
                "timestamp": ts,
                "call_id": state_call_id,
                "update_type": action,
                "role": payload.get("role", ""),
                "turn_index": payload.get("turn_index", 0),
                "committed_count": payload.get("committed_count", 0),
                "wm_count": payload.get("wm_count", 0),
            },
            record,
            ctx,
        ),
        _with_parent(
            {
                "kind": "state_update_end",
                **base,
                "timestamp": ts + _SYNTHETIC_SPAN_DURATION_S,
                "call_id": state_call_id,
                "update_type": action,
                "content_preview": payload.get("content_preview", ""),
                "synthetic": True,
            },
            record,
            ctx,
        ),
    ]


def _boundary_client_response(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    return [
        {
            "kind": "client_response",
            **base,
            "timestamp": ts,
            "finish_reason": payload.get("finish_reason", "stop"),
        }
    ]


def _boundary_hitl_request(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    return [
        {
            "kind": "hitl_gate",
            **base,
            "timestamp": ts,
            "question": payload.get("question", ""),
            "policy_name": payload.get("policy_name", ""),
        }
    ]


def _boundary_hitl_resolve(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """The human's actual answer to a HITL gate — without this, only the
    question (hitl_gate) reaches the trace; what was decided is lost."""
    return [
        {
            "kind": "hitl_resolve",
            **base,
            "timestamp": ts,
            "resolution": payload.get("resolution", ""),
            "answer": payload.get("answer"),
        }
    ]


def _boundary_governance_decision(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """The semantic governance outcome (decision + reason + which policy), as
    opposed to _boundary_envelope_activity's governance_authorize/validate
    kinds, which only bracket the *timing* of the check. Non-empty decision/
    reason arrive only on the "after" checkpoint (see GovEnvelopeMachine)."""
    if not payload.get("decision"):
        return []
    return [
        {
            "kind": "governance_decision",
            **base,
            "timestamp": ts,
            "hook": payload.get("hook", ""),
            "checkpoint": payload.get("checkpoint", ""),
            "decision": payload.get("decision", ""),
            "reason": payload.get("reason", ""),
            "policy_name": payload.get("policy_name", ""),
        }
    ]


def _boundary_error(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """A boundary error the kernel raised (e.g. retry budget exhausted — the
    circuit-breaker signal). Previously dropped entirely from the native trace."""
    return [
        {
            "kind": "boundary_error",
            **base,
            "timestamp": ts,
            "code": payload.get("code", ""),
            "recoverable": payload.get("recoverable", True),
            "message": payload.get("message", ""),
            "parent_call_id": payload.get("parent_call_id"),
        }
    ]


def _boundary_context_steer(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """Operator mid-run steering request (interactive sessions only)."""
    return [
        {
            "kind": "context_steer",
            **base,
            "timestamp": ts,
            "collect_id": payload.get("collect_id", ""),
        }
    ]


def _boundary_egress_misc(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """Catch-all for egress kernel symbols with no dedicated native kind
    (e.g. NoOp). Kept for completeness — no _KIND_BASE_TO_TYPE mapping, so it
    never appears as a call-lane bar."""
    return [
        {
            "kind": "boundary_egress",
            **base,
            "timestamp": ts,
            "egress_kind": payload.get("egress_kind", ""),
        }
    ]


def _boundary_ingress_misc(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """Catch-all for ingress kernel symbols with no dedicated native kind.
    Kept for completeness — no _KIND_BASE_TO_TYPE mapping, so it never appears
    as a call-lane bar."""
    return [
        {
            "kind": "boundary_ingress",
            **base,
            "timestamp": ts,
            "ingress_kind": payload.get("ingress_kind", ""),
        }
    ]


def _boundary_control(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    method = str(payload.get("method") or "")
    category = str(payload.get("category") or (f"control.{method}" if method else "control"))
    rec = {
        "kind": "control",
        "category": category,
        "method": method,
        "control_kind": payload.get("control_kind") or payload.get("kind") or method,
        "actor": payload.get("actor") or "",
        "surface": payload.get("surface") or "",
        "denied": bool(payload.get("denied")),
        "reason": payload.get("reason") or "",
        "session_id": payload.get("session_id") or "",
        "timestamp": ts,
        **base,
    }
    extra = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "method",
            "category",
            "control_kind",
            "actor",
            "surface",
            "denied",
            "reason",
            "session_id",
        }
    }
    if extra:
        rec["payload"] = extra
    return [rec]


_BoundaryHandler = Callable[..., list[dict]]

_BOUNDARY_KIND_HANDLERS: dict[str, _BoundaryHandler] = {
    ObsEventKind.ENGINE_IO.value: _boundary_engine_io,
    ObsEventKind.ENGINE_IO_RETURN.value: _boundary_engine_io_return,
    ObsEventKind.ENVELOPE_ACTIVITY.value: _boundary_envelope_activity,
    ObsEventKind.CONTEXT_ASSEMBLED.value: _boundary_context_assembled,
    ObsEventKind.CACHE_LOOKUP.value: _boundary_cache_lookup,
    ObsEventKind.CONTEXT_MUTATION.value: _boundary_context_mutation,
    ObsEventKind.CLIENT_RESPONSE.value: _boundary_client_response,
    ObsEventKind.HITL_REQUEST.value: _boundary_hitl_request,
    ObsEventKind.HITL_RESOLVE.value: _boundary_hitl_resolve,
    ObsEventKind.GOVERNANCE_DECISION.value: _boundary_governance_decision,
    ObsEventKind.BOUNDARY_ERROR.value: _boundary_error,
    ObsEventKind.CONTEXT_STEER.value: _boundary_context_steer,
    ObsEventKind.CONTROL.value: _boundary_control,
    ObsEventKind.BOUNDARY_EGRESS.value: _boundary_egress_misc,
    ObsEventKind.BOUNDARY_INGRESS.value: _boundary_ingress_misc,
}

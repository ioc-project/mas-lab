#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Boundary event kind dispatch — one handler per ``ObsEventKind``."""

from __future__ import annotations

import time
from collections.abc import Callable

from mas.library.standard.lib.observability.native.tool_name import resolve_tool_name
from mas.library.standard.lib.observability.native.transform import (
    TransformContext,
    _canonical_json,
    _event_kind,
    _interval_call_id,
    _resolve_call_id,
    _state_close,
    _state_fields,
    _state_new_id,
    _state_open,
    _state_set_content,
    _with_parent,
    register_tool_call,
)
from mas.runtime.schema.observability import ObsEventKind

# A thinking interval's call id is its LLM call's id plus this suffix.
THINKING_CALL_SUFFIX = "#think"


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


def _llm_request_fields(payload: dict) -> dict:
    """The request an ENGINE_IO payload carries, as ``llm_call_start`` fields.

    ``llm_call_start`` is the request: ``model``, ``messages``, offered
    ``tools`` (names), ``max_tokens``, ``temperature``, ``reasoning_effort``. Unset values are left
    out. ``llm_call_end`` never repeats them.
    """
    fields: dict = {}
    if payload.get("messages"):
        fields["messages"] = payload["messages"]
    if payload.get("model"):
        fields["model"] = payload["model"]
    for name in ("max_tokens", "temperature", "reasoning_effort"):
        if payload.get(name) is not None:
            fields[name] = payload[name]
    if isinstance(payload.get("tools"), list):
        fields["tools"] = list(payload["tools"])
    return fields


def _memory_store_fields(payload: dict, ctx: TransformContext) -> dict:
    """The memory store a MEMORY_OP uses: ``memory_name`` / ``memory_type``.

    A MEMORY_OP that names its store keeps that name. Otherwise the op is the
    kernel memory contract (``ContractKind.MEMORY``) of the agent that issued
    it, i.e. that agent's working memory, named ``<agent_id>.working_memory``.
    """
    name = str(payload.get("memory_name") or payload.get("collection") or "")
    if name:
        return {"memory_name": name, "memory_type": str(payload.get("memory_type") or "")}
    return {"memory_name": f"{ctx.agent_id}.working_memory", "memory_type": "working"}


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
        call_id = _resolve_call_id(record, ctx, cid, "MEMORY_OP")
        rec = {
            "kind": "memory_call_start",
            **base,
            "call_id": call_id,
            "timestamp": ts,
            **_memory_store_fields(payload, ctx),
            **_state_fields(_state_open(ctx, call_id)),
        }
        out.append(_with_parent(rec, record, ctx))
    if op == "LLM_CALL" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        call_id = _resolve_call_id(record, ctx, cid, "LLM_CALL")
        rec = {
            "kind": "llm_call_start",
            **base,
            "call_id": call_id,
            "timestamp": ts,
            **_state_fields(_state_open(ctx, call_id)),
        }
        rec.update(_llm_request_fields(payload))
        out.append(_with_parent(rec, record, ctx))
    if op == "TOOL_CALL" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        call_id = _resolve_call_id(record, ctx, cid, "TOOL_CALL")
        pair = _state_open(ctx, call_id)
        # Every tool call is registered so a delegate's user_input can find its caller.
        register_tool_call(
            ctx.shared,
            call_id,
            {
                "agent_id": ctx.agent_id,
                "exec_call_id": ctx.exec_call_id,
                "state_in": pair["state_in"],
                "state_in_content": pair["state_in_content"],
                "state_out": pair["state_out"],
            },
        )
        tool_rec: dict = {
            "kind": "tool_call_start",
            **base,
            "call_id": call_id,
            "timestamp": ts,
            "tool_name": resolve_tool_name(payload),
            **_state_fields(pair),
        }
        args = payload.get("tool_arguments")
        if isinstance(args, dict) and args:
            tool_rec["arguments"] = args
        out.append(_with_parent(tool_rec, record, ctx))
    if op == "SPAWN" and key not in ctx._seen_engine_ops:
        ctx._seen_engine_ops.add(key)
        # A spawn moves no State: a child visit under it must not wait for a tool registration.
        register_tool_call(ctx.shared, _resolve_call_id(record, ctx, cid, "SPAWN"), None)
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
        if payload.get("response_kind") == "ERROR":
            rec["status"] = "error"
        usage = payload.get("usage")
        if usage:
            rec["tokens_used"] = usage
        finish_reason = payload.get("finish_reason")
        if finish_reason:
            rec["finish_reason"] = finish_reason
        if payload.get("max_tokens") is not None:
            rec["max_tokens"] = payload["max_tokens"]
        if payload.get("truncation_retries"):
            rec["truncation_retries"] = payload["truncation_retries"]
        tool_calls = payload.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            rec["tool_calls"] = list(tool_calls)
        for field in ("cache_status", "cache_layer", "cache_events"):
            if payload.get(field) is not None:
                rec[field] = payload[field]
        # Model text if any, else the structured tool calls of a tool-use turn.
        llm_content = str(rec.get("output") or "")
        if not llm_content and rec.get("tool_calls"):
            llm_content = _canonical_json(rec["tool_calls"])
        rec.update(_state_close(ctx, rec["call_id"], llm_content))
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
        if payload.get("response_kind") == "ERROR":
            rec["status"] = "error"
        rec.update(_state_close(ctx, rec["call_id"], str(rec["output"] or "")))
        out.append(_with_parent(rec, record, ctx))
    if op == "MEMORY_OP" and key not in ctx._seen_engine_returns:
        ctx._seen_engine_returns.add(key)
        rec = {
            "kind": "memory_call_end",
            **base,
            "call_id": _resolve_call_id(record, ctx, cid, "MEMORY_OP"),
            "timestamp": ts,
            "output": payload.get("text", ""),
            **_memory_store_fields(payload, ctx),
        }
        rec.update(_state_close(ctx, rec["call_id"], str(rec["output"] or "")))
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
    out.extend(_flush_closed_groups(ctx, ts, record))
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
    op_key = f"CONTRACT:{op_name}" if activity == "contract_call" and op_name else (op_name or activity)
    call_id = _resolve_call_id(record, ctx, cid, op_key)
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
    if native_kind.removesuffix(f"_{boundary}") in _CHECK_ACTIVITIES:
        return [_check_record(rec, record, ctx, activity)]
    # wait_state and parallel_group are control markers, not processing: only
    # their plain start/end records are emitted (no ProcessingCall companions).
    if activity != "parallel_group":
        return [_with_parent(rec, record, ctx)]

    group_id = str(payload.get("group_id") or "")
    tools = list(payload.get("tools") or [])
    tool_count = len(tools)
    proc_call_id = (
        _interval_call_id(
            ctx,
            cid,
            f"PROCESSING_CALL:parallel_group:{group_id}",
        )
        if cid
        else ""
    )

    if boundary == "start":
        # Members started before the group ends read this State as their input.
        # No ProcessingCall here: the fork moves no data.
        ctx._state_group = {"group_in": ctx._state_current, "members": []}
        return [_with_parent(rec, record, ctx)]

    # The group end marker comes before its members' returns. The join is real processing
    # of the members' outputs, so it is emitted when the last member has returned.
    group = ctx._state_group or {"members": []}
    ctx._state_group = None
    group.update(
        base=base,
        proc_call_id=proc_call_id,
        group_id=group_id,
        tools=tools,
        tool_count=tool_count,
    )
    out = [_with_parent(rec, record, ctx)]
    if all(m.get("closed") for m in group["members"]):
        out.extend(_aggregate_records(ctx, group, ts, record))
    else:
        ctx._state_pending_groups.append(group)
    return out


# Checks that bracket a call (authorize before it, validate after it): ProcessingCalls.
_CHECK_ACTIVITIES = frozenset(
    {
        "governance_authorize",
        "governance_validate",
        "obs_wrap_gov_authorize",
        "obs_wrap_gov_validate",
    }
)


def _check_record(rec: dict, record: dict, ctx: TransformContext, activity: str) -> dict:
    """A check bracket: its real parent, the call it checks, and the State it reads.

    A check runs in the agent visit, before (authorize) or after (validate) the call it
    checks, not inside that call: its parent is the visit, and the checked call is
    ``checked_call_id``. It reads one State and lets it through unchanged: authorize the
    State the checked call consumes (the visit's current State, or its parallel group's
    input), validate the checked call's output State. ``state_in`` and ``state_out`` are
    that State.
    """
    checked = str(record.get("parent_call_id") or "")
    parent = ctx.exec_call_id or ctx.mas_call_id
    if parent:
        rec["parent_call_id"] = parent
    if checked:
        rec["checked_call_id"] = checked
    pair = ctx._state_by_call.get(checked) if checked else None
    if activity.endswith("validate") and pair is not None:
        state = pair["state_out"]
    elif ctx._state_group is not None:
        state = ctx._state_group["group_in"]
    else:
        state = ctx._state_current
    if state:
        content = ctx._state_contents.get(state, "")
        rec.update(state_in=state, state_in_content=content, state_out=state, state_out_content=content)
    return rec


def _aggregate_records(ctx: TransformContext, group: dict, ts: float, record: dict) -> list[dict]:
    """The `aggregate` ProcessingCall of a parallel group: N member outputs become one State."""
    members = group["members"]
    join_state = _state_new_id(ctx)
    ctx._state_current = join_state
    member_contents = _canonical_json([m["state_out_content"] for m in members])
    ctx._state_contents[join_state] = member_contents
    proc_fields = {
        "call_id": group["proc_call_id"],
        "processing_name": "aggregate",
        "processing_type": "parallel_group",
        "group_id": group["group_id"],
        "tool_count": group["tool_count"],
        "tools": group["tools"],
        "input": member_contents,
        "output": member_contents,
        "state_in": [m["state_out"] for m in members],
        "state_in_content": member_contents,
        "state_out": join_state,
        "state_out_content": member_contents,
    }
    proc_start = {"kind": "processing_call_start", **group["base"], "timestamp": ts, **proc_fields}
    proc_end = {"kind": "processing_call_end", **group["base"], "timestamp": ts, **proc_fields}
    return [_with_parent(proc_start, record, ctx), _with_parent(proc_end, record, ctx)]


def _flush_closed_groups(ctx: TransformContext, ts: float, record: dict) -> list[dict]:
    """Emit the aggregate of every ended parallel group whose members have all returned."""
    out: list[dict] = []
    for group in list(ctx._state_pending_groups):
        if all(m.get("closed") for m in group["members"]):
            ctx._state_pending_groups.remove(group)
            out.extend(_aggregate_records(ctx, group, ts, record))
    return out


def _context_assembly_operation(messages: list[dict]) -> str:
    """Determine if context operation is PREPEND (has system msg) or APPEND."""
    return "PREPEND" if any(str(m.get("role") or "").strip().lower() == "system" for m in messages) else "APPEND"


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


def _boundary_llm_thinking(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    """``thinking_start`` / ``thinking_end``: the reasoning interval of one LLM call.

    Emitted as the runtime reports each boundary: the start at the first
    reasoning delta, the end at the first answer delta (or the end of the
    stream). The timestamp is the one the runtime took when it happened
    (``at``). The interval reads the LLM call's prompt State (``state_in`` of
    that call) and writes its own State ``<llm_call_id>#think#0``, whose
    content is the reasoning text (on the end record).
    """
    boundary = str(payload.get("boundary") or "")
    if boundary not in ("start", "end"):
        return []
    llm_call_id = _resolve_call_id(record, ctx, cid, "LLM_CALL")
    at = payload.get("at")
    rec: dict = {
        "kind": f"thinking_{boundary}",
        **base,
        "timestamp": float(at) if isinstance(at, (int, float)) else ts,
        "call_id": f"{llm_call_id}{THINKING_CALL_SUFFIX}",
        "parent_call_id": llm_call_id,
    }
    pair = ctx._state_by_call.get(llm_call_id)
    if pair is not None:
        rec["state_in"] = pair["state_in"]
        rec["state_in_content"] = pair["state_in_content"]
    rec["state_out"] = f"{llm_call_id}{THINKING_CALL_SUFFIX}#0"
    if boundary == "end":
        rec["state_out_content"] = str(payload.get("text") or "")
    return [rec]


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
    _proc_call_id = ctx.next_interval_call_id(cid, "PROCESSING_CALL:context_assembly") if cid else ""
    _usage = payload.get("context_usage") if isinstance(payload.get("context_usage"), dict) else {}
    _archive_view_id = str(_usage.get("archive_view_id") or "")
    _proc_common = {
        **base,
        "call_id": _proc_call_id,
        "processing_name": "context assembly",
        "processing_type": "context_assembly",
        "context_operation": _operation,
        "messages": _asm_msgs,
    }
    if _archive_view_id:
        _proc_common["archive_view_id"] = _archive_view_id
    _asm_content = _canonical_json(_asm_msgs)
    _proc_pair = _state_open(ctx, _proc_call_id)
    _state_set_content(ctx, _proc_pair, _asm_content)
    _proc_start = {
        "kind": "processing_call_start",
        "timestamp": ts,
        **_proc_common,
        **_state_fields(_proc_pair, _asm_content if _asm_msgs else None),
    }
    _proc_end = {
        "kind": "processing_call_end",
        # The runtime reports context assembly at one instant: zero duration.
        "timestamp": ts,
        **_proc_common,
        **_state_fields(_proc_pair, _asm_content),
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
    if _usage:
        _ca["context_usage"] = dict(_usage)
    if _archive_view_id:
        _ca["archive_view_id"] = _archive_view_id
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
            # The part is contributed to the prompt of this LLM call: its owner.
            **({"parent_call_id": llm_call_id} if llm_call_id else {}),
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
        out.append(cpc_event if llm_call_id else _with_parent(cpc_event, record, ctx))

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
                # A working-memory mutation is reported at one instant: zero duration.
                "timestamp": ts,
                "call_id": state_call_id,
                "update_type": action,
                "content_preview": payload.get("content_preview", ""),
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
    rec = {
        "kind": "client_response",
        **base,
        "timestamp": ts,
        "finish_reason": payload.get("finish_reason", "stop"),
    }
    return [_with_parent(rec, record, ctx)]


def _boundary_hitl_request(
    record: dict,
    *,
    ctx: TransformContext,
    cid: int,
    base: dict,
    payload: dict,
    ts: float,
) -> list[dict]:
    rec = {
        "kind": "hitl_gate",
        **base,
        "timestamp": ts,
        "question": payload.get("question", ""),
        "policy_name": payload.get("policy_name", ""),
    }
    return [_with_parent(rec, record, ctx)]


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
    rec = {
        "kind": "hitl_resolve",
        **base,
        "timestamp": ts,
        "resolution": payload.get("resolution", ""),
        "answer": payload.get("answer"),
        "operator_id": payload.get("operator_id") or record.get("actor_id") or "",
        "steering": payload.get("steering") or "",
    }
    return [_with_parent(rec, record, ctx)]


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
    rec = {
        "kind": "governance_decision",
        **base,
        "timestamp": ts,
        "hook": payload.get("hook", ""),
        "checkpoint": payload.get("checkpoint", ""),
        "decision": payload.get("decision", ""),
        "reason": payload.get("reason", ""),
        "policy_name": payload.get("policy_name", ""),
    }
    return [_with_parent(rec, record, ctx)]


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
        _with_parent(
            {
                "kind": "boundary_error",
                **base,
                "timestamp": ts,
                "code": payload.get("code", ""),
                "recoverable": payload.get("recoverable", True),
                "message": payload.get("message", ""),
                **({"parent_call_id": payload["parent_call_id"]} if payload.get("parent_call_id") else {}),
            },
            record,
            ctx,
        )
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
    rec = {
        "kind": "context_steer",
        **base,
        "timestamp": ts,
        "collect_id": payload.get("collect_id", ""),
    }
    return [_with_parent(rec, record, ctx)]


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
    rec = {
        "kind": "boundary_egress",
        **base,
        "timestamp": ts,
        "egress_kind": payload.get("egress_kind", ""),
    }
    return [_with_parent(rec, record, ctx)]


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
    rec = {
        "kind": "boundary_ingress",
        **base,
        "timestamp": ts,
        "ingress_kind": payload.get("ingress_kind", ""),
    }
    return [_with_parent(rec, record, ctx)]


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
    ObsEventKind.LLM_THINKING.value: _boundary_llm_thinking,
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

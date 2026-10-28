#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Remap MAS-lab ``delegate_to_<agent>`` tool calls onto observe-sdk handoffs.

This is the one intentional exception to 1:1 native-kind → OTel-span
conversion. LangGraph observe-sdk traces a graph re-entry as a new
``*.agent`` plus ``ioa_observe.agent.previous``. MAS-lab encodes the same
handoff as a tool named ``delegate_to_<id>`` inside one long
``execution_start``. When ``rewrite_tool_delegation`` is on:

* the tool span is suppressed
* the caller ``AgentCall`` closes so the specialist is a sibling
* the next caller LLM / user_response opens a resume ``AgentCall``

Caller/target names always come from the event, never a hardcoded agent id.
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion.tool_name import resolve_tool_name
from mas.runtime.boundary.delegation.handoff import handoff_target
from mas.runtime.boundary.delegation.policy import DELEGATE_TOOL_PREFIX


def delegate_target_agent(tool_name: str | None, ev: Dict[str, Any] | None = None) -> str:
    """Return the target agent of a recorded handoff, or ``""``.

    Uses the shared handoff helper: ``semantics.concept == agent_comm``,
    or a legacy ``delegate_to_<id>`` tool name. A user tool named
    ``send``, or a tool that only has a ``target`` argument, is not a
    handoff.
    """
    return handoff_target(tool_name, event=ev)


def rewrite_delegate_tool_start(conv: Any, ev: Dict[str, Any]) -> bool:
    """Skip the tool span and record a handoff. True = caller must not emit."""
    if not getattr(conv, "_rewrite_tool_delegation", False):
        return False
    target = delegate_target_agent(resolve_tool_name(ev), ev)
    if not target:
        return False
    call_id = str(ev.get("call_id") or conv.require_call_id(ev))
    caller = conv.agent_id(ev)
    conv._pending_delegations[target] = {
        "caller": caller,
        "call_id": call_id,
        "parent": str(ev.get("parent_call_id") or ""),
    }
    conv._rewritten_delegate_ids.add(call_id)
    raw = str(ev.get("call_id") or "")
    if raw and raw != call_id:
        conv._rewritten_delegate_ids.add(raw)
    close_caller_visit(conv, caller, ev, awaiting=target)
    return True


def rewrite_delegate_tool_end(conv: Any, ev: Dict[str, Any]) -> bool:
    """No-op close for a rewritten ``delegate_to_*`` tool call."""
    if not getattr(conv, "_rewrite_tool_delegation", False):
        return False
    call_id = str(ev.get("call_id") or "")
    rewritten = getattr(conv, "_rewritten_delegate_ids", set())
    if call_id and call_id in rewritten:
        return True
    if call_id:
        resolved = conv._lookup_span_key(call_id, conv.agent_id(ev))
        if resolved and resolved in rewritten:
            return True
    return bool(delegate_target_agent(resolve_tool_name(ev), ev))


def close_caller_visit(
    conv: Any, agent: str, ev: Dict[str, Any], *, awaiting: str = ""
) -> None:
    """End the caller's current AgentCall so the specialist is a sibling."""
    if getattr(conv, "_converter_profile", "") != "observe_sdk":
        return
    current = conv._open_agent_call_id(agent) or conv._fallback_parent_call_id(agent)
    if not current:
        return
    visit = int(conv._agent_visit_count.get(agent) or 1)
    conv._resume_pending[agent] = {
        "original": current,
        "visit": visit + 1,
        "input": conv.agent_output_for(current)
        or str((conv._span_meta.get(current) or {}).get("llm_messages") or ""),
        "awaiting": awaiting,
    }
    conv._agent_visit_count[agent] = visit + 1
    output = conv.agent_output_for(current) or resolve_tool_name(ev) or "delegate"
    conv.close_span(
        current,
        {"mas.output": str(output)},
        status="success",
        end_ns=conv.ts_ns(ev),
    )


def ensure_resumed_agent_call(conv: Any, ev: Dict[str, Any]) -> str | None:
    """Open the next caller AgentCall after a rewritten delegation."""
    agent = conv.agent_id(ev)
    open_id = conv._open_agent_call_id(agent)
    if open_id:
        return open_id
    pending = conv._resume_pending.pop(agent, None)
    if not pending:
        return None
    original = str(pending.get("original") or "")
    visit = int(pending.get("visit") or 2)
    resume_id = f"{original}-resume-{visit}"
    conv._resume_ids[original] = resume_id
    raw = str(ev.get("parent_call_id") or ev.get("call_id") or "")
    if raw:
        conv._resume_ids[raw] = resume_id
    attrs: Dict[str, Any] = {
        "mas.boundary": "AgentCall",
        "mas.agent.id": agent,
        "mas.input": str(pending.get("input") or ev.get("input") or ""),
    }
    start_ns = conv.ts_ns(ev)
    awaiting = str(pending.get("awaiting") or "")
    specialist_end = conv._agent_end_ns.get(awaiting)
    if specialist_end is not None:
        start_ns = specialist_end + 1
    elif start_ns is not None:
        start_ns = max(0, start_ns - 1_000)
    conv.open_span(
        resume_id,
        "AgentCall",
        attrs,
        None,
        start_ns=start_ns,
        set_call_id=True,
    )
    return resume_id


def resolve_agent_end_call_id(conv: Any, ev: Dict[str, Any]) -> str:
    """Map ``execution_end`` onto the live visit (original or resume)."""
    agent = conv.agent_id(ev)
    opened = conv._open_agent_call_id(agent)
    if opened:
        return opened
    cid = str(ev.get("call_id") or "")
    resolved = conv._lookup_span_key(cid, agent) if cid else None
    for key in (cid, resolved or ""):
        resume = conv._resume_ids.get(key)
        if resume and resume in conv._open_spans:
            return resume
    ensured = ensure_resumed_agent_call(conv, ev)
    if ensured:
        return ensured
    if cid and cid in conv._open_spans:
        return cid
    if resolved and resolved in conv._open_spans:
        meta = conv._span_meta.get(resolved) or {}
        if meta.get("agent") in {agent, None, ""}:
            return resolved
    return cid


__all__ = [
    "DELEGATE_TOOL_PREFIX",
    "close_caller_visit",
    "delegate_target_agent",
    "ensure_resumed_agent_call",
    "resolve_agent_end_call_id",
    "rewrite_delegate_tool_end",
    "rewrite_delegate_tool_start",
]

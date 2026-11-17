#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Execution-block handlers — the work an agent performs.

LLM calls, tool calls, processing calls, skill executions, network calls,
workflow transitions, and agent-to-agent communication.  These are the
interval spans nested under an ``AgentCall``.

Ontology block: ``execution`` (model / tool / orchestrator summands).
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register
from mas.library.telemetry.conversion.tool_calls import tool_calls_text
from mas.library.telemetry.conversion.tool_name import resolve_tool_name


def _number(value: Any, cast: Any) -> Any:
    """``value`` as ``cast`` (int/float), or None when it is unset or not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _reasoning_tokens(usage: Dict[str, Any]) -> Any:
    """Reasoning tokens the provider reported (OpenAI ``completion_tokens_details``), or None."""
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        return _number(details["reasoning_tokens"], int)
    return _number(usage.get("reasoning_tokens"), int)


# ── LLM calls ────────────────────────────────────────────────────────────────


@register("llm_call_start")
def h_llm_call_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    if conv.emit_duplicate_start_annotation(ev, "llm_call_start"):
        return
    call_id = conv.require_call_id(ev)
    attrs: Dict[str, Any] = {
        "mas.boundary": "LLMCall",
        "mas.agent.id": conv.agent_id(ev),
        "mas.llm.model": ev.get("model")
        or ev.get("llm_name")
        or getattr(conv, "llm_model_for", lambda _a: "")(conv.agent_id(ev))
        or "",
        "mas.llm.messages": conv.enc(ev.get("messages")),
    }
    if (temperature := _number(ev.get("temperature"), float)) is not None:
        attrs["mas.llm.temperature"] = temperature
    if (max_tokens := _number(ev.get("max_tokens"), int)) is not None:
        attrs["mas.llm.max_tokens"] = max_tokens
    if ev.get("tools") is not None:
        attrs["mas.llm.tools"] = conv.enc(ev.get("tools"))
    if ev.get("reasoning_effort"):
        attrs["mas.llm.reasoning_effort"] = str(ev["reasoning_effort"])
    attrs.update(conv.state_start_attrs(ev))
    conv.open_span(call_id, "LLMCall", attrs, ev.get("parent_call_id"), start_ns=conv.ts_ns(ev))


@register("llm_call_end")
def h_llm_call_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    tokens = ev.get("tokens_used") or {}
    response = ev.get("response") or {}
    if isinstance(response, dict):
        completion = response.get("content", "")
        usage = response.get("usage") or {}
        thinking = response.get("thinking", "")
        if not tokens and usage:
            tokens = usage
    else:
        completion = str(response) if response else ""
        thinking = ""
    if not completion:
        completion = ev.get("output", "")
    tool_text = tool_calls_text(ev.get("tool_calls"))
    if tool_text:
        completion = f"{completion}\n{tool_text}".strip() if completion else tool_text
    extra: Dict[str, Any] = {
        "mas.llm.finish_reason": ev.get("finish_reason") or "",
        "mas.llm.response": str(completion),
    }
    if thinking:
        extra["mas.llm.thinking"] = str(thinking)
    if isinstance(ev.get("tool_calls"), list) and ev["tool_calls"]:
        extra["mas.llm.tool_calls"] = conv.enc(ev["tool_calls"])
    # A cap on the return is the one actually sent, when it differs from the one asked for on start.
    if (sent_max_tokens := _number(ev.get("max_tokens"), int)) is not None:
        extra["mas.llm.max_tokens"] = sent_max_tokens
    if isinstance(tokens, dict):
        extra["metrics.token.input"] = tokens.get("prompt_tokens")
        extra["metrics.token.output"] = tokens.get("completion_tokens")
        extra["metrics.token.total"] = tokens.get("total_tokens")
        extra["metrics.token.reasoning"] = _reasoning_tokens(tokens)
    elif tokens:
        extra["metrics.token.total"] = tokens
    extra.update(conv.state_end_attrs(ev))
    call_id = ev.get("call_id")
    if conv.is_closed(call_id):
        return
    if call_id and not conv.is_open(call_id):
        import logging

        logging.getLogger(__name__).warning(
            "orphan_call_end_total: llm_call_end without a matching start (call_id=%s)",
            call_id,
        )
        if hasattr(conv, "_orphan_call_end_total"):
            conv._orphan_call_end_total += 1
        return
    conv.close_span(call_id, extra, status=ev.get("status", "success"), end_ns=conv.ts_ns(ev))


# ── Thinking (the reasoning interval of one LLM call) ───────────────────────


@register("thinking_start")
def h_thinking_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """``thinking_start``: the first reasoning delta of the LLM call ``parent_call_id``."""
    llm_call_id = str(ev.get("parent_call_id") or "")
    model = getattr(conv, "span_model", lambda _c: "")(llm_call_id)
    attrs: Dict[str, Any] = {
        "mas.boundary": "ThinkingCall",
        "mas.agent.id": conv.agent_id(ev),
        "mas.thinking.llm_call_id": llm_call_id,
        **({"mas.llm.model": model} if model else {}),
        **conv.state_start_attrs(ev),
    }
    conv.open_span(conv.require_call_id(ev), "ThinkingCall", attrs, llm_call_id or None, start_ns=conv.ts_ns(ev))


@register("thinking_end")
def h_thinking_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """``thinking_end``: the first answer delta (or the end of the stream)."""
    extra: Dict[str, Any] = dict(conv.state_end_attrs(ev))
    if ev.get("state_out_content"):
        extra["mas.thinking.text"] = str(ev["state_out_content"])
    conv.close_span(ev.get("call_id"), extra, status=ev.get("status", "success"), end_ns=conv.ts_ns(ev))


# ── Tool calls ───────────────────────────────────────────────────────────────


@register("tool_call_start")
def h_tool_call_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    if conv.emit_duplicate_start_annotation(ev, "tool_call_start"):
        return
    call_id = conv.require_call_id(ev)
    attrs: Dict[str, Any] = {
        "mas.boundary": "ToolCall",
        "mas.agent.id": conv.agent_id(ev),
        "mas.tool.name": resolve_tool_name(ev),
        "mas.tool.input": conv.enc(ev.get("arguments")),
        "mas.tool.category": ev.get("tool_category", "data"),
    }
    if ev.get("barrier_id"):
        attrs["mas.tool.barrier_id"] = ev["barrier_id"]
    attrs.update(conv.state_start_attrs(ev))
    conv.open_span(call_id, "ToolCall", attrs, ev.get("parent_call_id"), start_ns=conv.ts_ns(ev))


@register("tool_call_end")
def h_tool_call_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    result = ev.get("result")
    if result in (None, ""):
        result = ev.get("output", "")
    output = result if isinstance(result, str) else conv.enc(result)
    conv.close_span(
        ev.get("call_id"),
        {
            "mas.tool.output": output,
            **conv.state_end_attrs(ev),
        },
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


# ── Processing calls ─────────────────────────────────────────────────────────


@register("processing_call_start")
def h_processing_call_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.span_key(ev)
    conv.open_span(
        call_id,
        "ProcessingCall",
        {
            "mas.boundary": "ProcessingCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.processing.name": ev.get("processing_name", ""),
            "mas.processing.type": ev.get("processing_type", ""),
            "mas.processing.segments": int(ev.get("segments") or 0),
            "mas.processing.tokens": int(ev.get("tokens") or 0),
            **_processing_content(conv, ev, "input"),
            **conv.state_start_attrs(ev),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("processing_call_end")
def h_processing_call_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        conv.span_key(ev),
        {**_processing_content(conv, ev, "output"), **conv.state_end_attrs(ev)},
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


def _processing_content(conv: SpanEmitter, ev: Dict[str, Any], side: str) -> Dict[str, Any]:
    """What a processing step took in (``input``) or produced (``output``), when it says.

    ``input`` is the step's own input; ``output`` is the prompt it assembled
    (``messages``) or its plain ``output``.
    """
    if side == "input":
        value = ev.get("input") or ev.get("payload")
        return {"mas.processing.input": value if isinstance(value, str) else conv.enc(value)} if value else {}
    if ev.get("messages"):
        return {"mas.processing.messages": conv.enc(ev["messages"])}
    output = ev.get("output")
    return {"mas.processing.output": output if isinstance(output, str) else conv.enc(output)} if output else {}


# ── Skill executions ─────────────────────────────────────────────────────────


@register("skill_execution_start")
def h_skill_execution_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "SkillExecution",
        {
            "mas.boundary": "SkillExecution",
            "mas.agent.id": conv.agent_id(ev),
            "mas.skill.name": ev.get("skill_name") or "",
            "mas.skill.input": conv.enc(ev.get("input")),
            **conv.state_start_attrs(ev),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("skill_execution_end")
def h_skill_execution_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    output = ev.get("output", "")
    val = output if isinstance(output, str) else conv.enc(output)
    conv.close_span(
        ev.get("call_id"),
        {"mas.skill.output": val, **conv.state_end_attrs(ev)},
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


# ── Network calls ────────────────────────────────────────────────────────────


@register("network_call_start")
def h_network_call_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "NetworkCall",
        {
            "mas.boundary": "NetworkCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.network.url": ev.get("url", ""),
            "mas.network.method": ev.get("method", ""),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("network_call_end")
def h_network_call_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        ev.get("call_id"),
        {
            "mas.network.status_code": int(ev.get("status_code") or 0),
        },
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


# ── Workflow transitions ─────────────────────────────────────────────────────


@register("workflow_transition_start")
def h_workflow_transition_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "WorkflowTransition",
        {
            "mas.boundary": "WorkflowTransition",
            "mas.agent.id": conv.agent_id(ev),
            "mas.transition.type": ev.get("transition_type", ""),
            "mas.transition.arguments": conv.enc(ev.get("arguments"), limit=500),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("workflow_transition_end")
def h_workflow_transition_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        ev.get("call_id"),
        {
            "mas.transition.result": conv.enc(ev.get("result"), limit=500),
        },
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


# ── Agent-to-agent communication ─────────────────────────────────────────────


@register("agent_communication_start")
def h_agent_communication_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    call_id = conv.require_call_id(ev)
    conv.open_span(
        call_id,
        "AgentCommunication",
        {
            "mas.boundary": "AgentCommunication",
            "mas.agent.id": conv.agent_id(ev),
            "mas.communication.type": ev.get("message_type") or "",
            "mas.communication.source": ev.get("source_agent_id") or "",
            "mas.communication.target": ev.get("target_agent_id") or "",
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("agent_communication_end")
def h_agent_communication_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    conv.close_span(
        ev.get("call_id"),
        {
            "mas.communication.correlation_id": ev.get("correlation_id") or "",
        },
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )


@register("contract_call_start")
def h_contract_call_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    # Native KIND_TO_CLASS maps contract_call_* to None. Observe-sdk must
    # not emit them as ProcessingCall or complete-path equivalence drifts.
    if getattr(conv, "_converter_profile", "") == "observe_sdk":
        return
    call_id = conv.span_key(ev)
    conv.open_span(
        call_id,
        "ProcessingCall",
        {
            "mas.boundary": "ProcessingCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.processing.name": ev.get("contract_id") or ev.get("name") or "contract",
            "mas.processing.type": "contract_call",
            "mas.processing.actor": conv.agent_id(ev),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register("contract_call_end")
def h_contract_call_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    if getattr(conv, "_converter_profile", "") == "observe_sdk":
        return
    conv.close_span(conv.span_key(ev), status=ev.get("status", "success"), end_ns=conv.ts_ns(ev))


@register(
    "observability_pre_execute_start",
    "observability_pre_execute_end",
    "observability_post_execute_start",
    "observability_post_execute_end",
    "boundary_ingress",
    "boundary_egress",
    "tool_result_injected",
)
def h_execution_annotation(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """Kernel markers: one CallAnnotation point per record."""
    conv.emit_annotation(ev, "CallAnnotation", {"mas.annotation.kind": str(ev.get("kind") or "")})

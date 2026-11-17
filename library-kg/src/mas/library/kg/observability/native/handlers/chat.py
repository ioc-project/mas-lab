#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``llm_call_start`` / ``llm_call_end`` event kinds (LLMCall).

Each record says what it says: the request on the start, the return on the end. The
``thinking`` category adds the reasoning interval (``thinking_start`` / ``thinking_end``,
``handle_thinking_call``); no interval is inferred from the call's duration.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def format_messages(messages: Any) -> str:
    """Format an OpenAI-style messages list as a readable conversation string.

    Each turn is rendered as::

        [ROLE]
        <content>

    Multi-part (vision/tool) content is flattened to text.  No length limit
    is applied here; callers may truncate for storage.
    """
    if not messages or not isinstance(messages, list):
        return ""
    parts: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "?").upper()
        content = msg.get("content") or ""
        if isinstance(content, list):
            # Structured multi-part content (vision, tool_use, …)
            texts: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    texts.append(part.get("text") or json.dumps(part, ensure_ascii=False)[:120])
                else:
                    texts.append(str(part))
            content = " ".join(texts)
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def number(value: Any, cast: Any) -> Any:
    """``value`` as ``cast`` (int/float), or None when it is unset or not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _reasoning_tokens(usage: Dict[str, Any]) -> Any:
    """Reasoning tokens the provider reported (``completion_tokens_details``), or None."""
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        return number(details["reasoning_tokens"], int)
    return number(usage.get("reasoning_tokens"), int)


def tool_names(tools: List[Any]) -> List[str]:
    """Offered tools as names: the runtime writes names; OpenAI tool dicts are reduced to theirs."""
    names: List[str] = []
    for tool in tools:
        if isinstance(tool, dict):
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            tool = fn.get("name") or tool.get("name") or ""
        if tool:
            names.append(str(tool))
    return names


def _parse_tool_call(tc: Dict[str, Any]) -> Dict[str, str]:
    """One tool call in any accepted shape as ``{id, name, arguments}``.

    Accepts OpenAI (``function.name`` / ``function.arguments``), the runtime's
    ``{toolName, arguments}`` and flat ``{name, arguments}``. ``arguments`` is
    always a JSON string.
    """
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    args = fn.get("arguments") or tc.get("arguments") or "{}"
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False, default=str)
    return {
        "id": str(tc.get("id") or tc.get("tool_call_id") or ""),
        "name": str(fn.get("name") or tc.get("toolName") or tc.get("name") or "tool"),
        "arguments": args,
    }


def _format_tool_calls(tool_calls: Any) -> str:
    """Render tool calls as ``→ name(args)`` lines, args cut at 300 characters.

    The OTel converter renders the same text (``tool_calls_text``), so a
    tool-use turn reads identically in both paths.
    """
    if not tool_calls or not isinstance(tool_calls, list):
        return ""
    parsed = (_parse_tool_call(tc) for tc in tool_calls if isinstance(tc, dict))
    return "\n".join(f"→ {call['name']}({call['arguments'][:300]})" for call in parsed)


def _text_with_tool_calls(content: str, tool_calls: Any) -> str:
    """``content`` followed by the rendered tool calls, if any."""
    tc_text = _format_tool_calls(tool_calls)
    return (content + "\n" + tc_text).strip() if tc_text else content


def response_text(resp: Dict[str, Any]) -> str:
    """Reply text of a response in either shape (OpenAI ``choices`` or flat), without tool calls."""
    choices = resp.get("choices") or []
    if choices and isinstance(choices, list) and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        return str((msg.get("content") if isinstance(msg, dict) else "") or "")
    return str(resp.get("content") or resp.get("text") or "")


def _extract_completion(resp: Dict[str, Any]) -> str:
    """Best-effort extraction of completion text from a response dict.

    Handles three response shapes:
    1. OpenAI canonical: ``choices[0].message.{content, tool_calls}``
    2. Flat dict (our runtime format): ``{content, tool_calls, text}``
    3. Tool-use only: ``content == ''`` but ``tool_calls`` present → rendered
       so the trajectory never shows an empty output for tool-use steps.
    """
    choices = resp.get("choices") or []
    if choices and isinstance(choices, list) and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        msg = msg if isinstance(msg, dict) else {}
        return _text_with_tool_calls(response_text(resp), msg.get("tool_calls"))
    return _text_with_tool_calls(response_text(resp), resp.get("tool_calls"))


def extract_tool_calls_structured(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract structured tool calls from OpenAI-style response payloads."""
    choices = resp.get("choices") or []
    if choices and isinstance(choices, list):
        tool_calls = (choices[0].get("message") or {}).get("tool_calls") or []
    else:
        tool_calls = resp.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        return []
    parsed = (_parse_tool_call(tc) for tc in tool_calls if isinstance(tc, dict))
    return [
        {"toolCallId": c["id"], "toolName": c["name"], "arguments": c["arguments"]} for c in parsed
    ]


def _extract_finish_reason(resp: Dict[str, Any]) -> str:
    choices = resp.get("choices") or []
    if choices and isinstance(choices, list):
        return str(choices[0].get("finish_reason") or "")
    return ""


def handle_llm_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    node = builder.call_partial(event, "LLMCall")
    if node is None:
        return []
    kind = event.get("kind", "")
    node["llmName"] = event.get("llm_name") or event.get("model") or ""
    model = str(event.get("model") or "")

    if kind.endswith("_start"):
        if "/" in model:
            provider, name = model.split("/", 1)
            node["provider"] = provider or "unknown"
            node["modelName"] = name or model
        else:
            node["modelName"] = model
        # A call is named by what it executes: the model (the LLM type's name).
        node["name"] = node["modelName"]
        # Prefer explicit input/prompt field; fall back to formatting the messages list
        # as a readable conversation.
        messages = event.get("messages")
        node["prompt"] = (
            event.get("input")
            or event.get("prompt")
            or format_messages(messages)
            or (json.dumps(messages, ensure_ascii=False) if messages else "")
        )
        temperature = number(event.get("temperature"), float)
        if temperature is not None:
            node["temperature"] = temperature
        max_tokens = number(event.get("max_tokens"), int)
        if max_tokens is not None:
            node["maxTokens"] = max_tokens
        if event.get("reasoning_effort"):
            node["reasoningEffort"] = str(event["reasoning_effort"])
        if isinstance(event.get("tools"), list):
            node["offeredTools"] = tool_names(event["tools"])
    else:
        _read_return(event, node)

    builder.emit_call(event, node)
    if kind.endswith("_start") and model:
        catalog_name = node["modelName"]
        extra = {"provider": node["provider"]} if node.get("provider") else {}
        llm = builder.catalog("LLM", catalog_name, **extra)
        builder.executes(node, "executesLLM", llm)
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            builder.add_edge("usesLLM", builder.catalog("Agent", agent_id, node_id=agent_id), llm)
    return [node]


def _read_return(event: Dict[str, Any], node: Dict[str, Any]) -> None:
    """Fill the return of an ``llm_call_end`` onto its node.

    Usage, finish_reason and tool_calls are at the top level; an OpenAI-shaped
    ``response`` is also accepted. Request fields (``model``, messages, tools) live on
    the start record and are not read here.
    """
    resp: Dict[str, Any] = event.get("response") or {}
    if not isinstance(resp, dict):
        resp = {}
    flat_out = str(event.get("content") or event.get("output") or "").strip()
    usage: Dict[str, Any] = {}
    top_usage = event.get("tokens_used")
    if isinstance(top_usage, dict):
        usage.update(top_usage)
    if resp.get("usage") and isinstance(resp["usage"], dict):
        usage.update({k: v for k, v in resp["usage"].items() if v not in (None, "")})
    event_tool_calls = event.get("tool_calls")
    if resp:
        node["toolCalls"] = extract_tool_calls_structured(resp)
        node["completion"] = _extract_completion(resp)
        node["responseId"] = resp.get("id", "") or ""
    if event_tool_calls:
        # The runtime's own shape wins over an OpenAI-shaped ``response`` on the same event;
        # the OTel converter does the same: text first, then the rendered calls.
        node["toolCalls"] = extract_tool_calls_structured({"tool_calls": event_tool_calls})
        base_text = response_text(resp) or flat_out
        node["completion"] = _text_with_tool_calls(base_text, event_tool_calls)
    if not node.get("completion") and flat_out:
        node["completion"] = flat_out
    sent_max_tokens = number(event.get("max_tokens"), int)
    if sent_max_tokens is not None:
        node["maxTokens"] = sent_max_tokens
    if usage:
        node["promptTokenCount"] = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        node["completionTokenCount"] = (
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        node["totalTokenCount"] = usage.get("total_tokens") or 0
        node["cacheReadTokenCount"] = usage.get("cache_read_input_tokens") or 0
        reasoning = _reasoning_tokens(usage)
        if reasoning is not None:
            node["reasoningTokenCount"] = reasoning
    finish = str(event.get("finish_reason") or _extract_finish_reason(resp) or "")
    if finish:
        node["finishReason"] = finish


def handle_thinking_call(
    event: Dict[str, Any], builder: "NativeGraphBuilder"
) -> List[Dict[str, Any]]:
    """Handle ``thinking_start`` / ``thinking_end``: the reasoning interval of one LLMCall.

    The record's ``parent_call_id`` is the LLM call (its own ``call_id`` is
    ``<llm_call_id>#think``); the LLMCall ``hasThinkingCall`` it. The start names the
    input State (the prompt) in ``state_in`` and the end the output State (the reasoning
    text) in ``state_out``, like every element record. A reasoning token count on the end
    is ``reasoningTokenCount``; the LLM it ran on is the one the record names.
    """
    from mas.library.kg.core.native_graph_builder import call_node_id

    node = builder.call_partial(event, "ThinkingCall")
    if node is None:
        return []
    if str(event.get("kind", "")).endswith("_end"):
        tokens = number(event.get("reasoning_tokens"), int)
        if tokens is not None:
            node["reasoningTokenCount"] = tokens
    builder.emit_call(event, node)
    run_id = str(event.get("run_id") or "")
    parent = str(event.get("parent_call_id") or "")
    if parent:
        builder.add_edge("hasThinkingCall", call_node_id("LLMCall", run_id, parent), node["id"])
    model = str(event.get("model") or event.get("llm_name") or "")
    if model:
        provider, name = model.split("/", 1) if "/" in model else ("", model)
        extra = {"provider": provider} if provider else {}
        # Named by the model it ran on, like its LLMCall.
        node["name"] = name or model
        builder.add_node(node)
        builder.executes(node, "executesLLM", builder.catalog("LLM", name or model, **extra))
    return [node]

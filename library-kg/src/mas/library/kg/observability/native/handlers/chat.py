#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``llm_call_start`` / ``llm_call_end`` event kinds (LLMCall).

Also synthesizes a child ``ThinkingCall`` node when the LLM response carries
a ``thinking`` trace — this is legitimate per-event construction (everything
needed is on this one ``llm_call_end`` event), not a cross-event heuristic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

from mas.library.kg.core.scope_models import mas_namespace

_MAS_NS = mas_namespace()

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


def _number(value: Any, cast: Any) -> Any:
    """``value`` as ``cast`` (int/float), or None when it is unset or not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _tool_names(tools: List[Any]) -> List[str]:
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


def _extract_completion(resp: Dict[str, Any]) -> str:
    """Best-effort extraction of completion text from a response dict.

    Handles three response shapes:
    1. OpenAI canonical: ``choices[0].message.{content, tool_calls}``
    2. Flat dict (our runtime format): ``{content, tool_calls, text}``
    3. Tool-use only: ``content == ''`` but ``tool_calls`` present → rendered
       so the trajectory never shows an empty output for tool-use steps.
    """
    choices = resp.get("choices") or []
    if choices and isinstance(choices, list):
        msg = choices[0].get("message") or {}
        return _text_with_tool_calls(str(msg.get("content") or ""), msg.get("tool_calls"))
    content = str(resp.get("content") or resp.get("text") or "")
    return _text_with_tool_calls(content, resp.get("tool_calls"))


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
    if str(event.get("kind") or "").endswith("_start"):
        ensure = getattr(builder, "ensure_resumed_agent_call", None)
        if callable(ensure):
            ensure(event)
        active = builder._active_exec.get(str(event.get("agent_id") or ""))
        if active and active != event.get("parent_call_id"):
            event = dict(event)
            event["parent_call_id"] = active
    node = builder.upsert_call(event, "LLMCall")
    if node is None:
        return []
    kind = event.get("kind", "")
    new_nodes: List[Dict[str, Any]] = [node]

    if kind.endswith("_start"):
        node.setdefault(
            "llmName",
            event.get("llm_name") or event.get("model") or "llm",
        )
        model = str(event.get("model") or "")
        if "/" in model:
            provider, name = model.split("/", 1)
            node.setdefault("provider", provider or "unknown")
            node.setdefault("modelName", name or model)
        else:
            node.setdefault("modelName", model)
            node.setdefault("provider", "unknown")
        # Same name the OTel normalizer gives an LLMCall, so the two paths pair
        # calls by name: ``LLM call (<provider>/<model>) <id>``.
        label = f"{node['provider']}/{node['modelName'] or 'llm'}"
        node.setdefault("name", f"LLM call ({label}) {event.get('call_id') or ''}".rstrip())
        # Prefer explicit input/prompt field; fall back to formatting the
        # messages list as a readable conversation (no artificial truncation
        # — callers truncate when writing to State.content).
        node.setdefault(
            "prompt",
            event.get("input")
            or event.get("prompt")
            or format_messages(event.get("messages"))
            or json.dumps(event.get("messages") or [], ensure_ascii=False),
        )
        node.setdefault("promptTokenCount", 0)
        node.setdefault("completionTokenCount", 0)
        node.setdefault("totalTokenCount", 0)
        node.setdefault("cacheReadTokenCount", 0)
        node.setdefault("finishReason", "")
        node.setdefault("responseId", "")
        temperature = _number(event.get("temperature"), float)
        node.setdefault("temperature", 0.0 if temperature is None else temperature)
        max_tokens = _number(event.get("max_tokens"), int)
        if max_tokens is not None:
            node.setdefault("maxTokens", max_tokens)
        if isinstance(event.get("tools"), list):
            node.setdefault("offeredTools", _tool_names(event["tools"]))
        return new_nodes

    # llm_call_end — usage/finish_reason/tool_calls at the top level;
    # OpenAI-shaped `response` is also accepted. Request fields (`model`,
    # messages, tools) live on start and are not read here.
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
        node.setdefault("toolCalls", extract_tool_calls_structured(resp))
        node.setdefault("completion", _extract_completion(resp))
        node.setdefault("responseId", resp.get("id", "") or "")
    if event_tool_calls:
        # The runtime's own shape wins over an OpenAI-shaped ``response`` on the same event;
        # the OTel converter does the same: text first, then the rendered calls.
        node["toolCalls"] = extract_tool_calls_structured({"tool_calls": event_tool_calls})
        base_text = str(resp.get("content") or flat_out)
        node["completion"] = _text_with_tool_calls(base_text, event_tool_calls)
    if not node.get("completion") and flat_out:
        node["completion"] = flat_out
    # A cap on the return is the one actually sent, when it differs from the one asked for on start.
    sent_max_tokens = _number(event.get("max_tokens"), int)
    if sent_max_tokens is not None:
        node["maxTokens"] = sent_max_tokens
    if usage:
        node["promptTokenCount"] = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        node["completionTokenCount"] = (
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        node["totalTokenCount"] = usage.get("total_tokens") or 0
        node.setdefault("cacheReadTokenCount", usage.get("cache_read_input_tokens") or 0)
    finish = str(event.get("finish_reason") or _extract_finish_reason(resp) or "")
    if finish:
        node["finishReason"] = finish

    # thinking/reasoning trace is nested under response.thinking
    thinking_text = (event.get("response") or {}).get("thinking") or ""
    if thinking_text and getattr(builder, "include_native_extensions", False):
        call_id = node["callId"]
        llm_start = float(node.get("startTime") or 0)
        llm_end = float(event.get("timestamp") or 0)
        # Thinking occupies the first ~85 % of the LLM call duration.
        think_end = llm_start + (llm_end - llm_start) * 0.85
        tc_id = f"{call_id}-thinking"
        thinking_node = {
            "node_type": "ThinkingCall",
            "id": tc_id,
            "callId": tc_id,
            "executionId": f"ThinkingCall/{tc_id}",
            "agentId": event.get("agent_id", ""),
            "parentCallId": call_id,
            "kindBase": "thinking_call",
            "masUri": _MAS_NS + "ThinkingCall",
            "spanLevel": "thinking",
            "runId": event.get("run_id", ""),
            "startTime": node.get("startTime"),
            "endTime": think_end,
            "thinkingContent": thinking_text,
            "status": "success",
        }
        # Direct write, not upsert_call(): ThinkingCall is fully determined
        # by this one llm_call_end event (no separate start/end pair to
        # merge across), and tc_id is never an AgentCall, so it can never
        # collide with _call_nodes_merge_key's compound "<call_id>::
        # <agent_id>" scheme -- a plain dict key here is correct, not a
        # shortcut around upsert_call's merge semantics.
        builder.call_nodes[tc_id] = thinking_node
        builder.edges.append(
            {
                "edge_type": "hasThinking",
                "from_id": call_id,
                "from_type": "call",
                "to_id": tc_id,
                "to_type": "call",
            }
        )
        new_nodes.append(thinking_node)

    return new_nodes

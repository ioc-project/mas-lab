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

from mas.library.kg.core.oxp_models import mas_namespace

_MAS_NS = mas_namespace()

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def _format_messages(messages: Any) -> str:
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


def _format_tool_calls(tool_calls: Any) -> str:
    """Render a list of OpenAI tool-call dicts as a compact text block."""
    if not tool_calls or not isinstance(tool_calls, list):
        return ""
    lines: List[str] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or "tool"
        args = fn.get("arguments") or tc.get("arguments") or "{}"
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        lines.append(f"→ {name}({args[:300]})")
    return "\n".join(lines)


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
        content = str(msg.get("content") or "")
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            tc_text = _format_tool_calls(tool_calls)
            return (content + "\n" + tc_text).strip()
        return content
    # Flat runtime dict
    content = str(resp.get("content") or resp.get("text") or "")
    tool_calls = resp.get("tool_calls") or []
    if tool_calls:
        tc_text = _format_tool_calls(tool_calls)
        return (content + "\n" + tc_text).strip()
    return content


def _extract_tool_calls_structured(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract structured tool calls from OpenAI-style response payloads."""
    tool_calls: Any = []
    choices = resp.get("choices") or []
    if choices and isinstance(choices, list):
        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
    else:
        tool_calls = resp.get("tool_calls") or []

    out: List[Dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return out
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or tc.get("name") or "tool")
        args = fn.get("arguments") or tc.get("arguments") or "{}"
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        out.append(
            {
                "toolCallId": str(tc.get("id") or tc.get("tool_call_id") or ""),
                "toolName": name,
                "arguments": args,
            }
        )
    return out


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
        # Prefer explicit input/prompt field; fall back to formatting the
        # messages list as a readable conversation (no artificial truncation
        # — callers truncate when writing to State.content).
        node.setdefault(
            "prompt",
            event.get("input")
            or event.get("prompt")
            or _format_messages(event.get("messages"))
            or json.dumps(event.get("messages") or [], ensure_ascii=False),
        )
        node.setdefault("promptTokenCount", 0)
        node.setdefault("completionTokenCount", 0)
        node.setdefault("totalTokenCount", 0)
        node.setdefault("cacheReadTokenCount", 0)
        node.setdefault("finishReason", "")
        node.setdefault("responseId", "")
        node.setdefault("temperature", 0.0)
        return new_nodes

    # llm_call_end — runtime traces put usage/model/finish_reason at the
    # top level (`tokens_used`, `model`, `finish_reason`); OpenAI-shaped
    # `response` is also accepted.
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
    if resp:
        node.setdefault("toolCalls", _extract_tool_calls_structured(resp))
        node.setdefault("completion", _extract_completion(resp))
        node.setdefault("responseId", resp.get("id", "") or "")
        node.setdefault("temperature", event.get("temperature") or 0.0)
    elif flat_out:
        node.setdefault("completion", flat_out)
    if usage:
        node["promptTokenCount"] = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        node["completionTokenCount"] = (
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        node["totalTokenCount"] = usage.get("total_tokens") or 0
        node.setdefault("cacheReadTokenCount", usage.get("cache_read_input_tokens") or 0)
    model = str(event.get("model") or resp.get("model") or node.get("modelName") or "")
    if model:
        if "/" in model:
            provider, name = model.split("/", 1)
            node["provider"] = provider or node.get("provider") or "unknown"
            node["modelName"] = name or model
        else:
            node.setdefault("provider", node.get("provider") or "unknown")
            node["modelName"] = model
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

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Render a human-readable AgentCall output/transcript from its child calls.

Self-contained concern: given an AgentCall node and its already-built child
call nodes (LLMCall, ToolCall, ProcessingCall, ...), build a structured
``outputParts`` list and a flattened ``outputContent`` string. This runs as
a post-processing pass of the OTel path over a released ``norm``
(:mod:`mas.library.kg.observability.otel_completion`), the same way
``core.otel_state_synthesis`` does. The native fold does not render outputs.
"""

from __future__ import annotations

from typing import Any, Dict, List


def render_agent_output_parts(parts: List[Dict[str, Any]]) -> str:
    """Render structured agent output parts to a compatibility text view."""
    lines: List[str] = []
    for part in parts:
        ptype = str(part.get("type") or "")
        text = str(part.get("text") or "").strip()
        if ptype == "agent_output":
            if text:
                lines.append(text)
            continue
        if ptype == "llm_completion":
            if text:
                lines.append(text)
            continue
        if ptype == "context_return":
            if text:
                lines.append(f"[context]\n{text}")
            continue
        if ptype == "tool_call":
            tool_name = str(part.get("toolName") or "tool")
            args = str(part.get("arguments") or "{}").strip()
            lines.append(f"→ {tool_name}({args[:200]})")
            continue
        if ptype == "llm_tool_call":
            tool_name = str(part.get("toolName") or "tool")
            args = str(part.get("arguments") or "{}").strip()
            lines.append(f"→ {tool_name}({args[:200]})")
            continue
        if ptype == "tool_result":
            tool_name = str(part.get("toolName") or "tool")
            if text:
                lines.append(f"[tool:{tool_name}] {text[:300]}")
            continue

    rendered: List[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        rendered.append(line)
    return "\n\n".join(rendered)


def build_agent_output_parts(
    agent_node: Dict[str, Any],
    children: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build structured output parts from AgentCall + child calls.

    Notes
    -----
    We intentionally do *not* perform semantic alignment between LLM-declared
    tool calls and executed ToolCall nodes in the online normalizer path.
    Such matching can be expensive and brittle (e.g. NL parameters), and is
    better handled in a dedicated offline annotation layer.
    """
    parts: List[Dict[str, Any]] = []

    explicit_output = str(agent_node.get("outputRaw") or "").strip()
    if explicit_output:
        parts.append(
            {
                "type": "agent_output",
                "source": "execution_end.output",
                "text": explicit_output,
            }
        )

    for child in sorted(children, key=lambda c: float(c.get("startTime") or 0.0)):
        ctype = str(child.get("node_type") or "")
        child_call_id = str(child.get("callId") or child.get("id") or "")
        if ctype == "LLMCall":
            completion = str(child.get("completion") or "").strip()
            if completion:
                parts.append(
                    {
                        "type": "llm_completion",
                        "callId": child_call_id,
                        "model": str(child.get("modelName") or ""),
                        "text": completion,
                    }
                )
            for tc in child.get("toolCalls") or []:
                if not isinstance(tc, dict):
                    continue
                parts.append(
                    {
                        "type": "llm_tool_call",
                        "callId": child_call_id,
                        "toolCallId": str(tc.get("toolCallId") or ""),
                        "toolName": str(tc.get("toolName") or "tool"),
                        "arguments": str(tc.get("arguments") or "{}"),
                    }
                )
            continue

        if ctype == "ProcessingCall":
            ctx_out = str(
                child.get("processingOutput")
                or child.get("outputContent")
                or child.get("output")
                or ""
            ).strip()
            if ctx_out:
                parts.append(
                    {
                        "type": "context_return",
                        "callId": child_call_id,
                        "processingName": str(child.get("processingName") or ""),
                        "text": ctx_out,
                    }
                )
            continue

        if ctype == "ToolCall":
            tool_name = str(child.get("toolName") or "tool")
            args = str(child.get("toolArguments") or child.get("arguments") or "{}").strip()
            parts.append(
                {
                    "type": "tool_call",
                    "callId": child_call_id,
                    "toolName": tool_name,
                    "arguments": args,
                }
            )
            tool_out = str(child.get("toolOutput") or child.get("result") or "").strip()
            if tool_out:
                parts.append(
                    {
                        "type": "tool_result",
                        "callId": child_call_id,
                        "toolName": tool_name,
                        "text": tool_out,
                    }
                )

    return parts


def materialize_agent_outputs(call_nodes: Dict[str, Dict[str, Any]]) -> None:
    """Populate AgentCall.outputParts and derive outputContent from this structure.

    This runs for all AgentCall nodes, not only empty outputs, so downstream
    consumers can rely on a stable structured representation.
    """
    children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for node in call_nodes.values():
        parent_id = str(node.get("parentCallId") or "")
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(node)

    for node in call_nodes.values():
        if node.get("node_type") != "AgentCall":
            continue

        agent_call_id = str(node.get("callId") or node.get("id") or "")
        if not agent_call_id:
            continue
        children = children_by_parent.get(agent_call_id, [])
        parts = build_agent_output_parts(node, children)
        if not parts:
            continue

        node["outputParts"] = parts
        rendered = render_agent_output_parts(parts)
        if rendered:
            node["outputContent"] = rendered


def agent_output_text(node: Dict[str, Any]) -> str:
    """Internal canonical accessor for AgentCall output text.

    Preference order:
    1) render from outputParts
    2) outputRaw
    """
    parts = node.get("outputParts")
    if isinstance(parts, list) and parts:
        rendered = render_agent_output_parts(parts)
        if rendered:
            return rendered
    raw = str(node.get("outputRaw") or "").strip()
    if raw:
        return raw
    return ""


def derive_tool_use_completions(call_nodes: Dict[str, Dict[str, Any]]) -> None:
    """Derive a readable ``completion`` summary for tool-use-only LLM calls.

    When the LLM responds with a tool call, the OpenAI ``content`` field is
    empty by design (the intent is encoded in ``tool_calls``) — this is
    normal OpenAI-API-shaped behavior, not a data-quality gap. Our runtime
    captures the tool-call details as separate ``tool_call_start/end``
    events rather than embedding them in the ``response`` dict, so the
    LLMCall's own ``completion`` field would otherwise read as empty for a
    perfectly ordinary tool-use turn.

    For each LLMCall whose ``completion`` is empty, find the ToolCall nodes
    that share the same ``parent_call_id`` (both are children of the same
    AgentCall) and whose ``start_time`` is >= the LLM call's end_time, and
    render a human-readable summary of what was called from them.
    """
    tool_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for node in call_nodes.values():
        if node["node_type"] == "ToolCall":
            pid = node.get("parentCallId") or ""
            tool_by_parent.setdefault(pid, []).append(node)

    for node in call_nodes.values():
        if node["node_type"] != "LLMCall":
            continue
        if (node.get("completion") or "").strip():
            continue  # already has a completion
        llm_end = float(node.get("endTime") or 0)
        pid = node.get("parentCallId") or ""
        # Tool calls emitted after this LLM call under the same agent call
        candidates = [
            t
            for t in tool_by_parent.get(pid, [])
            if float(t.get("startTime") or 0) >= llm_end - 0.01
        ]
        if not candidates:
            continue
        lines: List[str] = []
        for tc in sorted(candidates, key=lambda t: float(t.get("startTime") or 0)):
            name = tc.get("toolName") or "tool"
            args = tc.get("toolArguments") or "{}"
            out = tc.get("toolOutput") or ""
            lines.append(f"→ {name}({args[:200]})")
            if out:
                lines.append(f"   result: {str(out)[:300]}")
        node["completion"] = "\n".join(lines)

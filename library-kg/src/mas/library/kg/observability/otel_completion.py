#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a MAS Lab span carries that ``norm`` does not map onto the graph.

``norm`` reads the ``gen_ai.*`` and ``ioa_observe.*`` attributes. The MAS Lab
converter also writes the request and return of a call under ``mas.*``
(``mas.llm.max_tokens``, ``mas.llm.tools``, ``mas.llm.response``, ...) and the
outcome of the whole run on the ``TaskCall`` span. This module copies those onto
the nodes ``norm`` built, so the graph read from spans carries what the graph
read from native events carries. It adds attributes; it never changes a node
``norm`` produced except the ones ``norm`` could only guess (the MAS call's
outcome).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Tuple

from mas.library.kg.core.otel_state_synthesis import (
    session_trajectory_anchors,
    synthesize_states_and_transitions,
)
from mas.library.kg.core.output_rendering import (
    agent_output_text,
    derive_tool_use_completions,
    materialize_agent_outputs,
)
from mas.library.kg.observability.native.handlers.chat import (
    extract_tool_calls_structured,
    format_messages,
)
from mas.library.kg.observability.native.handlers.processing import render_assembled_messages

_NS_PER_MS = 1_000_000.0

_CALL_TYPES = frozenset({"AgentCall", "LLMCall", "ToolCall", "ProcessingCall"})
# What norm synthesizes from consecutive spans to chain calls together. MAS Lab
# spans carry the calls themselves, so the chain is rebuilt from them instead.
_NORM_TRAJECTORY_TYPES = frozenset({"State", "Transition", "Processing", "ProcessingCall"})


def _attrs(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("SpanAttributes") or {}


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _number(value: Any, cast: Any) -> Any:
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _complete_llm_call(node: Dict[str, Any], attrs: Dict[str, Any]) -> None:
    """Request and return fields of one LLMCall, from its ``mas.llm.*`` attributes."""
    model = str(attrs.get("mas.llm.model") or "")
    if model:
        node.setdefault("llmName", model)
    messages = _json(attrs.get("mas.llm.messages"))
    if isinstance(messages, list) and messages:
        node.setdefault("prompt", format_messages(messages))
    response = attrs.get("mas.llm.response")
    if response:
        node.setdefault("completion", str(response))
    tool_calls = _json(attrs.get("mas.llm.tool_calls"))
    if isinstance(tool_calls, list) and tool_calls:
        node.setdefault("toolCalls", extract_tool_calls_structured({"tool_calls": tool_calls}))
    max_tokens = _number(attrs.get("mas.llm.max_tokens"), int)
    if max_tokens is not None:
        node.setdefault("maxTokens", max_tokens)
    tools = _json(attrs.get("mas.llm.tools"))
    if isinstance(tools, list):
        node.setdefault("offeredTools", [str(t) for t in tools])


def _complete_mas_call(node: Dict[str, Any], row: Dict[str, Any]) -> None:
    """The MAS call's outcome, from the ``TaskCall`` span that covers the run.

    ``norm`` builds the MASCall from the session alone, so it has no end: duration 0
    and not successful, however the run went.
    """
    attrs = _attrs(row)
    success = attrs.get("execution.success")
    if success is not None:
        node["success"] = str(success).strip().lower() == "true"
    duration = _number(row.get("Duration"), float)
    if duration is not None:
        node["duration"] = duration / _NS_PER_MS


def _ends(edge: Dict[str, Any]) -> Tuple[str, str]:
    return (
        str(edge.get("from_id") or edge.get("source_id") or ""),
        str(edge.get("to_id") or edge.get("target_id") or ""),
    )


def _seconds(value: Any) -> float | None:
    return _number(value, float)


def _processing_call(row: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """A ProcessingCall node from its span, with the fields a native one carries."""
    attrs = _attrs(row)
    name = str(attrs.get("mas.processing.name") or "processing")
    start = _seconds(attrs.get("ioa_start_time"))
    duration = _number(row.get("Duration"), float)
    messages = _json(attrs.get("mas.processing.messages"))
    output = render_assembled_messages(messages) if isinstance(messages, list) else ""
    span_id = str(row.get("SpanId") or "")
    node: Dict[str, Any] = {
        "node_type": "ProcessingCall",
        "id": f"processingcall:{span_id}",
        "spanId": span_id,
        "parentSpanId": str(row.get("ParentSpanId") or ""),
        "sessionId": session_id,
        "name": name,
        "processingName": name,
        "processingType": str(attrs.get("mas.processing.type") or ""),
        "agentId": str(attrs.get("mas.agent.id") or ""),
        "inputContent": str(attrs.get("mas.processing.input") or ""),
        "processingOutput": output or str(attrs.get("mas.processing.output") or ""),
        "success": str(attrs.get("execution.success") or "true").strip().lower() == "true",
    }
    if start is not None:
        node["startTime"] = start
        if duration is not None:
            node["endTime"] = start + duration / _NS_PER_MS / 1000.0
    if duration is not None:
        node["duration"] = duration / _NS_PER_MS
    return node


def _call_content(node: Dict[str, Any], attrs: Dict[str, Any]) -> None:
    """The input and output text the trajectory is built from, from a call's span.

    Taken from the ``mas.*`` attributes, which hold the text as the runtime wrote
    it; ``norm`` may already have set a field from a ``gen_ai.*`` attribute, which
    is a re-rendering of it.
    """
    ntype = node["node_type"]
    fields: Dict[str, str] = {}
    if ntype == "AgentCall":
        fields = {"inputContent": "mas.input", "outputRaw": "mas.output"}
    elif ntype == "ToolCall":
        fields = {
            "toolName": "mas.tool.name",
            "toolArguments": "mas.tool.input",
            "toolOutput": "mas.tool.output",
            "barrierId": "mas.tool.barrier_id",
        }
    for field, attr in fields.items():
        if attrs.get(attr) is not None:
            node[field] = str(attrs[attr])


def _carries_processing_spans(spans: Sequence[Dict[str, Any]]) -> bool:
    return any(_attrs(row).get("mas.boundary") == "ProcessingCall" for row in spans)


def rebuild_trajectory(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    spans: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replace ``norm``'s guessed trajectory with the one the native path builds.

    The States, Transitions and Processing nodes ``norm`` synthesizes chain
    consecutive spans without knowing what each call took in and returned. The
    spans of a MAS Lab run do say, and they include the ProcessingCalls, so the
    same :func:`synthesize_states_and_transitions` the native path runs builds the
    trajectory here too. A run then reads alike from events and from spans.

    Spans that carry no ProcessingCall (another producer, or a trace from before
    they were exported) say too little to rebuild from: ``norm``'s trajectory stays.
    """
    if not _carries_processing_spans(spans):
        return nodes, edges
    by_span = {str(row.get("SpanId") or ""): row for row in spans}
    dropped = {str(n.get("id")) for n in nodes if n.get("node_type") in _NORM_TRAJECTORY_TYPES}
    nodes = [n for n in nodes if str(n.get("id")) not in dropped]
    edges = [e for e in edges if not (set(_ends(e)) & dropped)]

    sessions = [n for n in nodes if n.get("node_type") == "Session"]
    first_session = str((sessions[0:1] or [{}])[0].get("sessionId") or "")
    node_by_span = {str(n.get("spanId")): n for n in nodes if n.get("spanId")}
    for row in spans:
        if _attrs(row).get("mas.boundary") == "ProcessingCall":
            owner = node_by_span.get(str(row.get("ParentSpanId") or ""), {})
            call = _processing_call(row, str(owner.get("sessionId") or first_session))
            if "startTime" not in call and owner.get("startTime") is not None:
                call["startTime"] = owner["startTime"]
            nodes.append(call)
            node_by_span[call["spanId"]] = call

    call_nodes: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        if node.get("node_type") not in _CALL_TYPES:
            continue
        attrs = _attrs(by_span.get(str(node.get("spanId") or ""), {}))
        node["callId"] = str(attrs.get("mas.call.id") or node.get("callId") or node["id"])
        node.setdefault("sessionId", first_session)
        node["runId"] = str(node["sessionId"]).removeprefix("session-")
        _call_content(node, attrs)
        call_nodes[str(node["id"])] = node

    for node in call_nodes.values():
        parent = node_by_span.get(str(node.get("parentSpanId") or ""))
        node["parentCallId"] = str((parent or {}).get("callId") or "")
        if node["node_type"] == "ProcessingCall" and (parent or {}).get("node_type") == "AgentCall":
            edges.append(
                {"edge_type": "hasProcessingCall", "from_id": parent["id"], "to_id": node["id"]}
            )

    # Siblings chain in start order; equal start times keep the order the spans began in.
    began = {
        str(r.get("SpanId") or ""): i
        for i, r in enumerate(sorted(spans, key=lambda r: str(r.get("Timestamp") or "")))
    }
    ordered = sorted(
        call_nodes.values(),
        key=lambda n: (float(n.get("startTime") or 0), began.get(str(n.get("spanId") or ""), 0)),
    )
    id_of_call = {str(n["callId"]): str(n["id"]) for n in ordered}
    by_call_id = {str(n["callId"]): n for n in ordered}
    for session in sessions:
        sid = str(session.get("sessionId") or "")
        mine = {str(n["id"]): n for n in ordered if n.get("sessionId") == sid}
        if not mine:
            continue
        derive_tool_use_completions(mine)
        materialize_agent_outputs(mine)
        st_nodes, st_edges = synthesize_states_and_transitions(mine, agent_output_text)
        for node in st_nodes:
            node["sessionId"] = sid
        for edge in st_edges:
            for end in ("from_id", "to_id"):
                edge[end] = id_of_call.get(str(edge.get(end)), edge.get(end))
        nodes.extend(st_nodes)
        edges.extend(st_edges)
        edges.extend(session_trajectory_anchors([session], nodes, by_call_id))
    return nodes, edges


def complete_otel_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    spans: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Add what ``mas.*`` span attributes carry to the ``norm`` graph; return ``(nodes, edges)``.

    ``spans`` are ClickHouse-shaped rows.
    """
    by_span = {str(row.get("SpanId") or ""): row for row in spans}
    task_call = next((row for row in spans if _attrs(row).get("mas.boundary") == "TaskCall"), None)
    for node in nodes:
        ntype = node.get("node_type")
        if ntype == "LLMCall":
            row = by_span.get(str(node.get("spanId") or ""))
            if row is not None:
                _complete_llm_call(node, _attrs(row))
        elif ntype == "MASCall" and task_call is not None:
            _complete_mas_call(node, task_call)
    return rebuild_trajectory(nodes, edges, spans)

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What the OTel path adds from ``mas.*`` span attributes that ``norm`` does not map."""

from __future__ import annotations

import json

from mas.library.kg.observability.otel_completion import (
    _complete_llm_call,
    _complete_mas_call,
    _processing_call,
    complete_otel_graph,
)


def test_llm_call_gets_request_and_return_fields_from_its_span() -> None:
    node: dict = {"node_type": "LLMCall"}
    _complete_llm_call(
        node,
        {
            "mas.llm.model": "azure/gpt-4o",
            "mas.llm.messages": json.dumps([{"role": "user", "content": "hi"}]),
            "mas.llm.response": "→ t({})",
            "mas.llm.tool_calls": json.dumps([{"toolName": "t", "arguments": {}}]),
            "mas.llm.max_tokens": "512",
            "mas.llm.tools": json.dumps(["t", "u"]),
        },
    )
    assert node["llmName"] == "azure/gpt-4o"
    assert node["prompt"] == "[USER]\nhi"
    assert node["completion"] == "→ t({})"
    assert [c["toolName"] for c in node["toolCalls"]] == ["t"]
    assert node["maxTokens"] == 512
    assert node["offeredTools"] == ["t", "u"]


def test_llm_call_keeps_what_norm_already_set_and_survives_junk() -> None:
    node: dict = {"node_type": "LLMCall", "completion": "kept"}
    _complete_llm_call(
        node, {"mas.llm.response": "other", "mas.llm.max_tokens": "auto", "mas.llm.tools": "{"}
    )
    assert node["completion"] == "kept"
    assert "maxTokens" not in node and "offeredTools" not in node


def test_mas_call_takes_its_outcome_from_the_task_span() -> None:
    node: dict = {"node_type": "MASCall", "success": False, "duration": 0.0}
    _complete_mas_call(
        node, {"Duration": 2_500_000_000, "SpanAttributes": {"execution.success": "true"}}
    )
    assert node["success"] is True
    assert node["duration"] == 2500.0


def test_processing_call_node_carries_the_assembled_prompt_as_text() -> None:
    row = {
        "SpanId": "abc",
        "ParentSpanId": "agent",
        "Duration": 1_000_000,
        "SpanAttributes": {
            "mas.processing.name": "context assembly",
            "mas.processing.type": "context_assembly",
            "mas.agent.id": "planner",
            "ioa_start_time": "10.0",
            "execution.success": "true",
            "mas.processing.messages": json.dumps([{"role": "user", "content": "hi"}]),
        },
    }
    node = _processing_call(row, "sess")
    assert node["processingOutput"] == "user: hi"
    assert node["processingName"] == "context assembly"
    assert node["startTime"] == 10.0 and node["endTime"] == 10.001
    assert node["success"] is True


def _span(span_id: str, parent: str, **attrs: str) -> dict:
    return {
        "SpanId": span_id,
        "ParentSpanId": parent,
        "Duration": 1_000_000,
        "SpanAttributes": attrs,
    }


def test_norm_trajectory_is_replaced_by_one_built_from_the_calls() -> None:
    nodes = [
        {"node_type": "Session", "id": "s", "sessionId": "s"},
        {"node_type": "AgentCall", "id": "a", "spanId": "sa", "startTime": 1.0, "endTime": 9.0},
        {
            "node_type": "LLMCall",
            "id": "l",
            "spanId": "sl",
            "parentSpanId": "sa",
            "startTime": "2.0",
            "endTime": "3.0",
        },
        {"node_type": "State", "id": "norm-state"},
        {"node_type": "Transition", "id": "norm-trans"},
        {"node_type": "Processing", "id": "norm-proc"},
    ]
    edges = [{"edge_type": "hasState", "from_id": "a", "to_id": "norm-state"}]
    spans = [
        _span(
            "sa",
            "",
            **{
                "mas.boundary": "AgentCall",
                "mas.call.id": "A",
                "mas.input": "q",
                "mas.output": "done",
            },
        ),
        _span(
            "sp",
            "sa",
            **{"mas.boundary": "ProcessingCall", "mas.call.id": "P", "mas.processing.name": "x"},
        ),
        _span(
            "sl",
            "sa",
            **{
                "mas.boundary": "LLMCall",
                "mas.call.id": "L",
                "mas.llm.messages": json.dumps([{"role": "user", "content": "q"}]),
                "mas.llm.response": "answer",
            },
        ),
    ]
    out_nodes, out_edges = complete_otel_graph(nodes, edges, spans)
    ids = {n["id"] for n in out_nodes}
    assert not ({"norm-state", "norm-trans", "norm-proc"} & ids)
    assert not [e for e in out_edges if "norm-state" in (e["from_id"], e["to_id"])]
    states = [n for n in out_nodes if n["node_type"] == "State"]
    assert {"q", "answer"} <= {s["content"] for s in states}
    assert [n for n in out_nodes if n["node_type"] == "Transition"]
    assert all(s["sessionId"] == "s" for s in states)


def test_spans_without_processing_steps_keep_norms_trajectory() -> None:
    """Another producer's spans, or an older trace: too little to rebuild from."""
    nodes = [
        {"node_type": "Session", "id": "s", "sessionId": "s"},
        {"node_type": "AgentCall", "id": "a", "spanId": "sa"},
        {"node_type": "State", "id": "norm-state"},
        {"node_type": "ProcessingCall", "id": "norm-bridge"},
    ]
    edges = [{"edge_type": "hasState", "from_id": "a", "to_id": "norm-state"}]
    spans = [_span("sa", "", **{"mas.boundary": "AgentCall", "mas.call.id": "A"})]
    out_nodes, out_edges = complete_otel_graph(nodes, edges, spans)
    assert {n["id"] for n in out_nodes} == {"s", "a", "norm-state", "norm-bridge"}
    assert out_edges == edges


def test_two_sessions_in_one_file_get_their_own_trajectories() -> None:
    def agent(i: str, session: str, start: float) -> dict:
        return {
            "node_type": "AgentCall",
            "id": f"agent-{i}",
            "spanId": f"sa{i}",
            "sessionId": session,
            "startTime": start,
        }

    nodes = [
        {"node_type": "Session", "id": "s1", "sessionId": "s1"},
        {"node_type": "Session", "id": "s2", "sessionId": "s2"},
        agent("1", "s1", 1.0),
        agent("2", "s2", 2.0),
    ]
    spans = [
        _span(
            "sa1",
            "",
            **{
                "mas.boundary": "AgentCall",
                "mas.call.id": "A1",
                "mas.input": "one",
                "mas.output": "o1",
            },
        ),
        _span(
            "sa2",
            "",
            **{
                "mas.boundary": "AgentCall",
                "mas.call.id": "A2",
                "mas.input": "two",
                "mas.output": "o2",
            },
        ),
        _span("sp", "sa1", **{"mas.boundary": "ProcessingCall", "mas.call.id": "P"}),
    ]
    out_nodes, out_edges = complete_otel_graph(nodes, [], spans)
    by_session = {
        sid: {
            n["content"] for n in out_nodes if n["node_type"] == "State" and n["sessionId"] == sid
        }
        for sid in ("s1", "s2")
    }
    assert {"one", "o1"} <= by_session["s1"] and "two" not in by_session["s1"]
    assert {"two", "o2"} <= by_session["s2"]
    anchored = {e["from_id"] for e in out_edges if e["edge_type"] == "hasInitialState"}
    assert {"s1", "s2"} <= anchored

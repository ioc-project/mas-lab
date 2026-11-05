#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.library.kg.core.ontology_align import align_graph, canonicalize_edge_type


def test_canonical_contains_becomes_typed_has_call():
    assert canonicalize_edge_type("contains", "AgentCall", "LLMCall") == "hasLLMCall"
    assert canonicalize_edge_type("hasCall", "Run", "AgentCall") is None
    assert canonicalize_edge_type("realizes", "Transition", "AgentCall") == "representsExecution"
    assert canonicalize_edge_type("fromState", "Transition", "State") is None


def test_align_graph_strips_oxp_class_layer_and_fills_id():
    nodes, edges = align_graph(
        [
            {
                "node_type": "AgentCall",
                "callId": "c1",
                "id": "c1",
                "sessionId": "s1",
                "agentId": "u1",
                "layer": "execution",
                "startTime": 1.0,
                "endTime": 1.5,
            },
            {
                "node_type": "Session",
                "id": "s1",
                "sessionId": "s1",
                "appName": "demo",
                "startTime": 1.0,
                "endTime": 2.0,
            },
            {"node_type": "MASCall", "id": "m1", "callId": "m1", "layer": "execution"},
            {
                "node_type": "State",
                "id": "st1",
                "stateNodeId": "st1",
                "sessionId": "s1",
                "layer": "trajectory",
                "content": "hi",
            },
        ],
        [{"edge_type": "contains", "from_id": "m1", "to_id": "c1"}],
        emit_topology=True,
    )
    by_type = {n["node_type"]: n for n in nodes}
    assert "layer" not in by_type["AgentCall"]
    assert by_type["AgentCall"]["block"] == "execution"
    assert by_type["AgentCall"]["duration"] == 500.0
    assert by_type["State"]["layer"] == "normalized"
    assert any(e["edge_type"] == "hasAgentCall" for e in edges)
    assert any(e["edge_type"] == "executesSession" for e in edges)
    assert any(n["node_type"] == "MAS" for n in nodes)


def test_align_fills_agent_catalog_agent_id():
    nodes, _edges = align_graph(
        [
            {"node_type": "Agent", "id": "moderator", "name": "moderator"},
            {
                "node_type": "AgentCall",
                "id": "ac1",
                "agentId": "moderator",
                "spanId": "aaaabbbbccccdddd",
            },
        ],
        [],
        emit_topology=True,
    )
    agent = next(n for n in nodes if n["node_type"] == "Agent")
    assert agent["agentId"] == "moderator"


def test_align_does_not_mint_agent_from_mascall_agent_id():
    nodes, _edges = align_graph(
        [
            {
                "node_type": "MASCall",
                "id": "mc1",
                "name": "mas",
                "agentId": "mas",
            },
            {"node_type": "Agent", "id": "moderator", "name": "moderator"},
            {
                "node_type": "AgentCall",
                "id": "ac1",
                "agentId": "moderator",
            },
        ],
        [],
        emit_topology=True,
    )
    agents = [n for n in nodes if n["node_type"] == "Agent"]
    assert {n.get("agentId") or n.get("name") for n in agents} == {"moderator"}


def test_align_does_not_emit_uses_tool_or_calls_agent():
    nodes, edges = align_graph(
        [
            {"node_type": "Agent", "id": "schedule_agent", "name": "schedule_agent"},
            {
                "node_type": "AgentCall",
                "id": "ac1",
                "agentId": "schedule_agent",
                "spanId": "1111222233334444",
            },
            {
                "node_type": "ToolCall",
                "id": "tc1",
                "agentId": "schedule_agent",
                "toolName": "lookup_schedule",
                "parentSpanId": "1111222233334444",
            },
        ],
        [],
        emit_topology=True,
    )
    assert [e for e in edges if e["edge_type"] in {"usesTool", "callsAgent"}] == []
    assert any(n["node_type"] == "Tool" for n in nodes)

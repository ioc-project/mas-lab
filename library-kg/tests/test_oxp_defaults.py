#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Default native KG is SCOPE attributes and categories only."""

from __future__ import annotations

from mas.library.kg.core.oxp_models import (
    DEFERRED_EDGE_TYPES,
    GRAPH_KEYS,
    allowed_node_keys,
    restrict_graph,
)
from mas.library.kg.pipeline import build_kg_document


def _events() -> list[dict]:
    return [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "llm_call_start",
            "timestamp": 1.1,
            "run_id": "r1",
            "call_id": "llm1",
            "parent_call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "llm_call_end",
            "timestamp": 1.2,
            "run_id": "r1",
            "call_id": "llm1",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "model": "azure/gpt-4o-mini",
            "tokens_used": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            "finish_reason": "stop",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 1.3,
            "run_id": "r1",
            "call_id": "t1",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "tool_name": "search",
        },
        {
            "kind": "tool_call_end",
            "timestamp": 1.4,
            "run_id": "r1",
            "call_id": "t1",
            "agent_id": "planner",
            "status": "success",
        },
        {
            "kind": "rag_query_start",
            "timestamp": 1.45,
            "run_id": "r1",
            "call_id": "rag1",
            "parent_call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "rag_query_end",
            "timestamp": 1.46,
            "run_id": "r1",
            "call_id": "rag1",
            "agent_id": "planner",
            "status": "success",
        },
        {
            "kind": "execution_end",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "ok",
        },
    ]


def test_default_kg_drops_native_extension_nodes_and_deferred_edges() -> None:
    doc = build_kg_document(_events(), run_id="r1")
    types = {n.get("node_type") for n in doc["nodes"]}
    assert "RAGQuery" not in types
    assert "AgentCall" in types
    assert "LLMCall" in types
    assert not {e.get("edge_type") for e in doc["edges"]} & DEFERRED_EDGE_TYPES


def test_native_extensions_layer_keeps_rag_query() -> None:
    doc = build_kg_document(_events(), run_id="r1", include_native_extensions=True)
    assert any(n.get("node_type") == "RAGQuery" for n in doc["nodes"])


def test_default_kg_keeps_only_oxp_attributes() -> None:
    doc = build_kg_document(_events(), run_id="r1")
    llm = next(n for n in doc["nodes"] if n.get("node_type") == "LLMCall")
    extra = set(llm) - allowed_node_keys("LLMCall")
    assert extra == set()
    assert llm["promptTokenCount"] == 10
    assert llm["finishReason"] == "stop"
    assert "callId" not in llm
    assert "agentId" not in llm
    tool = next(n for n in doc["nodes"] if n.get("node_type") == "ToolCall")
    assert tool["name"] == "search"
    assert "toolName" not in tool
    agent = next(n for n in doc["nodes"] if n.get("node_type") == "AgentCall")
    assert agent["name"] == "planner"
    assert "agentId" not in agent


def test_all_attributes_keeps_native_fields() -> None:
    doc = build_kg_document(_events(), run_id="r1", all_attributes=True)
    llm = next(n for n in doc["nodes"] if n.get("node_type") == "LLMCall")
    assert llm["callId"] == "llm1"
    assert llm["agentId"] == "planner"


def test_restrict_graph_drops_deferred_edges_even_with_all_attributes() -> None:
    nodes = [{"node_type": "AgentCall", "id": "a", "callId": "c1"}]
    edges = [
        {"edge_type": "hasLLMCall", "from_id": "a", "to_id": "b", "weight": 1},
        {"edge_type": "callsAgent", "from_id": "a", "to_id": "agent"},
        {"edge_type": "usesTool", "from_id": "agent", "to_id": "tool"},
    ]
    out_nodes, out_edges = restrict_graph(nodes, edges, all_attributes=True)
    assert out_nodes[0]["callId"] == "c1"
    assert {e["edge_type"] for e in out_edges} == {"hasLLMCall"}
    stripped_nodes, stripped_edges = restrict_graph(nodes, edges, all_attributes=False)
    assert "callId" not in stripped_nodes[0]
    assert set(stripped_edges[0]) <= GRAPH_KEYS

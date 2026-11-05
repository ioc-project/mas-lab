# SPDX-License-Identifier: Apache-2.0
"""Ontology alignment gate — kind map must not target absent OWL classes."""

from __future__ import annotations

import pytest

from mas.library.kg.core.event_mappings import KIND_TO_CLASS
from mas.library.kg.core.graph_builder import extract_graph
from mas.library.kg.core.verifier import KNOWN_NODE_TYPES


def test_skill_execution_maps_to_skill_call() -> None:
    assert KIND_TO_CLASS["skill_execution_start"] == "SkillCall"
    assert KIND_TO_CLASS["skill_execution_end"] == "SkillCall"
    if not KNOWN_NODE_TYPES:
        # KNOWN_NODE_TYPES is parsed from the real ontology TTL (see
        # core/verifier.py's _OntologyIndex) -- empty means scope_ontology
        # or rdflib isn't installed, not that the alignment is actually
        # broken.
        pytest.importorskip("scope_ontology")
        pytest.importorskip("rdflib")
    assert "SkillCall" in KNOWN_NODE_TYPES


def test_governance_kinds_map_to_governance_event() -> None:
    gov_kinds = [
        k
        for k in KIND_TO_CLASS
        if k.startswith("governance")
        or k
        in {
            "audit",
            "policy_denial",
            "policy_allow",
            "budget_event",
            "transformation_event",
            "control_intervention",
            "hitl_gate",
        }
    ]
    assert gov_kinds, "expected governance-related kinds in map"
    for kind in gov_kinds:
        assert KIND_TO_CLASS[kind] == "GovernanceEvent", kind


def test_checkpoint_kinds_mapped() -> None:
    assert KIND_TO_CLASS.get("checkpoint_start") == "CallAnnotation"
    assert KIND_TO_CLASS.get("checkpoint_end") == "CallAnnotation"


def test_alignment_report_script_runs() -> None:
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "ontology_alignment_report.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1)
    assert "kind\tmas_class" in proc.stdout


def test_session_trajectory_anchors_use_state_transition_chain() -> None:
    run_id = "demo_123e4567-e89b-12d3-a456-426614174000"
    normalized = [
        {
            "kind": "execution_start",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "call-1",
            "timestamp": 1.0,
            "input": "hello",
        },
        {
            "kind": "execution_end",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "call-1",
            "timestamp": 2.0,
            "output": "world",
            "status": "success",
        },
        {
            "kind": "execution_start",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "call-2",
            "timestamp": 3.0,
            "input": "again",
        },
        {
            "kind": "execution_end",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "call-2",
            "timestamp": 4.0,
            "output": "done",
            "status": "success",
        },
    ]

    nodes, edges = extract_graph(normalized, all_attributes=True)
    transitions = {
        node["transitionId"]: node for node in nodes if node.get("node_type") == "Transition"
    }
    session = next(node for node in nodes if node.get("node_type") == "Session")

    assert any(
        edge["edge_type"] == "hasInitialState" and edge["from_id"] == session["id"]
        for edge in edges
    )
    assert any(
        edge["edge_type"] == "hasFinalState" and edge["from_id"] == session["id"] for edge in edges
    )
    assert any(
        edge["edge_type"] == "inputTo"
        and edge["from_type"] == "state"
        and edge["to_type"] == "transition"
        and edge["to_id"] in transitions
        for edge in edges
    )
    assert not any(
        edge["edge_type"] == "leadsTo" and edge.get("from_type") == "state" for edge in edges
    )


def test_session_anchors_stay_connected_when_top_call_has_children() -> None:
    """Session hasInitialState/hasFinalState must land on States reachable
    from each other via an (inputTo/leadsTo)+ path -- exactly what
    SessionShape's connectivity check requires.

    Regression test: a top-level AgentCall that has children (LLMCall,
    ToolCall) got its own wrapper transition included in a flat,
    session-wide timestamp sort used to pick the session's anchors. Since
    that wrapper transition is deliberately excluded from the sibling chain
    its children form, picking it as an anchor left the session's
    hasInitialState/hasFinalState disconnected from the real trajectory.
    """
    run_id = "demo_223e4567-e89b-12d3-a456-426614174000"
    normalized = [
        {
            "kind": "execution_start",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "agent-1",
            "timestamp": 1.0,
            "input": "plan a trip",
        },
        {
            "kind": "llm_call_start",
            "mas_class": "LLMCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "llm-1",
            "parent_call_id": "agent-1",
            "timestamp": 2.0,
            "prompt": "prompt text",
        },
        {
            "kind": "llm_call_end",
            "mas_class": "LLMCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "llm-1",
            "parent_call_id": "agent-1",
            "timestamp": 3.0,
            "completion": "completion text",
        },
        {
            "kind": "tool_call_start",
            "mas_class": "ToolCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "tool-1",
            "parent_call_id": "agent-1",
            "timestamp": 4.0,
            "tool_name": "search",
            "arguments": {"q": "paris"},
        },
        {
            "kind": "tool_call_end",
            "mas_class": "ToolCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "tool-1",
            "parent_call_id": "agent-1",
            "timestamp": 5.0,
            "result": "tool result text",
        },
        {
            "kind": "execution_end",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "agent-1",
            "timestamp": 6.0,
            "output": "final answer",
            "status": "success",
        },
    ]

    nodes, edges = extract_graph(normalized)
    session = next(node for node in nodes if node.get("node_type") == "Session")

    adjacency: dict = {}
    for edge in edges:
        if edge["edge_type"] in ("inputTo", "leadsTo"):
            adjacency.setdefault(edge["from_id"], []).append(edge["to_id"])

    initial_state = next(
        e["to_id"]
        for e in edges
        if e["edge_type"] == "hasInitialState" and e["from_id"] == session["id"]
    )
    final_state = next(
        e["to_id"]
        for e in edges
        if e["edge_type"] == "hasFinalState" and e["from_id"] == session["id"]
    )

    reachable = {initial_state}
    frontier = [initial_state]
    while frontier:
        next_frontier = []
        for n in frontier:
            for m in adjacency.get(n, []):
                if m not in reachable:
                    reachable.add(m)
                    next_frontier.append(m)
        frontier = next_frontier

    assert final_state in reachable, (
        f"session hasFinalState {final_state!r} is not reachable from "
        f"hasInitialState {initial_state!r} via (inputTo/leadsTo)+"
    )


def test_edges_reference_nodes_by_their_final_id_not_a_stale_pre_rewrite_id() -> None:
    """extract_graph rewrites every *Call node's id to a human-readable
    canonical form ("agentcall:{run}:{agent}:{callId}") in its identity-
    normalization pass, near the end of the function -- but every edge
    created earlier (contains, hasCall, hasInitialState/hasFinalState,
    realizes, invokesProcessing, ...) was built using the *pre-rewrite* id
    (the raw callId). Without remapping those edges too, every one of them
    becomes dangling: Neo4j push MERGEs nodes by their (new) id but MATCHes
    edge endpoints by the same from_id/to_id field, so a dangling edge can
    never find its node and the relationship is silently never created.
    """
    run_id = "demo_323e4567-e89b-12d3-a456-426614174000"
    normalized = [
        {
            "kind": "execution_start",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "agent-1",
            "timestamp": 1.0,
            "input": "plan a trip",
        },
        {
            "kind": "llm_call_start",
            "mas_class": "LLMCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "llm-1",
            "parent_call_id": "agent-1",
            "timestamp": 2.0,
            "prompt": "prompt text",
        },
        {
            "kind": "llm_call_end",
            "mas_class": "LLMCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "llm-1",
            "parent_call_id": "agent-1",
            "timestamp": 3.0,
            "completion": "completion text",
        },
        {
            "kind": "execution_end",
            "mas_class": "AgentCall",
            "run_id": run_id,
            "agent_id": "planner",
            "call_id": "agent-1",
            "timestamp": 6.0,
            "output": "final answer",
            "status": "success",
        },
    ]

    nodes, edges = extract_graph(normalized, all_attributes=True)

    # The rewrite actually happened -- a node's final id must differ from
    # its raw callId, otherwise this test would trivially pass either way.
    agent_node = next(n for n in nodes if n.get("node_type") == "AgentCall")
    assert agent_node["id"] != agent_node["callId"]

    node_ids = {n["id"] for n in nodes}
    dangling = [
        e for e in edges if e.get("from_id") not in node_ids or e.get("to_id") not in node_ids
    ]
    assert not dangling, f"edges referencing a stale pre-rewrite id: {dangling}"

# SPDX-License-Identifier: Apache-2.0
"""Ontology alignment gate — kind map must not target absent OWL classes."""

from __future__ import annotations

from mas.library.kg.core.event_mappings import CHECK_INTERVALS, KIND_TO_CLASS
from mas.library.kg.core.graph_builder import extract_graph
from mas.library.kg.core.verifier import KNOWN_NODE_TYPES


def test_skill_execution_maps_to_skill_call() -> None:
    assert KIND_TO_CLASS["skill_execution_start"] == "SkillCall"
    assert KIND_TO_CLASS["skill_execution_end"] == "SkillCall"
    assert KNOWN_NODE_TYPES, "the SCOPE ontology did not load"
    assert "SkillCall" in KNOWN_NODE_TYPES


def test_governance_kinds_map_to_governance_event() -> None:
    gov_kinds = [
        k
        for k in KIND_TO_CLASS
        if (k.startswith("governance") and not k.endswith(("_start", "_end")))
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


def test_check_intervals_are_processing_calls() -> None:
    """The timing brackets of a check are work: ProcessingCalls, not governance points."""
    assert CHECK_INTERVALS == {
        "governance_authorize",
        "governance_validate",
        "obs_wrap_gov_authorize",
        "obs_wrap_gov_validate",
        "checkpoint",
    }
    for name in CHECK_INTERVALS:
        assert KIND_TO_CLASS[f"{name}_start"] == KIND_TO_CLASS[f"{name}_end"] == "ProcessingCall"


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


def _chain_events(run_id: str) -> list[dict]:
    """One visit whose LLM and tool calls pass the State on, and the MAS call around it."""
    base = {"run_id": run_id, "agent_id": "planner"}
    return [
        {
            **base,
            "kind": "mas_call_start",
            "call_id": "mc",
            "timestamp": 0.5,
            "mas_class": "MASCall",
        },
        {
            **base,
            "kind": "execution_start",
            "mas_class": "AgentCall",
            "call_id": "agent-1",
            "parent_call_id": "mc",
            "timestamp": 1.0,
            "input": "plan a trip",
            "state_out": "a#0",
            "state_out_content": "plan a trip",
        },
        {
            **base,
            "kind": "llm_call_start",
            "mas_class": "LLMCall",
            "call_id": "llm-1",
            "parent_call_id": "agent-1",
            "timestamp": 2.0,
            "state_in": "a#0",
            "state_in_content": "plan a trip",
            "state_out": "a#1",
        },
        {
            **base,
            "kind": "llm_call_end",
            "mas_class": "LLMCall",
            "call_id": "llm-1",
            "parent_call_id": "agent-1",
            "timestamp": 3.0,
            "state_in": "a#0",
            "state_out": "a#1",
            "state_out_content": "completion text",
        },
        {
            **base,
            "kind": "tool_call_start",
            "mas_class": "ToolCall",
            "call_id": "tool-1",
            "parent_call_id": "agent-1",
            "timestamp": 4.0,
            "tool_name": "search",
            "state_in": "a#1",
            "state_in_content": "completion text",
            "state_out": "a#2",
        },
        {
            **base,
            "kind": "tool_call_end",
            "mas_class": "ToolCall",
            "call_id": "tool-1",
            "parent_call_id": "agent-1",
            "timestamp": 5.0,
            "tool_name": "search",
            "state_in": "a#1",
            "state_out": "a#2",
            "state_out_content": "tool result text",
        },
        {
            **base,
            "kind": "execution_end",
            "mas_class": "AgentCall",
            "call_id": "agent-1",
            "parent_call_id": "agent-1",
            "timestamp": 6.0,
            "status": "success",
            "state_in": "a#2",
            "state_in_content": "tool result text",
            "state_out": "a#2",
        },
        {
            **base,
            "kind": "mas_call_end",
            "mas_class": "MASCall",
            "call_id": "mc",
            "timestamp": 7.0,
            "state_in": "a#0",
            "state_in_content": "plan a trip",
            "state_out": "a#2",
            "state_out_content": "tool result text",
        },
    ]


def test_session_boundary_states_are_the_ones_the_mas_call_end_names() -> None:
    run_id = "demo_123e4567-e89b-12d3-a456-426614174000"
    nodes, edges = extract_graph(_chain_events(run_id), all_attributes=True)
    session = next(node for node in nodes if node.get("node_type") == "Session")
    mas_call = next(node for node in nodes if node.get("node_type") == "MASCall")
    initial = f"state:{run_id}:a#0"
    final = f"state:{run_id}:a#2"
    for owner in (session, mas_call):
        assert (
            sum(
                1
                for e in edges
                if e["edge_type"] == "hasInitialState"
                and e["from_id"] == owner["id"]
                and e["to_id"] == initial
            )
            == 1
        )
        assert any(
            e["edge_type"] == "hasFinalState"
            and e["from_id"] == owner["id"]
            and e["to_id"] == final
            for e in edges
        )
    transitions = {n["id"] for n in nodes if n.get("node_type") == "Transition"}
    assert any(
        e["edge_type"] == "inputTo" and e["from_id"] == initial and e["to_id"] in transitions
        for e in edges
    )
    assert any(
        e["edge_type"] == "leadsTo" and e["to_id"] == final and e["from_id"] in transitions
        for e in edges
    )


def test_session_anchors_stay_connected_when_top_call_has_children() -> None:
    """Session hasInitialState/hasFinalState land on States reachable from each other via
    an (inputTo/leadsTo)+ path -- what SessionShape's connectivity check requires. The
    path follows the State ids the records name: the visit's wrapper Transition and its
    children's Transitions all read and write ids of one chain."""
    run_id = "demo_223e4567-e89b-12d3-a456-426614174000"
    nodes, edges = extract_graph(_chain_events(run_id))
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

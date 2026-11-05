#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regression tests for individual native event handlers.

These target bugs found in review that an end-to-end fixture test wouldn't
reliably catch (a duplicate edge, a lookup-key mismatch) by asserting on the
exact node/edge counts a small, hand-built event sequence should produce.
"""

from __future__ import annotations

import json
from pathlib import Path

from mas.library.kg.pipeline import build_kg_document, load_events_jsonl, normalize


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _normalize_full(path: Path):
    events = load_events_jsonl(path)
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    return doc["nodes"], doc["edges"]


def test_delegate_to_tool_call_is_rewritten_without_calls_agent(tmp_path: Path) -> None:
    """A delegate_to_<agent> tool is not a ToolCall. callsAgent stays off
    until A2A observability lands."""
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c2",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "tool_name": "delegate_to_researcher",
            "args": {},
        },
        {
            "kind": "tool_call_end",
            "timestamp": 3.0,
            "run_id": "r1",
            "call_id": "c2",
            "agent_id": "planner",
            "tool_name": "delegate_to_researcher",
            "status": "success",
            "result": {},
        },
        {
            "kind": "execution_end",
            "timestamp": 4.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "success",
        },
    ]
    path = _write_events(tmp_path, events)
    _nodes, edges = normalize(str(path))
    assert [e for e in edges if e.get("edge_type") == "callsAgent"] == []
    assert not any(
        n.get("node_type") == "ToolCall"
        and str(n.get("toolName") or n.get("name") or "").startswith("delegate_to_")
        for n in _nodes
    )


def test_user_tool_named_send_is_not_a_handoff(tmp_path: Path) -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c2",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "tool_name": "send",
            "arguments": {"target": "worker", "message": "hi"},
        },
        {
            "kind": "tool_call_end",
            "timestamp": 3.0,
            "run_id": "r1",
            "call_id": "c2",
            "agent_id": "planner",
            "tool_name": "send",
            "status": "success",
        },
        {
            "kind": "execution_end",
            "timestamp": 4.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "success",
        },
    ]
    _nodes, edges = normalize(str(_write_events(tmp_path, events)))
    assert [e for e in edges if e.get("edge_type") == "callsAgent"] == []
    assert any(n.get("node_type") == "ToolCall" and n.get("name") == "send" for n in _nodes)


def test_tool_with_target_argument_is_not_a_handoff(tmp_path: Path) -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c2",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "tool_name": "book_flight",
            "arguments": {"target": "paris"},
        },
        {
            "kind": "tool_call_end",
            "timestamp": 3.0,
            "run_id": "r1",
            "call_id": "c2",
            "agent_id": "planner",
            "tool_name": "book_flight",
            "status": "success",
        },
        {
            "kind": "execution_end",
            "timestamp": 4.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "success",
        },
    ]
    _nodes, edges = normalize(str(_write_events(tmp_path, events)))
    assert [e for e in edges if e.get("edge_type") == "callsAgent"] == []


def test_recorded_agent_comm_semantics_are_a_handoff(tmp_path: Path) -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c2",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "tool_name": "send",
            "arguments": {"target": "worker", "message": "hi"},
            "semantics": {"concept": "agent_comm", "op": "send", "subject_arg": "target"},
        },
        {
            "kind": "tool_call_end",
            "timestamp": 3.0,
            "run_id": "r1",
            "call_id": "c2",
            "agent_id": "planner",
            "tool_name": "send",
            "status": "success",
        },
        {
            "kind": "execution_end",
            "timestamp": 4.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "success",
        },
    ]
    _nodes, edges = normalize(str(_write_events(tmp_path, events)))
    assert [e for e in edges if e.get("edge_type") == "callsAgent"] == []
    assert not any(n.get("node_type") == "ToolCall" and n.get("name") == "send" for n in _nodes)


def test_session_trajectory_anchor_excludes_nested_agent_call(tmp_path: Path) -> None:
    """A session's hasInitialState/hasFinalState anchors must be derived
    from top-level transitions only. A nested AgentCall (one with a real
    parentCallId, delegated to from another agent) must not be mistaken
    for top-level just because its call_nodes entry is keyed by a compound
    "<call_id>::<agent_id>" merge key while its Transition's realizesCallId
    only carries the plain call_id."""
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "parent-call",
            "agent_id": "planner",
            "input": "start",
        },
        {
            "kind": "execution_start",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "nested-call",
            "parent_call_id": "parent-call",
            "agent_id": "researcher",
            "input": "nested",
        },
        {
            "kind": "execution_end",
            "timestamp": 3.0,
            "run_id": "r1",
            "call_id": "nested-call",
            "agent_id": "researcher",
            "status": "success",
            "output": "nested done",
        },
        {
            "kind": "execution_end",
            "timestamp": 4.0,
            "run_id": "r1",
            "call_id": "parent-call",
            "agent_id": "planner",
            "status": "success",
            "output": "done",
        },
    ]
    path = _write_events(tmp_path, events)
    nodes, edges = _normalize_full(path)

    session_node = next(n for n in nodes if n.get("node_type") == "Session")

    def _session_anchor(edge_type: str) -> str:
        matches = [
            e
            for e in edges
            if e.get("edge_type") == edge_type and e.get("from_id") == session_node["id"]
        ]
        assert len(matches) == 1, matches
        return str(matches[0]["to_id"])

    # The nested AgentCall's own Transition has a *later* transitionTimestamp
    # than the parent's (it starts after and finishes before the parent, but
    # this builder stamps one transitionTimestamp per call at its own start
    # time, so nested's is later here) -- under the bug, both calls' lookups
    # into call_nodes missed (both are AgentCalls, both keyed by the compound
    # "<call_id>::<agent_id>" merge key, neither matched by a plain-call_id
    # lookup), so *both* were wrongly treated as top-level, and the
    # session's hasFinalState anchor was taken from whichever sorted last by
    # timestamp -- the nested call's, not the actual top-level parent's.
    assert _session_anchor("hasInitialState") == "state-agent-parent-call-initial"
    assert _session_anchor("hasFinalState") == "state-agent-parent-call-final"


def test_context_assembly_processing_output_reads_real_messages(tmp_path: Path) -> None:
    """A context_assembly ProcessingCall's own ``messages`` field (which the
    runtime's context_assembly emitter already carries on processing_call_end)
    must be rendered into processingOutput -- not left as the literal
    "assembled" placeholder a prior, now-removed heuristic used to patch by
    reconstructing it from a sibling LLMCall's prompt instead of this same
    event's own data."""
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
        },
        {
            "kind": "processing_call_start",
            "timestamp": 1.1,
            "run_id": "r1",
            "call_id": "pc1",
            "parent_call_id": "c1",
            "agent_id": "planner",
            "processing_name": "context assembly",
            "processing_type": "context_assembly",
        },
        {
            "kind": "processing_call_end",
            "timestamp": 1.2,
            "run_id": "r1",
            "call_id": "pc1",
            "agent_id": "planner",
            "processing_type": "context_assembly",
            "output": "assembled",
            "messages": [
                {"role": "system", "content": "you are a planner"},
                {"role": "user", "content": "plan my trip"},
            ],
        },
        {
            "kind": "execution_end",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "success",
            "output": "done",
        },
    ]
    path = _write_events(tmp_path, events)
    nodes, _edges = _normalize_full(path)

    processing_call = next(n for n in nodes if n.get("node_type") == "ProcessingCall")
    output = processing_call.get("processingOutput", "")
    assert output != "assembled"
    assert "you are a planner" in output
    assert "plan my trip" in output


def test_llm_call_end_reads_top_level_tokens_used(tmp_path: Path) -> None:
    """Runtime traces put usage on the event, not under response.usage."""
    events = [
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
            "finish_reason": "tool_calls",
            "tokens_used": {
                "prompt_tokens": 535,
                "completion_tokens": 27,
                "total_tokens": 562,
            },
            "output": "delegate_to_schedule_agent",
        },
        {
            "kind": "execution_end",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "ok",
            "output": "done",
        },
    ]
    path = _write_events(tmp_path, events)
    nodes, _edges = normalize(str(path))
    llm = next(n for n in nodes if n.get("node_type") == "LLMCall")
    assert llm["promptTokenCount"] == 535
    assert llm["completionTokenCount"] == 27
    assert llm["totalTokenCount"] == 562
    assert llm["finishReason"] == "tool_calls"
    assert "modelName" not in llm
    assert "callId" not in llm
    agent_call = next(n for n in nodes if n.get("node_type") == "AgentCall")
    assert agent_call.get("success") is True

    full_nodes, _ = _normalize_full(path)
    full_llm = next(n for n in full_nodes if n.get("node_type") == "LLMCall")
    assert full_llm["modelName"] == "gpt-4o-mini"
    assert full_llm["provider"] == "azure"
    assert full_llm["callId"] == "llm1"

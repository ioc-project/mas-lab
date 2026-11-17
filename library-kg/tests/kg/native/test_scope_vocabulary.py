#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The native fold emits the SCOPE vocabulary, and only that."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mas.library.kg.core.verifier import KNOWN_EDGE_TYPES, KNOWN_NODE_TYPES
from mas.library.kg.pipeline import build_kg_document

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_ALL_LAYERS = {"categories": "all"}


def _ev(kind: str, call_id: str = "", **fields) -> dict:
    event = {"kind": kind, "run_id": "r", "agent_id": "a", "timestamp": 1.0, **fields}
    if call_id:
        event["call_id"] = call_id
    return event


def _events() -> list[dict]:
    return [
        _ev("execution_start", "exec", input="hi"),
        _ev("memory_store_start", "m1", parent_call_id="exec", memory_type="episodic"),
        _ev("memory_store_end", "m1", parent_call_id="exec", timestamp=1.1),
        _ev(
            "rag_query_start",
            "q1",
            parent_call_id="exec",
            collection="hotels",
            query_text="paris",
            top_k=3,
        ),
        _ev("rag_query_end", "q1", parent_call_id="exec", retrieved_doc_count=2, timestamp=1.2),
        _ev("skill_execution_start", "s1", parent_call_id="exec", skill_name="summarize"),
        _ev("skill_execution_end", "s1", parent_call_id="exec", timestamp=1.3),
        _ev("infrastructure_info", worker_id="w1", worker_pid=7, parent_call_id="exec"),
        _ev(
            "governance_checked",
            decision="deny",
            policy_id="p1",
            reason="no",
            parent_call_id="exec",
        ),
        _ev("routing", parent_call_id="exec", target="b"),
        _ev("execution_end", "exec", timestamp=2.0, output="done"),
    ]


@pytest.fixture(scope="module")
def doc() -> dict:
    return build_kg_document(_events(), run_id="r", **_ALL_LAYERS)


def _nodes(doc: dict, ntype: str) -> list[dict]:
    return [n for n in doc["nodes"] if n["node_type"] == ntype]


def _edge_pairs(doc: dict, etype: str) -> set[tuple[str, str]]:
    return {(e["from_id"], e["to_id"]) for e in doc["edges"] if e["edge_type"] == etype}


@pytest.mark.scope_next
def test_memory_calls_execute_a_memory_and_are_contained_by_the_agent_call(doc) -> None:
    (agent_call,) = _nodes(doc, "AgentCall")
    (rag,) = _nodes(doc, "RAGQuery")
    (store,) = _nodes(doc, "MemoryCall")
    assert (store["memoryOperation"], rag["memoryOperation"]) == ("store", "retrieve")
    assert (rag["memoryQuery"], rag["resultCount"], rag["topK"]) == ("paris", 2, 3)
    assert _edge_pairs(doc, "hasMemoryCall") == {
        (agent_call["id"], store["id"]),
        (agent_call["id"], rag["id"]),
    }
    memories = {n["name"]: n["id"] for n in _nodes(doc, "Memory")}
    assert set(memories) == {"episodic", "hotels"}
    assert _edge_pairs(doc, "executesMemory") == {
        (store["id"], memories["episodic"]),
        (rag["id"], memories["hotels"]),
    }


def test_a_skill_call_executes_a_skill(doc) -> None:
    (call,) = _nodes(doc, "SkillCall")
    (skill,) = _nodes(doc, "Skill")
    assert skill["name"] == "summarize"
    assert _edge_pairs(doc, "executesSkill") == {(call["id"], skill["id"])}
    (agent_call,) = _nodes(doc, "AgentCall")
    assert _edge_pairs(doc, "hasSkillCall") == {(agent_call["id"], call["id"])}


def test_a_worker_runs_the_execution_it_sits_under(doc) -> None:
    (worker,) = _nodes(doc, "Worker")
    (agent_call,) = _nodes(doc, "AgentCall")
    assert (worker["processId"], worker["name"]) == ("7", "w1")
    assert _edge_pairs(doc, "runsOn") == {(agent_call["id"], worker["id"])}


def test_annotations_carry_event_kind_and_time_and_annotate_their_call(doc) -> None:
    (agent_call,) = _nodes(doc, "AgentCall")
    (governance,) = _nodes(doc, "GovernanceEvent")
    assert (governance["decision"], governance["policyName"], governance["decisionReason"]) == (
        "deny",
        "p1",
        "no",
    )
    assert governance["eventKind"] == "governance_checked"
    (routing,) = [n for n in _nodes(doc, "CallAnnotation") if n["eventKind"] == "routing"]
    assert json.loads(routing["payload"]) == {"target": "b"}
    assert routing["eventTime"] == 1.0
    annotates = _edge_pairs(doc, "annotates")
    assert {(governance["id"], agent_call["id"]), (routing["id"], agent_call["id"])} <= annotates


def test_parallel_group_markers_add_nothing() -> None:
    events = [
        _ev("execution_start", "exec"),
        _ev("parallel_group_start", "g1", parent_call_id="exec"),
        _ev("parallel_group_end", "g1", parent_call_id="exec"),
        _ev("execution_end", "exec", timestamp=2.0),
    ]
    doc = build_kg_document(events, run_id="r", **_ALL_LAYERS)
    assert {n["node_type"] for n in doc["nodes"]} == {"AgentCall", "Agent", "Session"}


def test_no_thinking_call_without_a_thinking_event(doc) -> None:
    assert not _nodes(doc, "ThinkingCall")


@pytest.mark.parametrize(
    "trace", ["events_full_ontology.jsonl", "path_equivalence/explicit-events.jsonl.gz"]
)
@pytest.mark.scope_next
def test_every_node_and_edge_type_is_declared_by_scope(trace: str) -> None:
    path = _FIXTURES / trace
    opener = gzip.open if trace.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    result = build_kg_document(events, run_id="r", **_ALL_LAYERS)
    assert {n["node_type"] for n in result["nodes"]} <= KNOWN_NODE_TYPES
    assert {e["edge_type"] for e in result["edges"]} <= KNOWN_EDGE_TYPES

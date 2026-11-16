#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The native fold is a plain mapping of explicit signals.

Each event maps to nodes and edges by itself: the graph does not depend on the order of
the events, the realtime fold is the batch fold, States and Transitions come only from
the ``state_in`` / ``state_out`` fields, and containment is what the records name.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mas.library.kg.pipeline import build_kg_document

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "path_equivalence"
    / "explicit-events.jsonl.gz"
)
_LEGACY_EDGES = {"contains", "hasCall", "realizes", "fromState", "toState"}


@pytest.fixture(scope="module")
def events() -> List[Dict[str, Any]]:
    with gzip.open(_FIXTURE, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _doc(events: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("all_attributes", True)
    return build_kg_document(events, run_id="r", **kwargs)


def _canonical(doc: Dict[str, Any]) -> str:
    """Digest of the nodes and edges as canonical, sorted JSON (a failing assert stays cheap)."""
    nodes = sorted(json.dumps(n, sort_keys=True, default=str) for n in doc["nodes"])
    edges = sorted(json.dumps(e, sort_keys=True, default=str) for e in doc["edges"])
    return hashlib.sha256(json.dumps([nodes, edges]).encode()).hexdigest()


def _nodes(doc: Dict[str, Any], ntype: str) -> List[Dict[str, Any]]:
    return [n for n in doc["nodes"] if n["node_type"] == ntype]


def _edges(doc: Dict[str, Any], etype: str) -> List[Dict[str, Any]]:
    return [e for e in doc["edges"] if e["edge_type"] == etype]


# -- 1, 2: order-agnostic and realtime ------------------------------------------------


def test_permuting_the_events_does_not_change_the_graph(events) -> None:
    reference = _canonical(_doc(events))
    for seed in range(5):
        shuffled = list(events)
        random.Random(seed).shuffle(shuffled)
        assert _canonical(_doc(shuffled)) == reference, f"seed {seed}"
    assert _canonical(_doc(list(reversed(events)))) == reference


def test_the_realtime_fold_is_the_batch_fold(events) -> None:
    batch = _doc(events)
    live = _doc(events, realtime=True)
    assert live["metadata"]["realtime"] is True
    assert _canonical(live) == _canonical(batch)


def test_process_event_returns_what_the_event_contributes() -> None:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder

    builder = NativeGraphBuilder(run_id="r")
    nodes, edges = builder.process_event(
        {
            "kind": "tool_call_end",
            "run_id": "r",
            "agent_id": "a",
            "call_id": "t",
            "parent_call_id": "e",
            "tool_name": "search",
            "timestamp": 2.0,
            "state_in": "e#1",
            "state_in_content": "q",
            "state_out": "e#2",
            "state_out_content": "answer",
        }
    )
    ids = {n["id"] for n in nodes}
    assert {"toolcall:r:t", "state:r:e#1", "state:r:e#2", "trans:toolcall:r:t"} <= ids
    kinds = {e["edge_type"] for e in edges}
    assert {"hasToolCall", "hasInitialState", "hasFinalState", "inputTo", "leadsTo"} <= kinds
    # The parent never arrived: its edge is dropped only when the graph is finalized.
    final_nodes, final_edges = builder.finalize()
    assert not [e for e in final_edges if e["edge_type"] == "hasToolCall"]
    assert {n["node_type"] for n in final_nodes} >= {"ToolCall", "State", "Transition", "Tool"}


# -- 3: states --------------------------------------------------------------------------


def test_every_state_id_a_record_names_is_a_state_with_its_producers_content(events) -> None:
    doc = _doc(events)
    states = {n["id"]: n for n in _nodes(doc, "State")}
    expected: Dict[str, str] = {}
    for event in events:
        in_raw, out_raw = event.get("state_in"), event.get("state_out")
        if isinstance(in_raw, list):
            contents = json.loads(event.get("state_in_content") or "[]")
            for sid, content in zip(in_raw, contents):
                expected.setdefault(sid, "")
                expected[sid] = expected[sid] or content
        elif in_raw:
            expected.setdefault(in_raw, "")
            expected[in_raw] = expected[in_raw] or event.get("state_in_content") or ""
        if out_raw:
            expected.setdefault(out_raw, "")
            expected[out_raw] = expected[out_raw] or event.get("state_out_content") or ""
    assert expected
    for sid, content in expected.items():
        node = states[f"state:r:{sid}"]
        assert node["content"] == content or (not content and node["content"] == "")
    assert len(states) == len(expected)


# -- 4: names and dangling edges ----------------------------------------------------------


def test_no_legacy_edge_names_and_no_dangling_edge(events) -> None:
    doc = _doc(events, categories="all")
    assert not {e["edge_type"] for e in doc["edges"]} & _LEGACY_EDGES
    ids = {n["id"] for n in doc["nodes"]}
    assert all(e["from_id"] in ids and e["to_id"] in ids for e in doc["edges"])


# -- 5: containment -------------------------------------------------------------------------


def test_containment_is_explicit(events) -> None:
    doc = _doc(events)
    (mas_call,) = _nodes(doc, "MASCall")
    agent_calls = _nodes(doc, "AgentCall")
    assert len(agent_calls) == 8
    for call in agent_calls:
        assert (
            sum(
                1
                for e in _edges(doc, "belongsToMASCall")
                if e["from_id"] == call["id"] and e["to_id"] == mas_call["id"]
            )
            == 1
        )
        assert (
            sum(
                1
                for e in _edges(doc, "hasAgentCall")
                if e["to_id"] == call["id"] and e["from_id"] == mas_call["id"]
            )
            == 1
        )
    agent_ids = {c["id"] for c in agent_calls}
    for ntype, edge_type in (("ToolCall", "hasToolCall"), ("LLMCall", "hasLLMCall")):
        for node in _nodes(doc, ntype):
            parents = [e["from_id"] for e in _edges(doc, edge_type) if e["to_id"] == node["id"]]
            assert len(parents) == 1 and parents[0] in agent_ids


# -- 6: hand-built chains --------------------------------------------------------------------


def _ev(kind: str, ts: float, agent: str, call: str, **rest: Any) -> Dict[str, Any]:
    return {
        "kind": kind,
        "timestamp": ts,
        "run_id": "r",
        "agent_id": agent,
        "call_id": call,
        **rest,
    }


def _chain() -> List[Dict[str, Any]]:
    return [
        _ev("mas_call_start", 0.5, "mas", "mc", mas_id="m"),
        _ev(
            "execution_start",
            1.0,
            "a",
            "e",
            parent_call_id="mc",
            state_out="e#0",
            state_out_content="hi",
            mas_id="m",
        ),
        _ev(
            "llm_call_start",
            2.0,
            "a",
            "l",
            parent_call_id="e",
            model="p/x",
            state_in="e#0",
            state_in_content="hi",
            state_out="e#1",
        ),
        _ev(
            "llm_call_end",
            3.0,
            "a",
            "l",
            parent_call_id="e",
            state_in="e#0",
            state_out="e#1",
            state_out_content="tool please",
        ),
        _ev(
            "execution_end",
            4.0,
            "a",
            "e",
            status="ok",
            state_in="e#1",
            state_in_content="tool please",
            state_out="e#1",
        ),
        _ev(
            "mas_call_end",
            5.0,
            "mas",
            "mc",
            status="ok",
            mas_id="m",
            state_in="e#0",
            state_in_content="hi",
            state_out="e#1",
            state_out_content="tool please",
        ),
    ]


def test_a_single_agent_chain() -> None:
    doc = _doc(_chain())
    contents = {n["id"]: n["content"] for n in _nodes(doc, "State")}
    assert contents == {"state:r:e#0": "hi", "state:r:e#1": "tool please"}
    llm = next(n for n in _nodes(doc, "LLMCall"))
    visit = next(n for n in _nodes(doc, "AgentCall"))
    triples = {(e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"]}
    assert ("hasInitialState", llm["id"], "state:r:e#0") in triples
    assert ("hasFinalState", llm["id"], "state:r:e#1") in triples
    assert ("hasInitialState", visit["id"], "state:r:e#0") in triples
    assert ("hasFinalState", visit["id"], "state:r:e#1") in triples
    t_llm = f"trans:{llm['id']}"
    assert ("inputTo", "state:r:e#0", t_llm) in triples
    assert ("leadsTo", t_llm, "state:r:e#1") in triples
    assert ("correspondsToTransition", llm["id"], t_llm) in triples
    assert ("representsExecution", t_llm, llm["id"]) in triples
    # Session and MASCall share the boundary States.
    session = next(n for n in _nodes(doc, "Session"))
    mas_call = next(n for n in _nodes(doc, "MASCall"))
    for owner in (session, mas_call):
        assert ("hasInitialState", owner["id"], "state:r:e#0") in triples
        assert ("hasFinalState", owner["id"], "state:r:e#1") in triples
        assert ("hasState", owner["id"], "state:r:e#0") in triples
    assert ("hasMASCall", session["id"], mas_call["id"]) in triples
    assert len(_nodes(doc, "Transition")) == 4  # visit, LLM call, MAS call, Session


def test_a_join_has_one_input_edge_per_member() -> None:
    events = [
        _ev("execution_start", 1.0, "a", "e", state_out="e#0", state_out_content="go"),
        _ev(
            "processing_call_start",
            2.0,
            "a",
            "j",
            parent_call_id="e",
            processing_name="aggregate",
            processing_type="parallel_group",
            state_in=["e#1", "e#2"],
            state_in_content='["one","two"]',
            state_out="e#3",
            state_out_content='["one","two"]',
        ),
        _ev(
            "processing_call_end",
            2.0,
            "a",
            "j",
            parent_call_id="e",
            processing_name="aggregate",
            processing_type="parallel_group",
            state_in=["e#1", "e#2"],
            state_in_content='["one","two"]',
            state_out="e#3",
            state_out_content='["one","two"]',
        ),
    ]
    doc = _doc(events)
    join = next(n for n in _nodes(doc, "ProcessingCall"))
    t = f"trans:{join['id']}"
    triples = {(e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"]}
    for sid in ("e#1", "e#2"):
        assert ("inputTo", f"state:r:{sid}", t) in triples
        assert ("hasInitialState", join["id"], f"state:r:{sid}") in triples
    assert ("leadsTo", t, "state:r:e#3") in triples
    contents = {n["id"]: n["content"] for n in _nodes(doc, "State")}
    assert contents["state:r:e#1"] == "one" and contents["state:r:e#2"] == "two"


def test_a_delegation_hands_the_states_through_the_boundary_steps() -> None:
    events = [
        _ev("execution_start", 1.0, "boss", "be", state_out="b#0", state_out_content="plan"),
        _ev(
            "tool_call_start",
            2.0,
            "boss",
            "tc",
            parent_call_id="be",
            tool_name="delegate_to_helper",
            state_in="b#1",
            state_in_content="call",
            state_out="b#2",
        ),
        _ev(
            "processing_call_end",
            2.1,
            "boss",
            "tc:delegate",
            parent_call_id="be",
            processing_name="delegate",
            processing_type="agent_boundary",
            state_in="b#1",
            state_in_content="call",
            state_out="h#0",
            state_out_content="task",
        ),
        _ev(
            "execution_start",
            2.2,
            "helper",
            "he",
            parent_call_id="tc",
            state_out="h#0",
            state_out_content="task",
        ),
        _ev(
            "execution_end",
            3.0,
            "helper",
            "he",
            status="ok",
            state_in="h#1",
            state_in_content="done",
            state_out="h#1",
        ),
        _ev(
            "processing_call_end",
            3.1,
            "boss",
            "tc:reply",
            parent_call_id="be",
            processing_name="reply",
            processing_type="agent_boundary",
            state_in="h#1",
            state_in_content="done",
            state_out="b#2",
            state_out_content="done",
        ),
        _ev(
            "tool_call_end",
            3.2,
            "boss",
            "tc",
            parent_call_id="be",
            tool_name="delegate_to_helper",
            state_in="b#1",
            state_out="b#2",
            state_out_content="done",
        ),
        _ev("execution_end", 4.0, "boss", "be", status="ok", state_in="b#2", state_out="b#2"),
    ]
    doc = _doc(events)
    triples = {(e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"]}
    by_call = {n["callId"]: n for n in doc["nodes"] if n.get("callId")}
    tool = by_call["tc"]
    callee = by_call["he"]
    delegate = by_call["tc:delegate"]
    reply = by_call["tc:reply"]
    # The delegate step writes the callee's first State; the reply step writes the tool's output.
    assert ("hasFinalState", delegate["id"], "state:r:h#0") in triples
    assert ("hasInitialState", callee["id"], "state:r:h#0") in triples
    assert ("hasFinalState", reply["id"], "state:r:b#2") in triples
    assert ("hasFinalState", tool["id"], "state:r:b#2") in triples
    assert ("hasInitialState", reply["id"], "state:r:h#1") in triples
    assert ("hasFinalState", callee["id"], "state:r:h#1") in triples
    # The delegating tool is an ordinary tool call of the caller; the callee an ordinary visit.
    assert tool["node_type"] == "ToolCall" and callee["node_type"] == "AgentCall"
    assert ("hasToolCall", by_call["be"]["id"], tool["id"]) in triples
    assert ("hasProcessingCall", by_call["be"]["id"], delegate["id"]) in triples
    assert ("hasProcessingCall", by_call["be"]["id"], reply["id"]) in triples


def test_a_record_without_state_fields_adds_no_state_and_no_transition() -> None:
    events = [
        _ev("execution_start", 1.0, "a", "e"),
        _ev("tool_call_start", 2.0, "a", "t", parent_call_id="e", tool_name="x"),
        _ev("tool_call_end", 3.0, "a", "t", parent_call_id="e", tool_name="x", output="o"),
        _ev("execution_end", 4.0, "a", "e", status="ok"),
    ]
    doc = _doc(events)
    assert not _nodes(doc, "State") and not _nodes(doc, "Transition")


def test_two_different_contents_for_one_state_keep_the_smaller_and_warn(caplog) -> None:
    import logging

    events = [
        _ev("tool_call_end", 1.0, "a", "t1", state_in="s#0", state_in_content="beta"),
        _ev("tool_call_end", 2.0, "a", "t2", state_in="s#0", state_in_content="alpha"),
        _ev("tool_call_end", 3.0, "a", "t3", state_in="s#0", state_in_content=""),
    ]
    with caplog.at_level(logging.WARNING):
        doc = _doc(events)
        flipped = _doc(list(reversed(events)))
    (state,) = _nodes(doc, "State")
    assert state["content"] == "alpha"
    assert _canonical(doc) == _canonical(flipped)
    assert "two different contents" in caplog.text


def test_an_unfinished_call_keeps_no_end_time_and_an_edge_may_name_a_later_node() -> None:
    events = [
        _ev("llm_call_start", 2.0, "a", "l", parent_call_id="e", model="m"),
        _ev("execution_start", 1.0, "a", "e", state_out="e#0", state_out_content="x"),
    ]
    doc = _doc(events)
    llm = _nodes(doc, "LLMCall")[0]
    assert "endTime" not in llm
    assert any(e["edge_type"] == "hasLLMCall" and e["to_id"] == llm["id"] for e in doc["edges"])


# -- 7: SHACL ---------------------------------------------------------------------------------


def test_shacl_categories_on_the_fixture(events, capsys) -> None:
    from norm import verifier

    doc = _doc(events, all_attributes=False)
    report = verifier.verify_kg(doc["nodes"], doc["edges"], "r")
    assert set(report) >= {"structural_checks", "shacl"}
    # No unknown node or edge type, no edge outside its domain and range, no dangling edge.
    for name, check in report["structural_checks"].items():
        assert check["ok"], (name, check["violations"][:3])


# -- 8: placeholder ----------------------------------------------------------------------------


def test_the_placeholder_text_appears_nowhere(events) -> None:
    doc = _doc(events, categories="all")
    assert "(no content recorded)" not in json.dumps(doc, default=str)

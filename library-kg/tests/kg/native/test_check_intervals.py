#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Check intervals, interval annotations and the linkage of every node.

The timing brackets of a check (``governance_authorize_*``, ``governance_validate_*``,
``obs_wrap_gov_*``, ``checkpoint_*``) are ProcessingCalls named by their kind and contained
by the call they bracket. The ``call_id`` of any interval pair is its own key and no node,
so an annotation interval (``state_update_*``, ``wait_state_*``) ``annotates`` its
``parent_call_id``. Every node of the graph is linked, whatever the categories.
"""

from __future__ import annotations

import collections
import gzip
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mas.library.kg.core.event_mappings import CHECK_INTERVALS
from mas.library.kg.pipeline import build_kg_document

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "path_equivalence"
_DATAFLOW = {
    "hasState",
    "hasInitialState",
    "hasFinalState",
    "inputTo",
    "leadsTo",
    "correspondsToTransition",
    "representsExecution",
}
_ANNOTATIONS = {"CallAnnotation", "GovernanceEvent", "ContextContribution"}


def _load(name: str) -> List[Dict[str, Any]]:
    with gzip.open(_FIXTURES / name, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _unlinked(doc: Dict[str, Any]) -> Dict[str, int]:
    """Per node type, the nodes with no containment, ownership or ``annotates`` edge."""
    incoming: Dict[str, set] = collections.defaultdict(set)
    outgoing: Dict[str, set] = collections.defaultdict(set)
    for edge in doc["edges"]:
        outgoing[edge["from_id"]].add(edge["edge_type"])
        incoming[edge["to_id"]].add(edge["edge_type"])
    missing: collections.Counter = collections.Counter()
    for node in doc["nodes"]:
        kind, node_id = node["node_type"], node["id"]
        if kind in ("Session", "MASCall"):
            continue
        if kind in _ANNOTATIONS:
            linked = "annotates" in outgoing[node_id]
        elif kind.endswith("Call") or kind == "RAGQuery":
            linked = (
                any(
                    e.startswith(("has", "contains")) and e not in _DATAFLOW
                    for e in incoming[node_id]
                )
                or "belongsToMASCall" in outgoing[node_id]
            )
        elif kind == "Worker":
            linked = "runsOn" in incoming[node_id]
        else:
            linked = bool(incoming[node_id] | outgoing[node_id])
        if not linked:
            missing[kind] += 1
    return dict(missing)


@pytest.mark.parametrize("categories", [None, "all"], ids=["default", "all"])
@pytest.mark.parametrize(
    "trace",
    ["explicit-events.jsonl.gz", "delegation-events.jsonl.gz", "categories-events.jsonl.gz"],
)
def test_every_node_is_linked(trace: str, categories: Any) -> None:
    doc = build_kg_document(_load(trace), run_id="r1", categories=categories)
    assert _unlinked(doc) == {}


def _llm_run(**stated: Any) -> List[Dict[str, Any]]:
    """One agent visit, one LLM call, a governance check around it."""
    base = {"agent_id": "a", "run_id": "r", "session_id": "s"}

    def event(kind: str, t: float, **fields: Any) -> Dict[str, Any]:
        return {"kind": kind, "timestamp": t, **base, **fields}

    return [
        event("mas_call_start", 0.0, call_id="mas"),
        event("execution_start", 1.0, call_id="exec", parent_call_id="mas", state_out="exec#0"),
        event("llm_call_start", 2.0, call_id="llm", parent_call_id="exec", model="openai/m"),
        event("governance_authorize_start", 2.1, call_id="gate", parent_call_id="llm"),
        event("governance_authorize_end", 2.2, call_id="gate", parent_call_id="llm", **stated),
        event("state_update_start", 2.3, call_id="upd", parent_call_id="llm"),
        event("state_update_end", 2.3, call_id="upd", parent_call_id="llm"),
        event("llm_call_end", 3.0, call_id="llm", parent_call_id="exec"),
        event("execution_end", 4.0, call_id="exec", parent_call_id="mas", state_in="exec#0"),
        event("mas_call_end", 5.0, call_id="mas"),
    ]


def _by_type(doc: Dict[str, Any], ntype: str) -> List[Dict[str, Any]]:
    return [n for n in doc["nodes"] if n["node_type"] == ntype]


def test_a_check_interval_is_a_processing_call_contained_by_the_call_it_brackets() -> None:
    doc = build_kg_document(_llm_run(), run_id="r", all_attributes=True)
    (gate,) = [n for n in _by_type(doc, "ProcessingCall") if n["callId"] == "gate"]
    assert gate["name"] == "governance_authorize"
    assert gate["processingName"] == "governance_authorize"
    assert (gate["startTime"], gate["endTime"]) == (2.1, 2.2)
    (llm,) = _by_type(doc, "LLMCall")
    edges = {(e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"]}
    assert ("containsCall", llm["id"], gate["id"]) in edges
    (processing,) = _by_type(doc, "Processing")
    assert processing["processingType"] == "governance_authorize"
    assert ("executesProcessing", gate["id"], processing["id"]) in edges


def test_a_check_interval_under_an_agent_visit_uses_has_processing_call() -> None:
    events = _llm_run()
    for event in events:
        if event.get("call_id") == "gate":
            event["parent_call_id"] = "exec"
    doc = build_kg_document(events, run_id="r", all_attributes=True)
    (agent,) = _by_type(doc, "AgentCall")
    (gate,) = [n for n in _by_type(doc, "ProcessingCall") if n["callId"] == "gate"]
    assert ("hasProcessingCall", agent["id"], gate["id"]) in {
        (e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"]
    }


def test_a_check_interval_gets_states_only_when_its_records_carry_them() -> None:
    doc = build_kg_document(_llm_run(), run_id="r")
    (gate,) = [n for n in _by_type(doc, "ProcessingCall") if n["id"].endswith(":gate")]
    assert not any(
        e["edge_type"] in _DATAFLOW and gate["id"] in (e["from_id"], e["to_id"])
        for e in doc["edges"]
    )
    events = _llm_run()
    for event in events:
        if event.get("call_id") == "gate":
            event.update(state_in="exec#0", state_out="gate#0")
    doc = build_kg_document(events, run_id="r")
    assert any(
        e["edge_type"] == "hasFinalState" and e["from_id"] == gate["id"] for e in doc["edges"]
    )


def test_a_governance_point_exists_only_for_a_record_that_states_a_decision() -> None:
    silent = build_kg_document(_llm_run(), run_id="r", categories="annotations")
    assert _by_type(silent, "GovernanceEvent") == []
    stated = build_kg_document(
        _llm_run(decision="ALLOW", policy_name="p"), run_id="r", categories="annotations"
    )
    (point,) = _by_type(stated, "GovernanceEvent")
    assert (point["decision"], point["policyName"]) == ("ALLOW", "p")
    (gate,) = [n for n in _by_type(stated, "ProcessingCall") if n["id"].endswith(":gate")]
    assert any(
        e["edge_type"] == "annotates" and e["from_id"] == point["id"] and e["to_id"] == gate["id"]
        for e in stated["edges"]
    )
    default = build_kg_document(_llm_run(decision="ALLOW"), run_id="r")
    assert _by_type(default, "GovernanceEvent") == []


def test_an_annotation_interval_annotates_its_parent_call_not_its_own_key() -> None:
    doc = build_kg_document(_llm_run(), run_id="r", categories="annotations")
    (llm,) = _by_type(doc, "LLMCall")
    updates = [
        n for n in _by_type(doc, "CallAnnotation") if n["eventKind"].startswith("state_update")
    ]
    assert len(updates) == 2
    targets = {e["to_id"] for e in doc["edges"] if e["from_id"] in {u["id"] for u in updates}}
    assert targets == {llm["id"]}


def test_every_check_interval_kind_maps_to_a_processing_call() -> None:
    events = _llm_run()
    base = {"agent_id": "a", "run_id": "r", "session_id": "s"}
    for index, name in enumerate(sorted(CHECK_INTERVALS)):
        for suffix, offset in (("start", 0.0), ("end", 0.01)):
            events.append(
                {
                    "kind": f"{name}_{suffix}",
                    "timestamp": 2.4 + index / 10 + offset,
                    "call_id": f"x-{name}",
                    "parent_call_id": "exec",
                    **base,
                }
            )
    doc = build_kg_document(events, run_id="r")
    names = {n["name"] for n in _by_type(doc, "ProcessingCall")}
    assert CHECK_INTERVALS <= names
    assert _unlinked(doc) == {}

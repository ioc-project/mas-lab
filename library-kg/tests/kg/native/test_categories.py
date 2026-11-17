"""Tests for the opt-in ``categories`` of the native fold.

The default output holds the core classes only. ``capabilities``, ``annotations``,
``worker`` and ``thinking`` add their classes on top, never change a core node, and the
batch and realtime folds drop the same events. A mock ontology index keeps the
``normalize_events`` tests free of rdflib and the TTL.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from mas.library.kg.core.event_mappings import (
    CATEGORY_CLASSES,
    parse_categories,
    skipped_classes,
)
from mas.library.kg.core.graph_builder import normalize_events
from mas.library.kg.core.scope_models import mas_namespace
from mas.library.kg.pipeline import build_kg_document

_MAS_NS = mas_namespace()


def _make_ontology() -> Any:
    ontology = MagicMock()

    def _get(local_name: str):
        if local_name in ("ExecutionElement", None):
            return None
        return {
            "uri": _MAS_NS + local_name,
            "local_name": local_name,
            "label": local_name,
            "span_level": "call",
            "icon": "",
        }

    ontology.get.side_effect = _get
    return ontology


ONTOLOGY = _make_ontology()


def _event(
    kind: str, agent_id: str = "agent-1", call_id: str = "call-1", **extra: Any
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "agent_id": agent_id,
        "call_id": call_id,
        "timestamp": 0.0,
        "run_id": "test-run",
        **extra,
    }


def _kinds(normalized: List[Dict[str, Any]]) -> List[str]:
    return [e["kind"] for e in normalized]


# -- parse_categories -----------------------------------------------------------------------


def test_default_is_no_category() -> None:
    assert parse_categories(None) == frozenset()
    assert parse_categories("") == frozenset()
    assert skipped_classes(None) == frozenset().union(*CATEGORY_CLASSES.values())


def test_parse_csv_list_and_all() -> None:
    assert parse_categories("capabilities, thinking") == {"capabilities", "thinking"}
    assert parse_categories(["worker"]) == {"worker"}
    assert parse_categories("all") == set(CATEGORY_CLASSES)
    assert skipped_classes("all") == frozenset()


def test_unknown_category_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown categories"):
        parse_categories("capabilities,bogus")


# -- class-level filtering in normalize_events ------------------------------------------------

_CORE = [
    "execution_start",
    "execution_end",
    "tool_call_start",
    "llm_call_end",
    "processing_call_start",
]
_BY_CATEGORY = {
    "capabilities": [
        "rag_query_start",
        "memory_store_end",
        "memory_retrieve_start",
        "skill_execution_end",
    ],
    "annotations": [
        "routing",
        "context_assembled",
        "state_update_start",
        "observability_pre_execute_start",
        "user_input",
        "client_response",
        "audit",
        "policy_denial",
        "governance_checked",
        "context_part_contributed",
    ],
    "worker": ["infrastructure_info"],
    "thinking": ["thinking_start", "thinking_end"],
}


def _all_kinds() -> List[str]:
    return _CORE + [k for kinds in _BY_CATEGORY.values() for k in kinds]


def test_default_keeps_core_kinds_only() -> None:
    events = [_event(k) for k in _all_kinds()]
    assert set(_kinds(normalize_events(events, ONTOLOGY, "run-1"))) == set(_CORE)


@pytest.mark.parametrize("category", sorted(_BY_CATEGORY))
def test_one_category_adds_exactly_its_kinds(category: str) -> None:
    events = [_event(k) for k in _all_kinds()]
    result = normalize_events(events, ONTOLOGY, "run-1", categories=category)
    assert set(_kinds(result)) == set(_CORE) | set(_BY_CATEGORY[category])


def test_all_keeps_every_kind() -> None:
    events = [_event(k) for k in _all_kinds()]
    result = normalize_events(events, ONTOLOGY, "run-1", categories="all")
    assert set(_kinds(result)) == set(_all_kinds())


def test_disabled_category_never_raises_in_strict_mode() -> None:
    # A skill event without call_id is a gap only when the capabilities category is on.
    events = [_event("execution_start"), {"kind": "skill_execution_start", "run_id": "r"}]
    assert len(normalize_events(events, ONTOLOGY, "r", strict=True)) == 1
    with pytest.raises(Exception):
        normalize_events(events, ONTOLOGY, "r", strict=True, categories="capabilities")


# -- the core graph does not depend on the other categories -----------------------------------


def _trace() -> List[Dict[str, Any]]:
    e = lambda kind, **f: {"run_id": "r", "agent_id": "a", "timestamp": 1.0, "kind": kind, **f}  # noqa: E731
    return [
        e("execution_start", call_id="ag", state_out="s0", state_out_content="hi"),
        e(
            "llm_call_start",
            call_id="llm",
            parent_call_id="ag",
            model="openai/gpt-4o",
            state_in="s0",
            state_in_content="hi",
        ),
        e(
            "thinking_start",
            call_id="llm#think",
            parent_call_id="llm",
            model="openai/gpt-4o",
            state_in="s0",
            state_in_content="hi",
        ),
        e(
            "thinking_end",
            call_id="llm#think",
            parent_call_id="llm",
            state_out="t1",
            state_out_content="hmm",
            reasoning_tokens=7,
        ),
        e(
            "llm_call_end",
            call_id="llm",
            parent_call_id="ag",
            state_out="s1",
            state_out_content="ok",
        ),
        e(
            "memory_retrieve_start",
            call_id="m",
            parent_call_id="ag",
            memory_name="notes",
            state_in="s1",
            state_in_content="ok",
        ),
        e(
            "memory_retrieve_end",
            call_id="m",
            parent_call_id="ag",
            state_out="s2",
            state_out_content="n",
        ),
        e("skill_execution_start", call_id="sk", parent_call_id="ag", skill_name="plan"),
        e("skill_execution_end", call_id="sk", parent_call_id="ag"),
        e("rag_query_start", call_id="rq", parent_call_id="ag", collection="docs"),
        e("rag_query_end", call_id="rq", parent_call_id="ag"),
        e("context_assembled"),
        e("audit", call_id="llm"),
        e("context_part_contributed", part_id="p1", llm_call_id="llm"),
        e("infrastructure_info", parent_call_id="ag", worker_id="w1"),
        e("execution_end", call_id="ag", state_in="s1", state_in_content="ok"),
    ]


def _dump(doc: Dict[str, Any]) -> str:
    return json.dumps([doc["nodes"], doc["edges"]], sort_keys=True, default=str)


@pytest.mark.parametrize("realtime", [False, True])
def test_core_graph_is_identical_with_or_without_other_category_events(realtime: bool) -> None:
    full = _trace()
    extra_kinds = {k for kinds in _BY_CATEGORY.values() for k in kinds}
    core_only = [ev for ev in full if ev["kind"] not in extra_kinds]
    with_extra = build_kg_document(full, run_id="r", realtime=realtime)
    without = build_kg_document(core_only, run_id="r", realtime=realtime)
    assert _dump(with_extra) == _dump(without)
    types = {n["node_type"] for n in with_extra["nodes"]}
    assert not types & set().union(*CATEGORY_CLASSES.values())


@pytest.mark.parametrize("realtime", [False, True])
def test_each_category_adds_its_classes(realtime: bool) -> None:
    expected = {
        "capabilities": {"Skill", "SkillCall", "Memory", "MemoryCall", "RAGQuery"},
        "annotations": {"CallAnnotation", "GovernanceEvent", "ContextContribution"},
        "worker": {"Worker"},
        "thinking": {"ThinkingCall"},
    }
    base = {
        n["node_type"] for n in build_kg_document(_trace(), run_id="r", realtime=realtime)["nodes"]
    }
    for category, classes in expected.items():
        doc = build_kg_document(_trace(), run_id="r", categories=category, realtime=realtime)
        types = {n["node_type"] for n in doc["nodes"]}
        assert types - base == classes, category
        assert doc["metadata"]["categories"] == [category]


def test_batch_and_realtime_agree_with_all_categories() -> None:
    batch = build_kg_document(_trace(), run_id="r", categories="all")
    live = build_kg_document(_trace(), run_id="r", categories="all", realtime=True)
    assert _dump(batch) == _dump(live)


# -- thinking mapping --------------------------------------------------------------------------


def test_thinking_call_mapping() -> None:
    doc = build_kg_document(_trace(), run_id="r", categories="thinking", all_attributes=True)
    nodes = {n["id"]: n for n in doc["nodes"]}
    think = next(n for n in doc["nodes"] if n["node_type"] == "ThinkingCall")
    assert think["callId"] == "llm#think"
    assert think["reasoningTokenCount"] == 7
    llm = next(n for n in doc["nodes"] if n["node_type"] == "LLMCall")
    edges = {(e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"]}
    assert ("hasThinkingCall", llm["id"], think["id"]) in edges
    # State identity: input State is the LLM's prompt State, output the reasoning text.
    initial = [
        e["to_id"]
        for e in doc["edges"]
        if e["edge_type"] == "hasInitialState" and e["from_id"] == think["id"]
    ]
    final = [
        e["to_id"]
        for e in doc["edges"]
        if e["edge_type"] == "hasFinalState" and e["from_id"] == think["id"]
    ]
    assert initial == [
        n["id"] for n in doc["nodes"] if n["node_type"] == "State" and n.get("content") == "hi"
    ]
    assert [nodes[i]["content"] for i in final] == ["hmm"]
    assert any(
        e["edge_type"] == "executesLLM" and e["from_id"] == think["id"] for e in doc["edges"]
    )
    assert any(
        e["edge_type"] == "correspondsToTransition" and e["from_id"] == think["id"]
        for e in doc["edges"]
    )

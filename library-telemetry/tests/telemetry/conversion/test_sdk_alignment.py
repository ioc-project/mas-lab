#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observe-sdk profile spans carry the attributes SCOPE norm reads.

One test per attribute rule of norm's execution-extension contract
(docs/ingestion/normalization: span names, ``ioa_observe.agent.span_id``,
memory vocabulary, annotations, governance, reasoning, thinking).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.replay import replay_events_file

_EXEC = {"kind": "execution_start", "call_id": "a1", "timestamp": 1.0, "agent_id": "planner", "input": "hi"}
_EXEC_END = {"kind": "execution_end", "call_id": "a1", "timestamp": 9.0, "agent_id": "planner", "output": "done"}
_LLM = {
    "kind": "llm_call_start",
    "call_id": "l1",
    "parent_call_id": "a1",
    "timestamp": 2.0,
    "agent_id": "planner",
    "model": "openai/o4-mini",
    "messages": [{"role": "user", "content": "plan"}],
    "reasoning_effort": "high",
    "state_in": "a1#0",
    "state_in_content": "hi",
    "state_out": "a1#1",
}
_LLM_END = {
    "kind": "llm_call_end",
    "call_id": "l1",
    "timestamp": 3.0,
    "agent_id": "planner",
    "output": "the plan",
    "tokens_used": {
        "prompt_tokens": 5,
        "completion_tokens": 30,
        "total_tokens": 35,
        "completion_tokens_details": {"reasoning_tokens": 24},
    },
    "state_in": "a1#0",
    "state_in_content": "hi",
    "state_out": "a1#1",
    "state_out_content": "the plan",
}
_THINK = {
    "kind": "thinking_start",
    "call_id": "l1#think",
    "parent_call_id": "l1",
    "timestamp": 2.2,
    "agent_id": "planner",
    "state_in": "a1#0",
    "state_in_content": "hi",
    "state_out": "l1#think#0",
}
_THINK_END = {**_THINK, "kind": "thinking_end", "timestamp": 2.7, "state_out_content": "first, list the steps"}


def _spans(tmp_path: Path, events: list[dict], name: str = "spans") -> list[dict]:
    src = tmp_path / f"{name}.events.jsonl"
    src.write_text("\n".join(json.dumps(e) for e in events))
    out = tmp_path / f"{name}.jsonl"
    replay_events_file(
        src,
        out,
        app_name="app",
        converter_profile="observe_sdk",
        export_layers={
            "structure": True,
            "execution": True,
            "semantic": True,
            "provenance": True,
            "governance": True,
            "annotation": True,
        },
        extensions=True,
    )
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


def _one(spans: list[dict], name: str) -> dict:
    found = [s for s in spans if s["name"] == name]
    assert len(found) == 1, (name, [s["name"] for s in spans])
    return found[0]


def _sid(span: dict) -> str:
    return span["context"]["span_id"][2:]


def test_capability_spans_name_their_agent_span(tmp_path):
    events = [
        _EXEC,
        {
            "kind": "skill_execution_start",
            "call_id": "s1",
            "parent_call_id": "a1",
            "timestamp": 2,
            "agent_id": "planner",
            "skill_name": "plan",
        },
        {"kind": "skill_execution_end", "call_id": "s1", "timestamp": 2.1, "agent_id": "planner", "output": "ok"},
        {
            "kind": "memory_call_start",
            "call_id": "m1",
            "parent_call_id": "a1",
            "timestamp": 3,
            "agent_id": "planner",
            "memory_name": "notes",
        },
        {"kind": "memory_call_end", "call_id": "m1", "timestamp": 3.1, "agent_id": "planner", "output": "row"},
        {
            "kind": "rag_query_start",
            "call_id": "r1",
            "parent_call_id": "a1",
            "timestamp": 4,
            "agent_id": "planner",
            "collection": "docs",
            "query": "q",
        },
        {"kind": "rag_query_end", "call_id": "r1", "timestamp": 4.1, "agent_id": "planner", "result": "hit"},
        _EXEC_END,
    ]
    spans = _spans(tmp_path, events)
    agent = _sid(_one(spans, "planner.agent"))
    for name in ("plan.skill", "planner.memory", "planner.rag"):
        assert _one(spans, name)["attributes"][semconv.IOA_AGENT_SPAN_ID] == agent, name


def test_memory_operations_use_the_ontology_vocabulary(tmp_path):
    events = [
        _EXEC,
        {
            "kind": "memory_store_start",
            "call_id": "w1",
            "parent_call_id": "a1",
            "timestamp": 2,
            "agent_id": "planner",
            "memory_name": "notes",
            "data": "remember x",
        },
        {"kind": "memory_store_end", "call_id": "w1", "timestamp": 2.1, "agent_id": "planner"},
        {
            "kind": "memory_read_start",
            "call_id": "m1",
            "parent_call_id": "a1",
            "timestamp": 3,
            "agent_id": "planner",
            "memory_name": "notes",
            "key": "x",
        },
        {"kind": "memory_read_end", "call_id": "m1", "timestamp": 3.1, "agent_id": "planner", "result": "x"},
        {
            "kind": "rag_query_start",
            "call_id": "r1",
            "parent_call_id": "a1",
            "timestamp": 4,
            "agent_id": "planner",
            "collection": "docs",
        },
        {"kind": "rag_query_end", "call_id": "r1", "timestamp": 4.1, "agent_id": "planner", "result": "hit"},
        _EXEC_END,
    ]
    spans = _spans(tmp_path, events)
    ops = [
        (s["attributes"]["mas.memory.operation"], s["attributes"][semconv.OBSERVE_MEMORY_OPERATION])
        for s in spans
        if s["name"] in ("planner.memory", "planner.rag")
    ]
    assert sorted(ops) == [("retrieve", "retrieve"), ("retrieve", "retrieve"), ("store", "store")]
    store = next(s for s in spans if s["attributes"].get("mas.memory.operation") == "store")
    # A write that reports nothing back closes with what it stored: never an empty output.
    assert store["attributes"]["mas.memory.output"] == "remember x"
    assert store["attributes"][semconv.IOA_ENTITY_OUTPUT] == "remember x"
    assert store["attributes"][semconv.IOA_ENTITY_NAME] == "notes"


def test_annotation_worker_and_thinking_span_names(tmp_path):
    events = [
        _EXEC,
        {"kind": "routing", "parent_call_id": "a1", "timestamp": 1.5, "agent_id": "planner", "selected_agent": "w"},
        {
            "kind": "infrastructure_info",
            "parent_call_id": "a1",
            "timestamp": 1.6,
            "agent_id": "planner",
            "worker_id": "w-1",
            "worker_pid": 7,
        },
        _LLM,
        _THINK,
        _THINK_END,
        _LLM_END,
        _EXEC_END,
    ]
    spans = _spans(tmp_path, events)
    names = {s["name"] for s in spans}
    assert {"planner.annotation", "planner.worker", "planner.thinking"} <= names
    chat = _one(spans, "planner.chat")
    thinking = _one(spans, "planner.thinking")
    assert thinking["parent_id"][2:] == _sid(chat)
    assert _one(spans, "planner.worker")["parent_id"][2:] == _sid(_one(spans, "planner.agent"))


def test_thinking_span_carries_the_state_contract_and_the_model(tmp_path):
    spans = _spans(tmp_path, [_EXEC, _LLM, _THINK, _THINK_END, _LLM_END, _EXEC_END])
    attrs = _one(spans, "planner.thinking")["attributes"]
    assert attrs[semconv.OBSERVE_STATE_INPUT_ID] == "a1#0"
    assert attrs[semconv.OBSERVE_STATE_INPUT_CONTENT] == "hi"
    assert attrs[semconv.OBSERVE_STATE_OUTPUT_ID] == "l1#think#0"
    assert attrs[semconv.OBSERVE_STATE_OUTPUT_CONTENT] == "first, list the steps"
    assert (attrs[semconv.GEN_AI_PROVIDER], attrs[semconv.GEN_AI_REQUEST_MODEL]) == ("openai", "o4-mini")
    thinking = _one(spans, "planner.thinking")
    assert thinking["start_time"] < thinking["end_time"]


def test_llm_span_carries_reasoning_tokens_and_effort(tmp_path):
    attrs = _one(_spans(tmp_path, [_EXEC, _LLM, _LLM_END, _EXEC_END]), "planner.chat")["attributes"]
    assert attrs[semconv.GEN_AI_USAGE_REASONING_TOKENS] == 24
    assert attrs[semconv.GEN_AI_REQUEST_REASONING_EFFORT] == "high"


@pytest.mark.parametrize("before_llm", [True, False])
def test_context_contribution_annotates_the_llm_span_by_its_exported_id(tmp_path, before_llm):
    part = {
        "kind": "context_part_contributed",
        "timestamp": 1.9,
        "agent_id": "planner",
        "llm_call_id": "l1",
        "parent_call_id": "l1",
        "part_id": "p1",
        "source": "system",
    }
    middle = [part, _LLM] if before_llm else [_LLM, part]
    spans = _spans(tmp_path, [_EXEC, *middle, _LLM_END, _EXEC_END])
    context = _one(spans, "planner.context")["attributes"]
    assert context[semconv.OBSERVE_ANNOTATES_SPAN_ID] == _sid(_one(spans, "planner.chat"))
    assert context["mas.context.llm_call_id"] == "l1"


def test_every_deciding_governance_event_carries_its_decision_and_policy_name(tmp_path):
    base = {"parent_call_id": "a1", "agent_id": "planner"}
    events = [
        _EXEC,
        {**base, "kind": "governance_checked", "timestamp": 2, "outcome": "allowed", "policy_name": "p-check"},
        {
            **base,
            "kind": "governance_decision",
            "timestamp": 3,
            "decision": "deny",
            "policy_name": "p-dec",
            "reason": "r",
        },
        {**base, "kind": "hitl_resolve", "timestamp": 4, "resolution": "approved"},
        {**base, "kind": "policy_denial", "timestamp": 5, "policy_id": "p-legacy"},
        _EXEC_END,
    ]
    spans = [s for s in _spans(tmp_path, events) if s["name"] == "planner.governance"]
    got = {
        s["attributes"]["mas.governance.kind"]: (
            s["attributes"].get("mas.governance.decision_type"),
            s["attributes"].get("mas.governance.policy_name"),
        )
        for s in spans
    }
    assert got == {
        "governance_checked": ("allowed", "p-check"),
        "governance_decision": ("deny", "p-dec"),
        "hitl_resolve": ("approved", None),
        "policy_denial": ("deny", "p-legacy"),
    }
    assert not any("mas.governance.policy_id" in s["attributes"] for s in spans)
    assert not any("mas.governance.outcome" in s["attributes"] for s in spans)


def test_governance_check_intervals_are_processing_with_the_outcome_as_a_point(tmp_path):
    events = [
        _EXEC,
        {
            "kind": "governance_authorize_start",
            "call_id": "l1",
            "parent_call_id": "a1",
            "timestamp": 2,
            "agent_id": "planner",
            "op": "LLM_CALL",
        },
        {
            "kind": "governance_authorize_end",
            "call_id": "l1",
            "parent_call_id": "a1",
            "timestamp": 2.5,
            "agent_id": "planner",
            "outcome": "allow",
        },
        {
            "kind": "governance_validate_start",
            "call_id": "l1",
            "parent_call_id": "a1",
            "timestamp": 3,
            "agent_id": "planner",
        },
        {
            "kind": "governance_validate_end",
            "call_id": "l1",
            "parent_call_id": "a1",
            "timestamp": 3.5,
            "agent_id": "planner",
        },
        _EXEC_END,
    ]
    spans = _spans(tmp_path, events)
    processing = [s for s in spans if s["name"] == "planner.processing"]
    assert sorted(s["attributes"][semconv.OBSERVE_PROCESSING_NAME] for s in processing) == [
        "governance_authorize",
        "governance_validate",
    ]
    authorize = next(s for s in processing if s["attributes"]["mas.processing.name"] == "governance_authorize")
    points = [s for s in spans if s["name"] == "planner.governance"]
    # Only the check that stated an outcome has a decision point, under its interval.
    assert len(points) == 1
    assert points[0]["attributes"]["mas.governance.decision_type"] == "allow"
    assert points[0]["parent_id"][2:] == _sid(authorize)


def test_an_unfinished_call_is_not_exported_and_the_session_starts_with_the_run(tmp_path):
    events = [
        {"kind": "mas_call_start", "call_id": "run", "timestamp": 0.5, "agent_id": "orch"},
        {**_EXEC, "parent_call_id": "run", "session_id": "s1"},
        _LLM,
        _EXEC_END,
        {"kind": "mas_call_end", "call_id": "run", "timestamp": 9.5, "agent_id": "orch"},
    ]
    spans = _spans(tmp_path, events)
    assert not any(s["name"].endswith(".chat") for s in spans)
    start = _one(spans, "session.start")
    assert start["start_time"].startswith("1970-01-01T00:00:00.5")
    task = _one(spans, "invoke_agent app")
    # The run opened before any record named the session: it still belongs to it.
    assert task["attributes"][semconv.SESSION_ID] == "app_s1"


def test_semconv_mirrors_observe_sdk_constants():
    import mas.library.telemetry.conversion.semconv as module

    assert not hasattr(module, "_sdk")
    assert semconv.OBSERVE_STATE_INPUT_ID == "ioa_observe.state.input.id"
    assert semconv.OBSERVE_STATE_INPUT_CONTENT == "ioa_observe.state.input.content"
    assert semconv.OBSERVE_STATE_OUTPUT_ID == "ioa_observe.state.output.id"
    assert semconv.OBSERVE_STATE_OUTPUT_CONTENT == "ioa_observe.state.output.content"
    assert semconv.OBSERVE_PROCESSING_NAME == "ioa_observe.processing.name"
    assert semconv.IOA_ENTITY_NAME == "ioa_observe.entity.name"
    assert semconv.IOA_ENTITY_INPUT == "ioa_observe.entity.input"
    assert semconv.IOA_ENTITY_OUTPUT == "ioa_observe.entity.output"


def _norm_snapshot(spans: list[dict]):
    from mas.library.kg.observability.otel_via_norm import to_clickhouse_spans
    from norm.ioa_observe.build import build_kg

    return build_kg(to_clickhouse_spans(spans))


@pytest.mark.scope_next
def test_norm_builds_one_thinking_call_under_its_llm_call_in_any_span_order(tmp_path):
    spans = _spans(tmp_path, [_EXEC, _LLM, _THINK, _THINK_END, _LLM_END, _EXEC_END])
    expected = _norm_snapshot(spans)
    nodes, edges = expected
    thinking = [n for n in nodes if n["node_type"] == "ThinkingCall"]
    assert len(thinking) == 1
    llm = next(n for n in nodes if n["node_type"] == "LLMCall")
    assert {"edge_type": "hasThinkingCall", "from_id": llm["id"], "to_id": thinking[0]["id"]} in [
        {k: e[k] for k in ("edge_type", "from_id", "to_id")} for e in edges
    ]
    for order in itertools.islice(itertools.permutations(spans), 0, 40, 7):
        assert _norm_snapshot(list(order)) == expected

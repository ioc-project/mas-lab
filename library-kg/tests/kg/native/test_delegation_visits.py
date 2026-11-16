#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An agent's reply, its visits around a delegation, and a call the run never finished."""

from __future__ import annotations

from mas.library.kg.pipeline import build_kg_document


def _event(kind: str, ts: float, agent: str, call: str, **rest) -> dict:
    return {
        "kind": kind,
        "timestamp": ts,
        "run_id": "r1",
        "agent_id": agent,
        "call_id": call,
        **rest,
    }


def _delegation() -> list[dict]:
    return [
        _event("execution_start", 1.0, "boss", "boss-exec", input="plan"),
        _event(
            "tool_call_start",
            2.0,
            "boss",
            "d1",
            tool_name="delegate_to_helper",
            semantics={"concept": "agent_comm", "target": "helper"},
        ),
        _event("execution_start", 2.1, "helper", "helper-exec", input="task"),
        _event(
            "user_response",
            3.0,
            "helper",
            "helper-resp",
            parent_call_id="helper-exec",
            content="helper says hi",
        ),
        _event("execution_end", 3.1, "helper", "helper-exec", status="ok"),
        _event("tool_call_end", 3.2, "boss", "d1", tool_name="delegate_to_helper"),
        _event(
            "llm_call_start",
            4.0,
            "boss",
            "llm2",
            model="m/x",
            messages=[{"role": "user", "content": "go on"}],
        ),
        _event("llm_call_end", 4.5, "boss", "llm2", output="all done"),
        _event(
            "user_response",
            4.6,
            "boss",
            "boss-resp",
            parent_call_id="boss-exec",
            content="final answer",
        ),
        _event("execution_end", 4.7, "boss", "boss-exec", status="ok"),
    ]


def _agent_calls(events: list[dict]) -> list[dict]:
    doc = build_kg_document(events, run_id="r1")
    calls = [n for n in doc["nodes"] if n["node_type"] == "AgentCall"]
    return sorted(calls, key=lambda n: n["startTime"])


def test_a_delegation_is_an_ordinary_tool_call_and_an_ordinary_visit() -> None:
    doc = build_kg_document(_delegation(), run_id="r1", all_attributes=True)
    agents = sorted(c["agentId"] for c in doc["nodes"] if c["node_type"] == "AgentCall")
    assert agents == ["boss", "helper"]  # no resumed visit, no rewritten sibling
    tools = [n for n in doc["nodes"] if n["node_type"] == "ToolCall"]
    assert [t["toolName"] for t in tools] == ["delegate_to_helper"]
    assert tools[0]["endTime"] == 3.2


def test_a_user_response_is_not_the_agent_output() -> None:
    doc = build_kg_document(_delegation(), run_id="r1", all_attributes=True)
    assert not [n for n in doc["nodes"] if "helper says hi" in str(n.get("content", ""))]
    assert not [n for n in doc["nodes"] if n.get("outputParts") or n.get("outputContent")]


def test_a_call_the_run_never_ended_keeps_no_end_time_and_no_outcome() -> None:
    events = [
        _event("execution_start", 1.0, "boss", "boss-exec", input="plan"),
        _event(
            "llm_call_start",
            2.0,
            "boss",
            "llm",
            model="m/x",
            messages=[{"role": "user", "content": "q"}],
        ),
        _event("mas_call_end", 9.0, "mas", "mas-call"),
    ]
    doc = build_kg_document(events, run_id="r1")
    llm = next(n for n in doc["nodes"] if n["node_type"] == "LLMCall")
    assert llm["startTime"] == 2.0
    assert "endTime" not in llm
    assert "success" not in llm


def test_the_session_and_the_mas_call_share_the_boundary_states_the_mas_call_end_names() -> None:
    events = [
        _event("mas_call_start", 0.5, "mas", "mc"),
        _event("execution_start", 1.0, "boss", "e", input="plan", state_out="e#0"),
        _event("execution_end", 2.0, "boss", "e", status="ok", state_in="e#1", state_out="e#1"),
        _event(
            "mas_call_end",
            3.0,
            "mas",
            "mc",
            status="ok",
            state_in="e#0",
            state_in_content="plan",
            state_out="e#1",
            state_out_content="done",
        ),
    ]
    doc = build_kg_document(events, run_id="r1")
    session = next(n for n in doc["nodes"] if n["node_type"] == "Session")
    mas_call = next(n for n in doc["nodes"] if n["node_type"] == "MASCall")
    for edge_type, state in (
        ("hasInitialState", "state:r1:e#0"),
        ("hasFinalState", "state:r1:e#1"),
    ):
        for owner in (session, mas_call):
            assert any(
                e["edge_type"] == edge_type and e["from_id"] == owner["id"] and e["to_id"] == state
                for e in doc["edges"]
            )
    contents = {n["id"]: n["content"] for n in doc["nodes"] if n["node_type"] == "State"}
    assert contents["state:r1:e#0"] == "plan"
    assert contents["state:r1:e#1"] == "done"


def test_an_agent_with_two_executions_at_once_keeps_each_final_state_on_its_own_call() -> None:
    events = [
        _event("execution_start", 1.0, "a", "e1", input="one", state_out="e1#0"),
        _event("execution_start", 1.1, "a", "e2", input="two", state_out="e2#0"),
        _event(
            "execution_end",
            2.1,
            "a",
            "e1",
            status="ok",
            state_in="e1#1",
            state_in_content="REPLY1",
            state_out="e1#1",
        ),
        _event(
            "execution_end",
            3.1,
            "a",
            "e2",
            status="ok",
            state_in="e2#1",
            state_in_content="REPLY2",
            state_out="e2#1",
        ),
    ]
    doc = build_kg_document(events, run_id="r1")
    finals = {n["id"]: n["content"] for n in doc["nodes"] if n["node_type"] == "State"}
    assert finals["state:r1:e1#1"] == "REPLY1"
    assert finals["state:r1:e2#1"] == "REPLY2"
    by_id = {n["id"]: n for n in doc["nodes"]}
    ends = {e["from_id"]: e["to_id"] for e in doc["edges"] if e["edge_type"] == "hasFinalState"}
    assert ends["agentcall:r1:e1"] == "state:r1:e1#1"
    assert ends["agentcall:r1:e2"] == "state:r1:e2#1"
    assert by_id["agentcall:r1:e1"]["node_type"] == "AgentCall"


def test_timestamps_written_as_strings_do_not_break_the_fold() -> None:
    events = [
        _event("execution_start", "1.0", "boss", "boss-exec", input="plan"),
        _event(
            "tool_call_start",
            2.0,
            "boss",
            "d1",
            tool_name="delegate_to_helper",
            semantics={"concept": "agent_comm", "target": "helper"},
        ),
        _event("execution_end", "3.0", "helper", "helper-exec", status="ok"),
        _event(
            "llm_call_start",
            4.0,
            "boss",
            "llm2",
            model="m/x",
            messages=[{"role": "user", "content": "go"}],
        ),
        _event("execution_end", "5.0", "boss", "boss-exec", status="ok"),
    ]
    assert build_kg_document(events, run_id="r1")["nodes"]


def test_iso_timestamps_are_read_as_epoch_seconds() -> None:
    events = [
        _event("execution_start", "2026-01-01T00:00:01Z", "boss", "e", input="p"),
        _event(
            "llm_call_start",
            "2026-01-01T00:00:02+00:00",
            "boss",
            "l",
            model="m/x",
            messages=[{"role": "user", "content": "q"}],
        ),
        _event("llm_call_end", "2026-01-01T00:00:03Z", "boss", "l", output="o"),
        _event("execution_end", "2026-01-01T00:00:04Z", "boss", "e", status="ok"),
    ]
    doc = build_kg_document(events, run_id="r1")
    call = next(n for n in doc["nodes"] if n["node_type"] == "AgentCall")
    assert (call["startTime"], call["endTime"]) == (1767225601.0, 1767225604.0)


def test_a_status_in_neither_vocabulary_fails_the_call_and_is_reported_once(caplog) -> None:
    import logging

    from mas.library.kg.core import native_graph_builder as ngb

    ngb._UNKNOWN_STATUS_SEEN.discard("paused")
    assert ngb.status_is_success("ok") and ngb.status_is_success(None)
    assert not ngb.status_is_success("error")
    with caplog.at_level(logging.WARNING, logger=ngb.logger.name):
        assert not ngb.status_is_success("paused")
        assert not ngb.status_is_success("Paused")
    assert caplog.text.count("unknown call status") == 1


def _canon(doc: dict) -> tuple:
    nodes = sorted((n["node_type"], str(n.get("name")), n.get("startTime")) for n in doc["nodes"])
    edges = sorted((e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"])
    return nodes, edges


def test_a_realtime_fold_builds_the_batch_graph(tmp_path) -> None:
    import gzip
    import json

    path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "fixtures"
        / "path_equivalence"
        / "delegation-events.jsonl.gz"
    )
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    batch = build_kg_document(events, run_id="r1")
    live = build_kg_document(events, run_id="r1", realtime=True)
    assert live["metadata"]["realtime"] is True
    assert _canon(live) == _canon(batch)


def test_a_record_without_state_fields_gets_no_state_and_no_transition() -> None:
    events = [
        _event("execution_start", 1.0, "boss", "e", input="plan"),
        _event(
            "processing_call_start", 1.1, "boss", "p", parent_call_id="e", processing_name="wait"
        ),
        _event("processing_call_end", 1.2, "boss", "p", parent_call_id="e", processing_name="wait"),
        _event("execution_end", 2.0, "boss", "e", status="ok"),
    ]
    doc = build_kg_document(events, run_id="r1")
    assert not [n for n in doc["nodes"] if n["node_type"] in ("State", "Transition")]
    assert "(no content recorded)" not in str(doc)

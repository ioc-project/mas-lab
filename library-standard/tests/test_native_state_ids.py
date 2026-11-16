#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Explicit dataflow State identity on native records (``state_in`` / ``state_out``).

A visit is one ``execution_start`` .. ``execution_end`` (keyed by exec_call_id).
Its States are ``{exec_call_id}#{n}``, numbered from 0 in emission order.
"""

from __future__ import annotations

import json

from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext

MESSAGES = [
    {"role": "system", "content": "You are a weather agent."},
    {"role": "user", "content": "Weather in Paris?"},
]
TOOL_CALLS = [{"name": "get_weather", "arguments": {"city": "Paris"}}]
TOOL_CALLS_JSON = '[{"arguments":{"city":"Paris"},"name":"get_weather"}]'
MESSAGES_JSON = json.dumps(MESSAGES, sort_keys=True, separators=(",", ":"))


def _ctx() -> TransformContext:
    return TransformContext(agent_id="weather", run_id="run-1")


def _feed(records: list[dict], ctx: TransformContext) -> list[dict]:
    transform = NativeObservabilityTransform()
    out: list[dict] = []
    for record in records:
        out.extend(transform.transform(record, ctx=ctx))
    return out


def _user(text: str = "Weather in Paris?", call_id: str = "exec-1") -> dict:
    return {"_source": "session", "session_kind": "user_input", "text": text, "call_id": call_id, "turn_id": "t1"}


def _response(text: str = "It is sunny in Paris.") -> dict:
    return {"_source": "session", "session_kind": "agent_response", "text": text, "finish_reason": "stop"}


def _assembled(cid: int) -> dict:
    return {
        "_source": "boundary",
        "kind": "context.assembled",
        "correlation_id": cid,
        "payload": {"messages": MESSAGES, "segments": [], "message_count": len(MESSAGES)},
    }


def _llm_start(cid: int) -> dict:
    return {
        "_source": "boundary",
        "kind": "engine.io",
        "correlation_id": cid,
        "payload": {"op": "LLM_CALL", "model": "test-model", "messages": MESSAGES},
    }


def _llm_end(cid: int, text: str = "", tool_calls: list | None = None) -> dict:
    payload: dict = {"op": "LLM_CALL", "text": text, "next_step": "STOP"}
    if tool_calls is not None:
        payload["tool_calls"] = tool_calls
    return {"_source": "boundary", "kind": "engine.io.return", "correlation_id": cid, "payload": payload}


def _tool_start(cid: int, name: str) -> dict:
    return {
        "_source": "boundary",
        "kind": "engine.io",
        "correlation_id": cid,
        "payload": {"op": "TOOL_CALL", "tool_name": name, "tool_arguments": {"city": "Paris"}},
    }


def _tool_end(cid: int, output: str) -> dict:
    return {
        "_source": "boundary",
        "kind": "engine.io.return",
        "correlation_id": cid,
        "payload": {"op": "TOOL_CALL", "text": output},
    }


def _envelope(cid: int, activity: str, boundary: str, **extra: object) -> dict:
    return {
        "_source": "boundary",
        "kind": "envelope.activity",
        "correlation_id": cid,
        "payload": {"activity": activity, "boundary": boundary, **extra},
    }


def _single_agent_visit() -> list[dict]:
    """user_input, context, LLM (tool-use turn), tool, context, LLM (text), agent_response."""
    return _feed(
        [
            _user(),
            _assembled(1),
            _llm_start(1),
            _llm_end(1, tool_calls=TOOL_CALLS),
            _tool_start(2, "get_weather"),
            _tool_end(2, "sunny"),
            _assembled(3),
            _llm_start(3),
            _llm_end(3, text="It is sunny in Paris."),
            _response(),
        ],
        _ctx(),
    )


def _kind(out: list[dict], kind: str) -> list[dict]:
    return [r for r in out if r.get("kind") == kind]


def _state_view(out: list[dict]) -> list[tuple]:
    return [
        (r["kind"], r.get("state_in"), r.get("state_out"), r.get("state_out_content"))
        for r in out
        if "state_in" in r or "state_out" in r
    ]


def test_single_agent_visit_chains_states_in_emission_order() -> None:
    out = _single_agent_visit()
    chain = [(r["kind"], r["state_in"], r["state_out"]) for r in out if "state_in" in r]
    assert chain == [
        ("processing_call_start", "exec-1#0", "exec-1#1"),
        ("processing_call_end", "exec-1#0", "exec-1#1"),
        ("llm_call_start", "exec-1#1", "exec-1#2"),
        ("llm_call_end", "exec-1#1", "exec-1#2"),
        ("tool_call_start", "exec-1#2", "exec-1#3"),
        ("tool_call_end", "exec-1#2", "exec-1#3"),
        ("processing_call_start", "exec-1#3", "exec-1#4"),
        ("processing_call_end", "exec-1#3", "exec-1#4"),
        ("llm_call_start", "exec-1#4", "exec-1#5"),
        ("llm_call_end", "exec-1#4", "exec-1#5"),
        ("execution_end", "exec-1#5", "exec-1#5"),
    ]


def test_single_agent_visit_contents() -> None:
    out = _single_agent_visit()
    start = _kind(out, "execution_start")[0]
    assert start["state_out"] == "exec-1#0"
    assert start["state_out_content"] == "Weather in Paris?"
    assert "state_in" not in start

    llm_ends = _kind(out, "llm_call_end")
    assert llm_ends[0]["state_out_content"] == TOOL_CALLS_JSON
    assert llm_ends[1]["state_out_content"] == "It is sunny in Paris."

    assert _kind(out, "tool_call_end")[0]["state_out_content"] == "sunny"

    ctx_starts = [r for r in _kind(out, "processing_call_start") if r.get("processing_type") == "context_assembly"]
    assert [r["state_out_content"] for r in ctx_starts] == [MESSAGES_JSON, MESSAGES_JSON]
    ctx_ends = [r for r in _kind(out, "processing_call_end") if r.get("processing_type") == "context_assembly"]
    assert [r["state_out_content"] for r in ctx_ends] == [MESSAGES_JSON, MESSAGES_JSON]

    end = _kind(out, "execution_end")[0]
    assert end["state_in"] == end["state_out"] == "exec-1#5"


def test_state_in_content_is_the_producers_stated_content() -> None:
    out = _single_agent_visit()
    produced: dict[str, str] = {"exec-1#0": "Weather in Paris?"}
    checked = 0
    for r in out:
        if "state_in" not in r:
            continue
        assert r["state_in_content"] == produced[r["state_in"]], r["kind"]
        checked += 1
        if "state_out_content" in r:
            produced.setdefault(r["state_out"], r["state_out_content"])
    assert checked == 11
    assert _kind(out, "execution_end")[0]["state_in_content"] == "It is sunny in Paris."


def test_start_and_end_of_one_call_carry_identical_state() -> None:
    out = _single_agent_visit()
    for start_kind, end_kind in [
        ("llm_call_start", "llm_call_end"),
        ("tool_call_start", "tool_call_end"),
        ("processing_call_start", "processing_call_end"),
    ]:
        starts = [r for r in _kind(out, start_kind) if "state_in" in r]
        assert starts, start_kind
        for start in starts:
            ends = [r for r in _kind(out, end_kind) if r["call_id"] == start["call_id"]]
            assert len(ends) == 1, (start_kind, start["call_id"])
            assert ends[0]["state_in"] == start["state_in"]
            assert ends[0]["state_out"] == start["state_out"]


def test_control_markers_carry_no_state_fields() -> None:
    out = _feed(
        [
            _user(),
            _envelope(5, "wait_state", "start", wait_link_id="delegate-5", wait_role="WAIT", wait_note="waiting"),
            _envelope(5, "wait_state", "end", wait_link_id="delegate-5", wait_role="RESUME"),
            _envelope(7, "parallel_group", "start", group_id="g-empty", tools=[]),
            _envelope(7, "parallel_group", "end", group_id="g-empty", tools=[]),
            _response(),
        ],
        _ctx(),
    )
    markers = [r for r in out if str(r.get("kind", "")).startswith(("wait_state_", "parallel_group_"))]
    assert [r["kind"] for r in markers] == [
        "wait_state_start",
        "wait_state_end",
        "parallel_group_start",
        "parallel_group_end",
    ]
    for marker in markers:
        assert not {"state_in", "state_out", "state_out_content", "state_in_content"} & set(marker), marker["kind"]
    # Wait and resume have no ProcessingCall companions. The empty group's only
    # ProcessingCall is its aggregate pair.
    processing = [r for r in out if str(r.get("kind", "")).startswith("processing_call_")]
    assert not [r for r in processing if r.get("processing_type") == "wait_state"]
    assert {r["processing_name"] for r in processing if r.get("processing_type") == "parallel_group"} == {"aggregate"}


def test_parallel_group_members_share_group_input_and_join() -> None:
    tools = [{"correlation_id": c, "tool_name": f"t{c}"} for c in (11, 12, 13)]
    out = _feed(
        [
            _user(),
            _assembled(1),
            _llm_start(1),
            _llm_end(1, tool_calls=[{"name": f"t{c}", "arguments": {}} for c in (11, 12, 13)]),
            _envelope(10, "parallel_group", "start", group_id="g-1", tools=tools),
            _tool_start(11, "t11"),
            _tool_start(12, "t12"),
            _tool_start(13, "t13"),
            _tool_end(11, "o11"),
            _tool_end(12, "o12"),
            _tool_end(13, "o13"),
            _envelope(10, "parallel_group", "end", group_id="g-1", tools=tools),
            _assembled(2),
            _llm_start(2),
            _llm_end(2, text="done"),
            _response(),
        ],
        _ctx(),
    )
    tool_starts = _kind(out, "tool_call_start")
    assert [r["state_in"] for r in tool_starts] == ["exec-1#2"] * 3
    outs = [r["state_out"] for r in tool_starts]
    assert outs == ["exec-1#3", "exec-1#4", "exec-1#5"]
    assert len(set(outs)) == 3
    for start, end in zip(tool_starts, _kind(out, "tool_call_end"), strict=True):
        assert (end["state_in"], end["state_out"]) == (start["state_in"], start["state_out"])
    assert [r["state_out_content"] for r in _kind(out, "tool_call_end")] == ["o11", "o12", "o13"]

    # The join is the aggregate ProcessingCall pair at the group end; the control markers carry no state.
    assert "state_in" not in _kind(out, "parallel_group_start")[0]
    assert "state_in" not in _kind(out, "parallel_group_end")[0]
    aggregate_start = next(r for r in _kind(out, "processing_call_start") if r.get("processing_name") == "aggregate")
    join = next(r for r in _kind(out, "processing_call_end") if r.get("processing_name") == "aggregate")
    assert aggregate_start["call_id"] == join["call_id"]
    assert join["state_in"] == outs
    assert join["state_out"] == "exec-1#6"
    assert join["state_out_content"] == '["o11","o12","o13"]'
    assert join["state_in_content"] == '["o11","o12","o13"]'

    # Members read the group's input State content; the join lists the members' contents.
    llm1_content = _kind(out, "llm_call_end")[0]["state_out_content"]
    assert [r["state_in_content"] for r in tool_starts] == [llm1_content] * 3
    assert [r["state_in_content"] for r in _kind(out, "tool_call_end")] == [llm1_content] * 3

    # The next element chains from the join, not from a group member.
    after = [r for r in _kind(out, "processing_call_start") if r.get("processing_type") == "context_assembly"][1]
    assert after["state_in"] == "exec-1#6"
    assert after["state_in_content"] == join["state_out_content"]
    llm_after = _kind(out, "llm_call_start")[1]
    assert llm_after["state_in"] == "exec-1#7"
    assert _kind(out, "execution_end")[0]["state_out"] == "exec-1#8"


def test_two_visits_have_independent_chains() -> None:
    ctx = _ctx()
    first = _feed([_user("first", "exec-A"), _assembled(1), _llm_start(1), _llm_end(1, text="a"), _response("a")], ctx)
    second = _feed(
        [_user("second", "exec-B"), _assembled(1), _llm_start(1), _llm_end(1, text="b"), _response("b")], ctx
    )

    assert _kind(first, "execution_start")[0]["state_out"] == "exec-A#0"
    assert _kind(second, "execution_start")[0]["state_out"] == "exec-B#0"
    for record in first:
        for field in ("state_in", "state_out"):
            if isinstance(record.get(field), str) and record.get(field):
                assert record[field].startswith("exec-A#"), record
    for record in second:
        for field in ("state_in", "state_out"):
            if isinstance(record.get(field), str) and record.get(field):
                assert record[field].startswith("exec-B#"), record
    ctx_second = [r for r in _kind(second, "processing_call_start") if r.get("processing_type") == "context_assembly"]
    assert ctx_second[0]["state_in"] == "exec-B#0"


def test_same_input_gives_identical_state_ids() -> None:
    assert _state_view(_single_agent_visit()) == _state_view(_single_agent_visit())


def test_the_aggregate_waits_for_the_members_when_the_group_end_comes_first() -> None:
    """The runtime reports the group end before its members return (observed on a real run)."""
    tools = [{"correlation_id": c, "tool_name": f"t{c}"} for c in (11, 12)]
    out = _feed(
        [
            _user(),
            _assembled(1),
            _llm_start(1),
            _llm_end(1, tool_calls=[{"name": f"t{c}", "arguments": {}} for c in (11, 12)]),
            _envelope(10, "parallel_group", "start", group_id="g-1", tools=tools),
            _tool_start(11, "t11"),
            _tool_start(12, "t12"),
            _envelope(10, "parallel_group", "end", group_id="g-1", tools=tools),
            _tool_end(11, "o11"),
            _tool_end(12, "o12"),
            _assembled(2),
            _llm_start(2),
        ],
        _ctx(),
    )
    aggregates = [r for r in _kind(out, "processing_call_end") if r.get("processing_name") == "aggregate"]
    assert len(aggregates) == 1
    join = aggregates[0]
    assert join["state_in_content"] == '["o11","o12"]' and join["state_out_content"] == '["o11","o12"]'
    kinds = [r["kind"] for r in out]
    assert kinds.index("processing_call_end") < len(kinds)  # emitted
    # It comes after the last member's return, and the next element chains from the join.
    last_tool_end = max(i for i, r in enumerate(out) if r["kind"] == "tool_call_end")
    join_at = next(i for i, r in enumerate(out) if r is join)
    assert join_at > last_tool_end
    next_assembly = [r for r in _kind(out, "processing_call_start") if r.get("processing_type") == "context_assembly"][
        -1
    ]
    assert next_assembly["state_in"] == join["state_out"]

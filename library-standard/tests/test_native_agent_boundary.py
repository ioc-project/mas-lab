#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent-boundary dataflow on native records.

A delegation is a tool call on the caller that starts a visit on the callee.
The callee's visit carries ``parent_call_id`` = that tool call id. The caller
gets a ``delegate`` ProcessingCall pair before the callee starts and a
``reply`` pair after the callee ends. Control markers (wait, parallel group)
are plain records; the only parallel ProcessingCall is the ``aggregate`` pair.
"""

from __future__ import annotations

from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin

MESSAGES = [
    {"role": "system", "content": "You are an agent."},
    {"role": "user", "content": "Go."},
]


def _feed(records: list[dict], ctx: TransformContext) -> list[dict]:
    transform = NativeObservabilityTransform()
    out: list[dict] = []
    for record in records:
        out.extend(transform.transform(record, ctx=ctx))
    return out


def _kind(out: list[dict], kind: str) -> list[dict]:
    return [r for r in out if r.get("kind") == kind]


def _index(out: list[dict], kind: str, call_id: str | None = None) -> int:
    for i, r in enumerate(out):
        if r.get("kind") == kind and (call_id is None or r.get("call_id") == call_id):
            return i
    raise AssertionError(f"no {kind} {call_id}")


def _pair(out: list[dict], call_id: str) -> list[dict]:
    return [
        r
        for r in out
        if r.get("kind") in ("processing_call_start", "processing_call_end") and r.get("call_id") == call_id
    ]


def _user(text: str, call_id: str, parent_call_id: str | None = None) -> dict:
    rec = {"_source": "session", "session_kind": "user_input", "text": text, "call_id": call_id, "turn_id": "t1"}
    if parent_call_id is not None:
        rec["parent_call_id"] = parent_call_id
    return rec


def _response(text: str) -> dict:
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
        "payload": {"op": "LLM_CALL", "messages": MESSAGES, "model": "m"},
    }


def _llm_end(cid: int, text: str = "", tool_calls: list | None = None) -> dict:
    payload: dict = {"op": "LLM_CALL", "text": text, "next_step": "STOP"}
    if tool_calls is not None:
        payload["tool_calls"] = tool_calls
    return {"_source": "boundary", "kind": "engine.io.return", "correlation_id": cid, "payload": payload}


def _tool_start(cid: int, call_id: str, name: str) -> dict:
    return {
        "_source": "boundary",
        "kind": "engine.io",
        "call_id": call_id,
        "correlation_id": cid,
        "payload": {"op": "TOOL_CALL", "tool_name": name, "tool_arguments": {"task": name}},
    }


def _envelope(cid: int, activity: str, boundary: str, **extra: object) -> dict:
    return {
        "_source": "boundary",
        "kind": "envelope.activity",
        "correlation_id": cid,
        "payload": {"activity": activity, "boundary": boundary, **extra},
    }


def _root_turn(tool_ids: list[str], *, group: bool) -> list[dict]:
    """Root visit that delegates through one tool call per id (optionally in a parallel group)."""
    calls = [{"name": "delegate_to_worker", "arguments": {"task": t}} for t in tool_ids]
    records: list[dict] = [
        _user("Plan the trip", "exec-root"),
        _assembled(1),
        _llm_start(1),
        _llm_end(1, tool_calls=calls),
    ]
    if group:
        records.append(_envelope(10, "parallel_group", "start", group_id="g-1", tools=[]))
    for i, tid in enumerate(tool_ids):
        records.append(_tool_start(20 + i, tid, "delegate_to_worker"))
    if group:
        records.append(_envelope(10, "parallel_group", "end", group_id="g-1", tools=[]))
    return records


def _callee_visit(exec_id: str, parent_call_id: str | None, cid: int, reply: str) -> list[dict]:
    return [
        _user(f"task for {exec_id}", exec_id, parent_call_id=parent_call_id),
        _assembled(cid),
        _llm_start(cid),
        _llm_end(cid, text=reply),
        _response(reply),
    ]


def _shared_pair() -> tuple[TransformContext, TransformContext]:
    shared: dict = {}
    return (
        TransformContext(agent_id="root", run_id="r1", shared=shared),
        TransformContext(agent_id="worker", run_id="r1", shared=shared),
    )


def test_delegation_gives_caller_a_delegate_pair_before_callee_starts() -> None:
    root, worker = _shared_pair()
    caller_out = _feed(_root_turn(["tool-1"], group=False), root)
    tool_start = _kind(caller_out, "tool_call_start")[0]

    callee_out = _feed(_callee_visit("exec-w1", "tool-1", 1, "Rome is rainy."), worker)

    delegate = _pair(callee_out, "tool-1:delegate")
    assert [r["kind"] for r in delegate] == ["processing_call_start", "processing_call_end"]
    start, end = delegate
    assert start["agent_id"] == "root"
    assert start["parent_call_id"] == "exec-root"
    assert start["processing_name"] == "delegate"
    assert start["processing_type"] == "agent_boundary"
    assert start["timestamp"] == end["timestamp"]
    assert start["state_in"] == tool_start["state_in"]
    assert start["state_in_content"] == tool_start["state_in_content"]
    assert start["state_out"] == "exec-w1#0"
    assert start["state_out_content"] == "task for exec-w1"
    assert end["state_out"] == "exec-w1#0"

    # The pair comes before the callee's execution_start.
    assert _index(callee_out, "processing_call_start", "tool-1:delegate") < _index(callee_out, "execution_start")


def test_callee_reply_gives_caller_a_reply_pair_after_execution_end() -> None:
    root, worker = _shared_pair()
    caller_out = _feed(_root_turn(["tool-1"], group=False), root)
    tool_start = _kind(caller_out, "tool_call_start")[0]

    callee_out = _feed(_callee_visit("exec-w1", "tool-1", 1, "Rome is rainy."), worker)

    final_id = _kind(callee_out, "execution_end")[0]["state_in"]
    reply = _pair(callee_out, "tool-1:reply")
    assert [r["kind"] for r in reply] == ["processing_call_start", "processing_call_end"]
    start, end = reply
    assert start["agent_id"] == "root"
    assert start["parent_call_id"] == "exec-root"
    assert start["processing_name"] == "reply"
    assert start["processing_type"] == "agent_boundary"
    assert start["timestamp"] == end["timestamp"]
    assert start["state_in"] == final_id
    assert start["state_in_content"] == "Rome is rainy."
    assert start["state_out"] == tool_start["state_out"]
    assert start["state_out_content"] == "Rome is rainy."
    assert end["state_out"] == tool_start["state_out"]

    assert _index(callee_out, "processing_call_start", "tool-1:reply") > _index(callee_out, "execution_end")


def test_root_visit_gets_no_delegate_or_reply() -> None:
    ctx = TransformContext(agent_id="root", run_id="r1", shared={})
    out = _feed(
        [_user("Plan", "exec-root"), _assembled(1), _llm_start(1), _llm_end(1, text="done"), _response("done")],
        ctx,
    )
    boundary = [r for r in out if r.get("processing_type") == "agent_boundary"]
    assert boundary == []
    assert not [r for r in out if str(r.get("call_id", "")).endswith((":delegate", ":reply"))]


def test_parallel_delegations_to_one_agent_each_get_their_own_pairs() -> None:
    root, worker = _shared_pair()
    tool_ids = ["t1", "t2", "t3"]
    caller_out = _feed(_root_turn(tool_ids, group=True), root)
    tool_starts = {r["call_id"]: r for r in _kind(caller_out, "tool_call_start")}

    delegates: list[dict] = []
    for i, tid in enumerate(tool_ids):
        callee_out = _feed(_callee_visit(f"exec-{tid}", tid, 100 + i, f"r {tid}"), worker)
        delegate_start = _pair(callee_out, f"{tid}:delegate")[0]
        reply_start = _pair(callee_out, f"{tid}:reply")[0]
        assert delegate_start["state_out"] == f"exec-{tid}#0"
        assert delegate_start["state_in"] == tool_starts[tid]["state_in"]
        assert reply_start["state_out"] == tool_starts[tid]["state_out"]
        assert reply_start["state_in_content"] == f"r {tid}"
        delegates.append(delegate_start)

    assert [d["call_id"] for d in delegates] == ["t1:delegate", "t2:delegate", "t3:delegate"]
    assert len({tool_starts[t]["state_out"] for t in tool_ids}) == 3


def test_control_markers_are_plain_records_and_aggregate_is_the_only_parallel_pair() -> None:
    root, _ = _shared_pair()
    out = _feed(
        [
            _user("Plan", "exec-root"),
            _assembled(1),
            _llm_start(1),
            _llm_end(1, text="waiting"),
            _envelope(21, "wait_state", "start", wait_link_id="delegate-21", wait_role="WAIT", wait_note="waiting"),
            _envelope(21, "wait_state", "end", wait_link_id="delegate-21", wait_role="RESUME"),
            _envelope(30, "parallel_group", "start", group_id="g-2", tools=[]),
            _envelope(30, "parallel_group", "end", group_id="g-2", tools=[]),
            _response("done"),
        ],
        root,
    )

    assert [r["kind"] for r in out if str(r["kind"]).startswith("wait_state_")] == [
        "wait_state_start",
        "wait_state_end",
    ]
    assert [r["kind"] for r in out if str(r["kind"]).startswith("parallel_group_")] == [
        "parallel_group_start",
        "parallel_group_end",
    ]
    assert not [r for r in out if r.get("processing_type") == "wait_state"]
    group_pairs = [r for r in out if r.get("processing_type") == "parallel_group"]
    assert [(r["kind"], r["processing_name"]) for r in group_pairs] == [
        ("processing_call_start", "aggregate"),
        ("processing_call_end", "aggregate"),
    ]
    assert group_pairs[0]["call_id"] == group_pairs[1]["call_id"]
    end_marker = _kind(out, "parallel_group_end")[0]
    assert not {"state_in", "state_out", "state_out_content", "state_in_content"} & set(end_marker)


def test_context_assembly_end_is_zero_duration_and_not_synthetic() -> None:
    ctx = TransformContext(agent_id="root", run_id="r1", shared={})
    out = _feed([_user("Plan", "exec-root"), _assembled(1)], ctx)
    starts = [
        r for r in out if r.get("kind") == "processing_call_start" and r.get("processing_type") == "context_assembly"
    ]
    ends = [r for r in out if r.get("kind") == "processing_call_end" and r.get("processing_type") == "context_assembly"]
    assert len(starts) == 1
    assert len(ends) == 1
    assert ends[0]["timestamp"] == starts[0]["timestamp"]
    assert "synthetic" not in ends[0]


def test_plugin_gives_every_agent_context_the_same_shared_registry() -> None:
    plugin = NativeObservabilityPlugin()
    first = plugin._ctx_for("a")
    second = plugin._ctx_for("b")
    assert first.shared is plugin.shared
    assert second.shared is plugin.shared
    assert plugin.context.shared is plugin.shared


def test_the_mas_call_end_names_the_root_visits_first_and_last_state() -> None:
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )

    shared: dict = {}
    transform = NativeObservabilityTransform()

    def run(ctx: TransformContext, record: dict) -> list[dict]:
        return transform.transform({"_source": "session", **record}, ctx=ctx)

    root = TransformContext(agent_id="boss", run_id="r", shared=shared)
    run(root, {"session_kind": "mas_call_start", "call_id": "mas-1"})
    run(root, {"session_kind": "user_input", "text": "plan", "call_id": "boss-exec", "turn_id": "u1"})
    run(root, {"session_kind": "agent_response", "text": "done", "finish_reason": "stop"})
    (end,) = run(
        TransformContext(agent_id="mas", run_id="r", shared=shared),
        {"session_kind": "mas_call_end", "call_id": "mas-1"},
    )
    assert end["state_in"] == "boss-exec#0" and end["state_in_content"] == "plan"
    assert end["state_out"] == "boss-exec#0" and end["state_out_content"] == "plan"


def test_a_mas_call_without_a_visit_names_no_state() -> None:
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )

    ctx = TransformContext(agent_id="mas", run_id="r")
    (end,) = NativeObservabilityTransform().transform(
        {"_source": "session", "session_kind": "mas_call_end", "call_id": "mas-1"}, ctx=ctx
    )
    assert "state_in" not in end and "state_out" not in end


def test_a_delegated_visit_waits_for_its_callers_tool_call_to_be_registered() -> None:
    """Each agent dispatches on its own thread: the callee's first event can arrive first."""
    import threading
    import time

    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
        register_tool_call,
    )

    shared: dict = {}
    callee = TransformContext(agent_id="helper", run_id="r", shared=shared)
    results: list[list[dict]] = []

    def run_callee() -> None:
        results.append(
            NativeObservabilityTransform().transform(
                {
                    "_source": "session",
                    "session_kind": "user_input",
                    "text": "task",
                    "call_id": "helper-exec",
                    "turn_id": "t1",
                    "parent_call_id": "tool-1",
                },
                ctx=callee,
            )
        )

    thread = threading.Thread(target=run_callee)
    thread.start()
    time.sleep(0.2)  # the callee is waiting
    register_tool_call(
        shared,
        "tool-1",
        {
            "agent_id": "boss",
            "exec_call_id": "boss-exec",
            "state_in": "boss-exec#2",
            "state_in_content": "request",
            "state_out": "boss-exec#3",
        },
    )
    thread.join(timeout=5)
    kinds = [r["kind"] for r in results[0]]
    assert kinds == ["processing_call_start", "processing_call_end", "execution_start"]
    assert results[0][0]["state_in"] == "boss-exec#2"


def test_a_spawned_visit_does_not_wait() -> None:
    import time

    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
        register_tool_call,
    )

    shared: dict = {}
    register_tool_call(shared, "spawn-1", None)
    ctx = TransformContext(agent_id="child", run_id="r", shared=shared)
    started = time.monotonic()
    out = NativeObservabilityTransform().transform(
        {
            "_source": "session",
            "session_kind": "user_input",
            "text": "t",
            "call_id": "c",
            "turn_id": "t1",
            "parent_call_id": "spawn-1",
        },
        ctx=ctx,
    )
    assert time.monotonic() - started < 1.0
    assert [r["kind"] for r in out] == ["execution_start"]

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native boundary-event handlers — one test per ObsEventKind dispatch.

None of these had a dedicated test before: they were only exercised
indirectly (if at all) through end-to-end plot/pipeline tests, which would
not catch a wrong dict key or a dropped field in any single handler.
"""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.observability.native.boundary_handlers import dispatch_boundary
from mas.library.standard.lib.observability.native.transform import TransformContext


def _ctx() -> TransformContext:
    return TransformContext(agent_id="moderator", run_id="run-1")


def test_hitl_request_surfaces_policy_name_regression() -> None:
    """Regression: EMIT_HITL_REQUEST's policy_name was only ever set as the
    top-level ObservabilityEvent field, never duplicated into payload like
    every other emit site that needs to survive the live TransitionEvent
    pipeline (boundary_dict_from_transition only forwards payload/attributes)
    — so it silently dropped through that pipeline while governance_decision
    and boundary_error (already fixed) didn't."""
    record = {
        "kind": "hitl.request",
        "correlation_id": 5,
        "payload": {
            "question": "Proceed with booking?",
            "policy_name": "sample_governance",
            "pending_schedule": [],
            "offered_actions": [],
        },
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert len(out) == 1
    assert out[0]["kind"] == "hitl_gate"
    assert out[0]["question"] == "Proceed with booking?"
    assert out[0]["policy_name"] == "sample_governance"


def test_hitl_resolve_carries_resolution_and_answer() -> None:
    record = {
        "kind": "hitl.resolve",
        "correlation_id": 5,
        "payload": {"resolution": "approved", "answer": "yes, proceed"},
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert out == [
        {
            "kind": "hitl_resolve",
            "agent_id": "moderator",
            "run_id": "run-1",
            "correlation_id": 5,
            "timestamp": out[0]["timestamp"],
            "resolution": "approved",
            "answer": "yes, proceed",
            "operator_id": "",
            "steering": "",
        }
    ]


def test_hitl_resolve_carries_operator_id_and_steering() -> None:
    record = {
        "kind": "hitl.resolve",
        "correlation_id": 5,
        "actor_id": "cli-operator",
        "payload": {
            "resolution": "SKIP",
            "answer": None,
            "operator_id": "cli-operator",
            "steering": "Do not calculate this yourself.",
        },
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert out[0]["operator_id"] == "cli-operator"
    assert out[0]["steering"] == "Do not calculate this yourself."
    assert out[0]["resolution"] == "SKIP"


def test_governance_decision_carries_reason_and_policy_name() -> None:
    record = {
        "kind": "governance.decision",
        "correlation_id": 2,
        "payload": {
            "hook": "egress",
            "checkpoint": "after",
            "decision": "BLOCK",
            "reason": "Restricted destination: Shadowmere",
            "policy_name": "forbidden-destination",
        },
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert len(out) == 1
    assert out[0]["kind"] == "governance_decision"
    assert out[0]["decision"] == "BLOCK"
    assert out[0]["reason"] == "Restricted destination: Shadowmere"
    assert out[0]["policy_name"] == "forbidden-destination"
    # checkpoint must reach the native trace: multilevel_trajectory/governance.py's
    # _collect_blocked_actions only recognizes a BLOCK as a ghost marker when
    # hook=="egress" and checkpoint=="after" — dropping this field here silently
    # made every real BLOCK/TERMINATE/SKIP/BLACKLIST decision invisible to the plot.
    assert out[0]["hook"] == "egress"
    assert out[0]["checkpoint"] == "after"


def test_governance_decision_with_no_decision_is_dropped() -> None:
    """The "before" checkpoint carries no decision yet (see GovEnvelopeMachine)
    — dispatch_boundary must not emit an empty/meaningless record for it."""
    record = {
        "kind": "governance.decision",
        "correlation_id": 2,
        "payload": {"hook": "egress", "checkpoint": "before", "decision": ""},
    }
    assert dispatch_boundary(record, ctx=_ctx()) == []


def test_boundary_error_carries_code_and_message() -> None:
    record = {
        "kind": "boundary.error",
        "correlation_id": 3,
        "payload": {
            "code": "RETRY_BUDGET_EXHAUSTED",
            "recoverable": False,
            "message": "max retries exceeded",
            "parent_call_id": "call-1",
        },
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert len(out) == 1
    assert out[0]["kind"] == "boundary_error"
    assert out[0]["code"] == "RETRY_BUDGET_EXHAUSTED"
    assert out[0]["recoverable"] is False
    assert out[0]["message"] == "max retries exceeded"
    assert out[0]["parent_call_id"] == "call-1"


def test_context_steer_carries_collect_id() -> None:
    record = {"kind": "context.steer", "correlation_id": 0, "payload": {"collect_id": "abc-123"}}
    out = dispatch_boundary(record, ctx=_ctx())
    assert out[0]["kind"] == "context_steer"
    assert out[0]["collect_id"] == "abc-123"


def test_context_assembled_emits_processing_span_and_context_event() -> None:
    record = {
        "kind": "context.assembled",
        "correlation_id": 8,
        "call_id": "llm-call-8",
        "parent_call_id": "exec-1",
        "payload": {
            "agent_id": "moderator",
            "messages": [
                {"role": "system", "content": "You are a planner."},
                {"role": "user", "content": "Find flights"},
            ],
            "segments": [],
            "total_tokens": 25,
            "message_count": 2,
            "tools": ["get_logs", "get_metrics"],
            "context_usage": {
                "context_window": 256000,
                "estimated_prompt_tokens": 250,
                "archive_view_id": "viewdeadbeef",
                "context_parts": [{"source": "skills", "pinned": True, "tokens": 80}],
            },
        },
    }
    out = dispatch_boundary(record, ctx=_ctx())
    kinds = [ev.get("kind") for ev in out]
    assert "processing_call_start" in kinds
    assert "context_assembled" in kinds
    assert "processing_call_end" in kinds

    assembled = next(ev for ev in out if ev.get("kind") == "context_assembled")
    assert assembled["tools"] == ["get_logs", "get_metrics"]
    assert assembled["context_usage"]["context_window"] == 256000
    assert assembled["context_usage"]["context_parts"][0]["pinned"] is True
    assert assembled["archive_view_id"] == "viewdeadbeef"

    pstart = next(ev for ev in out if ev.get("kind") == "processing_call_start")
    pend = next(ev for ev in out if ev.get("kind") == "processing_call_end")
    assert pstart["processing_name"] == "context assembly"
    assert pstart["context_operation"] == "PREPEND"
    assert pstart.get("parent_call_id") == "exec-1"
    assert pstart["call_id"] == pend["call_id"]
    assert pstart["archive_view_id"] == "viewdeadbeef"
    assert pend["archive_view_id"] == "viewdeadbeef"


def test_cache_lookup_projects_without_creating_llm_completion() -> None:
    record = {
        "kind": "cache.lookup",
        "correlation_id": 8,
        "call_id": "llm-call-8",
        "payload": {"op": "LLM_CALL", "cache_layer": "infra_llm_cache", "cache_status": "miss"},
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert len(out) == 1
    assert out[0]["kind"] == "cache_lookup"
    assert out[0]["cache_layer"] == "infra_llm_cache"
    assert out[0]["cache_status"] == "miss"


def test_cache_lookup_is_not_misreported_as_a_model_completion() -> None:
    record = {
        "kind": "cache.lookup",
        "correlation_id": 8,
        "call_id": "llm-call-8",
        "payload": {"op": "LLM_CALL", "cache_layer": "infra_llm_cache", "cache_status": "miss"},
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert out == [
        {
            "kind": "cache_lookup",
            "agent_id": "moderator",
            "run_id": "run-1",
            "correlation_id": 8,
            "call_id": "llm-call-8",
            "timestamp": out[0]["timestamp"],
            "cache_layer": "infra_llm_cache",
            "cache_status": "miss",
        }
    ]


def test_parallel_group_emits_one_aggregate_pair_at_group_end() -> None:
    ctx = _ctx()
    start_record = {
        "kind": "envelope.activity",
        "correlation_id": 11,
        "parent_call_id": "exec-1",
        "payload": {
            "activity": "parallel_group",
            "boundary": "start",
            "group_id": "grp-1",
            "tools": [
                {"correlation_id": 11, "tool_name": "t1", "arguments": {"x": 1}},
                {"correlation_id": 12, "tool_name": "t2", "arguments": {"y": 2}},
            ],
        },
    }
    end_record = {
        "kind": "envelope.activity",
        "correlation_id": 11,
        "parent_call_id": "exec-1",
        "payload": {
            "activity": "parallel_group",
            "boundary": "end",
            "group_id": "grp-1",
            "tools": [
                {"correlation_id": 11, "tool_name": "t1", "arguments": {"x": 1}},
                {"correlation_id": 12, "tool_name": "t2", "arguments": {"y": 2}},
            ],
        },
    }

    out_start = dispatch_boundary(start_record, ctx=ctx)
    out_end = dispatch_boundary(end_record, ctx=ctx)

    # The fork is a control marker: no ProcessingCall at the group start.
    assert [ev["kind"] for ev in out_start] == ["parallel_group_start"]
    assert not [ev for ev in out_start if ev["kind"].startswith("processing_call_")]

    # The join is the one real processing step: one aggregate pair at the group end.
    pstart = next(ev for ev in out_end if ev.get("kind") == "processing_call_start")
    pend = next(ev for ev in out_end if ev.get("kind") == "processing_call_end")
    assert pstart["processing_name"] == "aggregate"
    assert pend["processing_name"] == "aggregate"
    assert pstart["call_id"] == pend["call_id"]
    assert pstart["timestamp"] == pend["timestamp"]
    assert pstart.get("parent_call_id") == "exec-1"
    assert pend.get("parent_call_id") == "exec-1"
    assert "fork" not in pstart["input"] and "aggregate 2" not in pend["output"]


def test_wait_state_emits_plain_markers_without_processing_calls() -> None:
    ctx = _ctx()
    wait_start = {
        "kind": "envelope.activity",
        "correlation_id": 21,
        "parent_call_id": "exec-1",
        "payload": {
            "activity": "wait_state",
            "boundary": "start",
            "op": "TOOL_CALL",
            "tool_name": "delegate_to_schedule_agent",
            "wait_link_id": "delegate-21",
            "wait_role": "WAIT",
            "wait_scope": "delegation",
            "wait_note": "agent wait on tool call; tool wait on delegated reply",
        },
    }
    wait_end = {
        "kind": "envelope.activity",
        "correlation_id": 21,
        "parent_call_id": "exec-1",
        "payload": {
            "activity": "wait_state",
            "boundary": "end",
            "op": "TOOL_CALL",
            "tool_name": "delegate_to_schedule_agent",
            "wait_link_id": "delegate-21",
            "wait_role": "RESUME",
            "wait_scope": "delegation",
            "wait_note": "delegated reply received; resume caller",
        },
    }

    out_wait = dispatch_boundary(wait_start, ctx=ctx)
    out_resume = dispatch_boundary(wait_end, ctx=ctx)

    # wait and resume are control markers: plain records, no ProcessingCall companions.
    assert [ev["kind"] for ev in out_wait] == ["wait_state_start"]
    assert [ev["kind"] for ev in out_resume] == ["wait_state_end"]
    assert out_wait[0].get("parent_call_id") == "exec-1"
    assert out_resume[0].get("parent_call_id") == "exec-1"


def test_boundary_egress_ingress_catch_alls() -> None:
    egress = dispatch_boundary(
        {"kind": "boundary.egress", "correlation_id": 0, "payload": {"egress_kind": "NO_OP"}},
        ctx=_ctx(),
    )
    assert egress[0]["kind"] == "boundary_egress"
    assert egress[0]["egress_kind"] == "NO_OP"

    ingress = dispatch_boundary(
        {"kind": "boundary.ingress", "correlation_id": 0, "payload": {"ingress_kind": "TOOL_RESULT"}},
        ctx=_ctx(),
    )
    assert ingress[0]["kind"] == "boundary_ingress"
    assert ingress[0]["ingress_kind"] == "TOOL_RESULT"


def test_llm_call_start_takes_request_end_takes_return() -> None:
    start = dispatch_boundary(
        {
            "kind": "engine.io",
            "correlation_id": 5,
            "call_id": "llm-5",
            "payload": {
                "op": "LLM_CALL",
                "model": "openai/gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": ["search"],
                "max_tokens": 64,
                "temperature": 0.2,
            },
        },
        ctx=_ctx(),
    )
    assert start[0]["kind"] == "llm_call_start"
    assert start[0]["model"] == "openai/gpt-4o"
    assert start[0]["messages"] == [{"role": "user", "content": "hi"}]
    assert start[0]["tools"] == ["search"]
    assert start[0]["max_tokens"] == 64
    assert start[0]["temperature"] == 0.2

    end = dispatch_boundary(
        {
            "kind": "engine.io.return",
            "correlation_id": 5,
            "call_id": "llm-5",
            "payload": {
                "op": "LLM_CALL",
                "text": "",
                "next_step": "TOOL_CALL",
                "finish_reason": "tool_calls",
                "model": "should-not-copy",
                "tools": ["search"],
                "max_tokens": 64,
                "tool_calls": [{"toolName": "search", "arguments": {"q": "x"}}],
                "usage": {"total_tokens": 9},
            },
        },
        ctx=_ctx(),
    )
    assert end[0]["kind"] == "llm_call_end"
    assert "model" not in end[0]
    assert "tools" not in end[0]
    assert end[0]["max_tokens"] == 64  # the return carried a cap: it is the one actually sent
    assert end[0]["tool_calls"] == [{"toolName": "search", "arguments": {"q": "x"}}]
    assert end[0]["finish_reason"] == "tool_calls"
    assert end[0]["tokens_used"]["total_tokens"] == 9


def test_unknown_kind_is_dropped() -> None:
    assert dispatch_boundary({"kind": "not.a.real.kind", "payload": {}}, ctx=_ctx()) == []


def test_dispatch_boundary_uses_real_timestamp_not_export_time() -> None:
    """Regression: dispatch_boundary used to unconditionally stamp every
    boundary-sourced record with `time.time()` at whatever moment the async
    export pipeline happened to process it, discarding the real occurrence-
    time timestamp boundary_dict_from_transition threads through as
    record["timestamp"] (TransitionEvent.timestamp, captured synchronously
    when the event actually fired). That export-time lag scrambled real
    occurrence order across concurrent per-agent async workers — e.g. an
    agent's own tool_call_end could appear to land after that same agent's
    own execution_end, even though it truly happened first."""
    record = {
        "kind": "engine.io",
        "correlation_id": 7,
        "call_id": "tool-abc",
        "timestamp": 12345.6789,
        "payload": {"op": "TOOL_CALL", "tool_name": "lookup_schedule"},
    }
    out = dispatch_boundary(record, ctx=_ctx())
    assert out
    assert all(rec["timestamp"] == 12345.6789 for rec in out), out


def _assembled_record(cid: int, text: str) -> dict[str, Any]:
    return {
        "kind": "context.assembled",
        "correlation_id": cid,
        "payload": {
            "agent_id": "moderator",
            "messages": [{"role": "user", "content": text}],
            "segments": [],
            "total_tokens": 1,
            "message_count": 1,
        },
    }


def _proc_ids(out: list[dict[str, Any]]) -> list[str]:
    return [ev["call_id"] for ev in out if ev.get("kind") == "processing_call_start"]


def test_context_assembly_call_id_is_stable_per_correlation_id() -> None:
    ctx = _ctx()
    first = dispatch_boundary(_assembled_record(2, "a"), ctx=ctx)
    other = dispatch_boundary(_assembled_record(3, "a"), ctx=ctx)
    assert _proc_ids(first) != _proc_ids(other)
    start = next(ev for ev in first if ev["kind"] == "processing_call_start")
    end = next(ev for ev in first if ev["kind"] == "processing_call_end")
    assert start["call_id"] == end["call_id"]


def test_second_assembly_for_same_correlation_id_gets_distinct_call_id() -> None:
    ctx = _ctx()
    first = dispatch_boundary(_assembled_record(2, "a"), ctx=ctx)
    second = dispatch_boundary(_assembled_record(2, "b"), ctx=ctx)
    assert len(set(_proc_ids(first) + _proc_ids(second))) == 2
    ends = [ev["call_id"] for ev in first + second if ev["kind"] == "processing_call_end"]
    assert ends == _proc_ids(first) + _proc_ids(second)


def test_llm_request_fields_leave_out_unset_values() -> None:
    from mas.library.standard.lib.observability.native.boundary_handlers import _llm_request_fields

    assert _llm_request_fields({"op": "LLM_CALL"}) == {}
    assert _llm_request_fields({"messages": [], "tools": None, "model": ""}) == {}
    assert _llm_request_fields({"max_tokens": 0, "temperature": 0.0, "tools": []}) == {
        "max_tokens": 0,
        "temperature": 0.0,
        "tools": [],
    }


def test_an_error_return_ends_the_call_with_an_error_status() -> None:
    for op, kind in (("LLM_CALL", "llm_call_end"), ("TOOL_CALL", "tool_call_end")):
        end = dispatch_boundary(
            {
                "kind": "engine.io.return",
                "correlation_id": 9,
                "call_id": f"{op}-9",
                "payload": {"op": op, "text": "boom", "next_step": "STOP", "response_kind": "ERROR"},
            },
            ctx=_ctx(),
        )
        assert end[0]["kind"] == kind
        assert end[0]["status"] == "error"


def _engine(kind: str, cid: int, op: str, call_id: str, **payload: Any) -> dict:
    return {"kind": kind, "correlation_id": cid, "call_id": call_id, "payload": {"op": op, **payload}}


def _check(activity: str, boundary: str, cid: int, checked: str) -> dict:
    return {
        "kind": "envelope.activity",
        "correlation_id": cid,
        "parent_call_id": checked,
        "payload": {"activity": activity, "boundary": boundary, "op": "LLM_CALL"},
    }


def test_a_check_runs_in_the_visit_and_reads_the_state_of_the_call_it_checks() -> None:
    """authorize reads the State the checked call consumes, validate its output State; the
    bracket's parent is the visit, and the checked call is ``checked_call_id``."""
    ctx = _ctx()
    ctx.exec_call_id = "moderator-exec"
    ctx._state_current = "moderator-exec#0"
    ctx._state_contents["moderator-exec#0"] = "plan a trip"
    authorize = dispatch_boundary(_check("gov_authorize", "start", 7, "llm-1"), ctx=ctx)
    dispatch_boundary(_engine("engine.io", 7, "LLM_CALL", "llm-1"), ctx=ctx)
    dispatch_boundary(_engine("engine.io.return", 7, "LLM_CALL", "llm-1", response={"content": "ok"}), ctx=ctx)
    validate = dispatch_boundary(_check("gov_validate", "end", 7, "llm-1"), ctx=ctx)
    (start,) = authorize
    assert start["kind"] == "governance_authorize_start"
    assert (start["parent_call_id"], start["checked_call_id"]) == ("moderator-exec", "llm-1")
    assert start["state_in"] == start["state_out"] == "moderator-exec#0"
    assert start["state_in_content"] == start["state_out_content"] == "plan a trip"
    (end,) = validate
    llm_out = ctx._state_by_call["llm-1"]["state_out"]
    assert end["state_in"] == end["state_out"] == llm_out

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
    assert out == [{
        "kind": "hitl_resolve",
        "agent_id": "moderator", "run_id": "run-1", "correlation_id": 5,
        "timestamp": out[0]["timestamp"],
        "resolution": "approved", "answer": "yes, proceed",
    }]


def test_governance_decision_carries_reason_and_policy_name() -> None:
    record = {
        "kind": "governance.decision",
        "correlation_id": 2,
        "payload": {
            "hook": "egress", "checkpoint": "after", "decision": "BLOCK",
            "reason": "Restricted destination: Shadowmere", "policy_name": "forbidden-destination",
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
            "code": "RETRY_BUDGET_EXHAUSTED", "recoverable": False,
            "message": "max retries exceeded", "parent_call_id": "call-1",
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

    pstart = next(ev for ev in out if ev.get("kind") == "processing_call_start")
    pend = next(ev for ev in out if ev.get("kind") == "processing_call_end")
    assert pstart["processing_name"] == "context assembly"
    assert pstart["context_operation"] == "PREPEND"
    assert pstart.get("parent_call_id") == "exec-1"
    assert pstart["call_id"] == pend["call_id"]


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
    assert out == [{
        "kind": "cache_lookup",
        "agent_id": "moderator",
        "run_id": "run-1",
        "correlation_id": 8,
        "call_id": "llm-call-8",
        "timestamp": out[0]["timestamp"],
        "cache_layer": "infra_llm_cache",
        "cache_status": "miss",
    }]


def test_parallel_group_emits_processing_start_and_end_for_fork_and_aggregation() -> None:
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

    pstart = next(ev for ev in out_start if ev.get("kind") == "processing_call_start")
    pend = next(ev for ev in out_end if ev.get("kind") == "processing_call_end")
    assert pstart["processing_name"] == "parallel fork"
    assert pend["processing_name"] == "parallel aggregation"
    assert pstart["call_id"] == pend["call_id"]
    assert pstart.get("parent_call_id") == "exec-1"
    assert pend.get("parent_call_id") == "exec-1"
    assert "fork 2 parallel tool calls" in pstart.get("input", "")
    assert "aggregate 2 parallel tool results" in pend.get("output", "")


def test_wait_state_emits_processing_markers_for_wait_and_resume() -> None:
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

    wstart = next(ev for ev in out_wait if ev.get("kind") == "processing_call_start")
    wend = next(ev for ev in out_wait if ev.get("kind") == "processing_call_end")
    rstart = next(ev for ev in out_resume if ev.get("kind") == "processing_call_start")
    rend = next(ev for ev in out_resume if ev.get("kind") == "processing_call_end")

    assert wstart["processing_type"] == "wait_state"
    assert wstart["wait_role"] == "WAIT"
    assert wstart["wait_link_id"] == "delegate-21"
    assert wstart.get("parent_call_id") == "exec-1"
    assert wstart["call_id"] == wend["call_id"]

    assert rstart["processing_type"] == "wait_state"
    assert rstart["wait_role"] == "RESUME"
    assert rstart["wait_link_id"] == "delegate-21"
    assert rstart.get("parent_call_id") == "exec-1"
    assert rstart["call_id"] == rend["call_id"]


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

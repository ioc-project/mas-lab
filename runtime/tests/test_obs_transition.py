#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observability transition and subscriber tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.plugins import build_observability_plugins
from mas.runtime.boundary.obs.transition import TransitionEvent, boundary_event_to_transition
from mas.runtime.schema.observability import ObservabilityEvent, ObsEventKind, ObsPhase


@dataclass
class _CapturePlugin(ObservabilityPlugin):
    seen: list[TransitionEvent] = field(default_factory=list)

    def on_transition(self, event: TransitionEvent) -> None:
        self.seen.append(event)


def test_boundary_event_to_transition_llm_start() -> None:
    ev = ObservabilityEvent(
        seq=1,
        kind=ObsEventKind.ENGINE_IO,
        phase=ObsPhase.EXECUTE,
        machine_id="M_model",
        correlation_id=7,
        payload={"op": "LLM_CALL", "tool_name": ""},
    )
    t = boundary_event_to_transition(ev, agent_id="planner", run_id="run-abc")
    assert t.contract_id == "model"
    assert t.phase == "start"
    assert t.mealy_symbol == "LLM_CALL"
    assert t.call_id == "llm-7"


def test_operator_notifies_subscribers() -> None:
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.set_context(agent_id="agent-1", run_id="run-1")
    op.record_engine_io(correlation_id=3, op="TOOL_CALL", tool_name="delegate_to_telemetry")
    assert len(cap.seen) == 1
    assert cap.seen[0].contract_id == "tool"
    assert cap.seen[0].phase == "start"
    assert cap.seen[0].attributes.get("tool_name") == "delegate_to_telemetry"
    assert cap.seen[0].call_id is not None
    op.record_engine_io_return(correlation_id=3, op="TOOL_CALL", text="ok")
    assert len(cap.seen) == 2
    assert cap.seen[1].call_id == cap.seen[0].call_id
    assert cap.seen[0].parent_call_id is None


def test_operator_exports_upstream_correlation_context() -> None:
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.set_context(agent_id="agent-1", upstream_correlation_id=42)

    op.record_engine_io(correlation_id=3, op="LLM_CALL")

    assert cap.seen[-1].correlation_id == 3
    assert cap.seen[-1].upstream_correlation_id == 42


def test_operator_unsubscribe_stops_notifications() -> None:
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.unsubscribe(cap)
    op.unsubscribe(cap)
    op.set_context(agent_id="agent-1", run_id="run-1")
    op.record_engine_io(correlation_id=3, op="TOOL_CALL", tool_name="delegate_to_telemetry")
    assert cap.seen == []


def test_operator_call_stack_parent_call_id() -> None:
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")
    op.record_engine_io(correlation_id=1, op="LLM_CALL")
    assert cap.seen[-1].parent_call_id == "exec-001"
    op.record_engine_io_return(correlation_id=1, op="LLM_CALL", text="hi")
    assert cap.seen[-1].parent_call_id == "exec-001"
    op.pop_call_frame()


def test_bare_engine_io_fallback_frame_does_not_leak() -> None:
    """Regression: with no contract_call wrapping the call (the fallback path
    ENGINE_IO takes when enable_envelope_observability=False), the frame that
    fallback pushes must be popped by ENGINE_IO_RETURN — there is no
    contract_call/end coming to do it. Before the fix, every bare call leaked
    its frame permanently, so a later unrelated call's parent_call_id would
    resolve to a stale sibling instead of the true enclosing frame or None.
    """
    op = ObservabilityOperator()
    op.subscribe(_CapturePlugin())
    op.push_call_frame("exec-001")

    op.record_engine_io(correlation_id=1, op="LLM_CALL")
    op.record_engine_io_return(correlation_id=1, op="LLM_CALL", text="hi")
    assert op._frames.stack == ("exec-001",), op._frames.stack

    op.record_engine_io(correlation_id=2, op="LLM_CALL")
    op.record_engine_io_return(correlation_id=2, op="LLM_CALL", text="hi2")
    assert op._frames.stack == ("exec-001",), op._frames.stack

    op.pop_call_frame("exec-001")
    assert op._frames.stack == ()


def test_operator_parallel_calls_do_not_corrupt_each_others_parent() -> None:
    """N calls dispatched together (e.g. one assistant message with several
    parallel tool_calls) must all parent to the same enclosing frame, not to
    each other.

    This drives the operator directly with only the parallel_group
    bracket + bare ENGINE_IO events, as a minimal unit-level check of the
    parallel_group safety net alone. The real kernel/driver path ALSO steps
    contract_call/start for each tool before parallel_group/start ever fires
    (see test_parallel_tools.py::test_schedule_parallel_tools_egress_siblings_share_one_parent
    for that end-to-end regression, which is what begin_sibling_batch fixes).

    Regression for: each call's ENGINE_IO push read whatever was CURRENTLY on
    top of the shared stack — its own preceding sibling once that sibling had
    already pushed — so siblings chained onto one another instead of all
    parenting to the agent's execution frame.
    """
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")

    tools = [
        {"correlation_id": 2, "tool_name": "lookup_schedule"},
        {"correlation_id": 3, "tool_name": "lookup_schedule"},
        {"correlation_id": 4, "tool_name": "lookup_schedule"},
    ]
    op.record_parallel_group(boundary="start", group_id="pg-1", tools=tools)
    op.record_parallel_group(boundary="end", group_id="pg-1", tools=tools)
    for t in tools:
        op.record_engine_io(correlation_id=t["correlation_id"], op="TOOL_CALL")

    by_corr = {t.correlation_id: t for t in cap.seen if t.mealy_symbol == "TOOL_CALL"}
    for t in tools:
        assert by_corr[t["correlation_id"]].parent_call_id == "exec-001", by_corr[t["correlation_id"]]

    for t in tools:
        op.record_engine_io_return(correlation_id=t["correlation_id"], op="TOOL_CALL", text="ok")
    op.pop_call_frame()


def test_operator_record_session_notifies_subscribers() -> None:
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.set_context(agent_id="planner", run_id="run-1")
    op.record_session("user_input", text="hi", call_id="t1-exec")
    assert len(cap.seen) == 1
    assert cap.seen[0].boundary_kind == "session"
    assert cap.seen[0].mealy_symbol == "user_input"


def test_native_plugin_contract_call_alone_does_not_emit_llm_call(tmp_path) -> None:
    """A bare contract_call envelope activity (no paired ENGINE_IO) must NOT
    produce llm_call_start/end — that translation used to duplicate the
    llm_call_start/end that ENGINE_IO/ENGINE_IO_RETURN already produce for the
    same call, with no shared dedup key between the two, corrupting the
    trajectory (a phantom zero-content "call" record)."""
    from mas.runtime.boundary.obs.binding import ObservabilityBinding

    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="planner")
    assert plugins
    op = ObservabilityOperator()
    for plugin in plugins:
        op.subscribe(plugin)
    op.set_context(agent_id="planner", run_id="run-test")
    op.record_envelope_activity(
        symbol="CONTRACT_START",
        activity="contract_call",
        boundary="start",
        phase=ObsPhase.REQUEST,
        correlation_id=5,
        machine_id="M_obs",
        payload={"op": "LLM_CALL"},
    )
    op.drain_plugin_queue()
    events_path = tmp_path / "events.jsonl"
    assert events_path.stat().st_size > 0
    assert not any("llm_call" in line for line in events_path.read_text().splitlines())


def test_native_plugin_engine_io_is_sole_source_of_llm_call(tmp_path) -> None:
    """The full envelope sequence (contract_call start -> engine_io ->
    engine_io_return -> contract_call end) must produce exactly one
    llm_call_start and one llm_call_end — ENGINE_IO/ENGINE_IO_RETURN are the
    only source; contract_call brackets the call for parent tracking only."""
    from mas.runtime.boundary.obs.binding import ObservabilityBinding

    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="planner")
    op = ObservabilityOperator()
    for plugin in plugins:
        op.subscribe(plugin)
    op.set_context(agent_id="planner", run_id="run-test")
    op.record_envelope_activity(
        symbol="CONTRACT_START",
        activity="contract_call",
        boundary="start",
        phase=ObsPhase.REQUEST,
        correlation_id=5,
        payload={"op": "LLM_CALL"},
    )
    op.record_engine_io(correlation_id=5, op="LLM_CALL")
    op.record_engine_io_return(correlation_id=5, op="LLM_CALL", text="hi")
    op.record_envelope_activity(
        symbol="CONTRACT_END",
        activity="contract_call",
        boundary="end",
        phase=ObsPhase.RESULT,
        correlation_id=5,
        payload={"op": "LLM_CALL"},
    )
    op.drain_plugin_queue()
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    starts = [line for line in lines if '"kind": "llm_call_start"' in line]
    ends = [line for line in lines if '"kind": "llm_call_end"' in line]
    assert len(starts) == 1, starts
    assert len(ends) == 1, ends


def test_contract_call_id_is_not_the_inner_llm_call_id() -> None:
    """A span keyed only by call_id must not let contract_call_end overwrite llm_call_end."""
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")
    op.record_envelope_activity(
        symbol="CONTRACT_START",
        activity="contract_call",
        boundary="start",
        phase=ObsPhase.REQUEST,
        correlation_id=5,
        payload={"op": "LLM_CALL"},
    )
    op.record_engine_io(correlation_id=5, op="LLM_CALL")
    op.record_engine_io_return(correlation_id=5, op="LLM_CALL", text="hi", model="gpt-test")
    op.record_envelope_activity(
        symbol="CONTRACT_END",
        activity="contract_call",
        boundary="end",
        phase=ObsPhase.RESULT,
        correlation_id=5,
        payload={"op": "LLM_CALL"},
    )
    by_symbol = {(event.mealy_symbol, event.phase): event for event in cap.seen}
    wrapper_start = by_symbol[("CONTRACT_START", "start")]
    wrapper_end = by_symbol[("CONTRACT_END", "end")]
    llm_start = by_symbol[("LLM_CALL", "start")]
    llm_end = by_symbol[("LLM_CALL", "end")]
    assert wrapper_start.call_id == wrapper_end.call_id
    assert llm_start.call_id == llm_end.call_id
    assert wrapper_start.call_id != llm_start.call_id
    assert llm_start.parent_call_id == wrapper_start.parent_call_id == "exec-001"


def test_governance_decision_resolves_real_call_id_and_parent() -> None:
    """A governance_decision carrying its own `op` must resolve to the SAME
    call_id the call's own ENGINE_IO/ENGINE_IO_RETURN pair uses (via
    _interval_call_id), and its parent must be the enclosing frame — not the
    non-unique f"call-{correlation_id}" fallback with a null parent."""
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")

    op.record_governance_decision(
        hook="egress",
        phase="before",
        correlation_id=9,
        op="TOOL_CALL",
    )
    gov_before = cap.seen[-1]
    assert gov_before.parent_call_id == "exec-001", gov_before

    op.record_engine_io(correlation_id=9, op="TOOL_CALL", tool_name="lookup_schedule")
    tool_start = cap.seen[-1]
    assert tool_start.call_id == gov_before.call_id, (tool_start, gov_before)

    op.record_engine_io_return(correlation_id=9, op="TOOL_CALL", text="ok")

    op.record_governance_decision(
        hook="ingress",
        phase="after",
        correlation_id=9,
        op="TOOL_CALL",
    )
    gov_after = cap.seen[-1]
    assert gov_after.call_id == tool_start.call_id, (gov_after, tool_start)
    assert gov_after.parent_call_id == "exec-001", gov_after

    op.pop_call_frame()


def test_governance_decision_call_id_does_not_collide_across_agents() -> None:
    """Two different agents' own ObservabilityOperator instances each start
    correlation_id at 1 — a governance_decision's call_id must not collide
    just because two unrelated calls happen to share the same correlation_id
    across agents (the old f"call-{correlation_id}" fallback did)."""
    op_a = ObservabilityOperator()
    cap_a = _CapturePlugin()
    op_a.subscribe(cap_a)
    op_a.record_governance_decision(hook="egress", phase="before", correlation_id=2, op="TOOL_CALL")

    op_b = ObservabilityOperator()
    cap_b = _CapturePlugin()
    op_b.subscribe(cap_b)
    op_b.record_governance_decision(hook="egress", phase="before", correlation_id=2, op="TOOL_CALL")

    assert cap_a.seen[-1].call_id != cap_b.seen[-1].call_id


def test_hitl_request_and_resolve_share_call_id_with_real_parent() -> None:
    """HITL_REQUEST/HITL_RESOLVE must resolve to the SAME real call_id (they
    share one request_id/correlation_id across the round-trip) and a real
    parent (the frame active when the gate fired), not a null parent."""
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")

    ev_request = ObservabilityEvent(
        seq=1,
        kind=ObsEventKind.HITL_REQUEST,
        phase=ObsPhase.AUTHZ,
        machine_id="M_gov",
        correlation_id=4,
        payload={"question": "proceed?"},
    )
    call_id_req, parent_req = op._resolve_transition_ids(ev_request)
    assert parent_req == "exec-001"

    ev_resolve = ObservabilityEvent(
        seq=2,
        kind=ObsEventKind.HITL_RESOLVE,
        phase=ObsPhase.AUTHZ,
        machine_id="M_gov",
        correlation_id=4,
        payload={"resolution": "approve"},
    )
    call_id_res, parent_res = op._resolve_transition_ids(ev_resolve)
    assert call_id_res == call_id_req
    assert parent_res == "exec-001"

    op.pop_call_frame()


def test_hitl_request_call_id_does_not_collide_across_agents() -> None:
    """request_id is just another per-agent RunLedger counter — two agents'
    own HITL requests must not collide on the same call_id."""
    op_a = ObservabilityOperator()
    op_b = ObservabilityOperator()

    def ev() -> ObservabilityEvent:
        return ObservabilityEvent(
            seq=1,
            kind=ObsEventKind.HITL_REQUEST,
            phase=ObsPhase.AUTHZ,
            machine_id="M_gov",
            correlation_id=3,
            payload={},
        )

    call_id_a, _ = op_a._resolve_transition_ids(ev())
    call_id_b, _ = op_b._resolve_transition_ids(ev())
    assert call_id_a != call_id_b


def test_operator_async_plugins_isolate_failures() -> None:
    class _BrokenPlugin(ObservabilityPlugin):
        def on_transition(self, event: TransitionEvent) -> None:
            raise RuntimeError("plugin boom")

    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(_BrokenPlugin())
    op.subscribe(cap)
    op.enable_async_plugins()
    op.record_engine_io(correlation_id=1, op="LLM_CALL")
    op.drain_plugin_queue()
    assert len(cap.seen) == 1


def test_native_transform_memory_and_parallel_kinds() -> None:
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )

    transform = NativeObservabilityTransform()
    ctx = TransformContext(agent_id="a1", run_id="r1")
    mem_start = transform.transform(
        {
            "_source": "boundary",
            "kind": ObsEventKind.ENGINE_IO.value,
            "correlation_id": 9,
            "payload": {"op": "MEMORY_OP"},
        },
        ctx=ctx,
    )
    assert any(r.get("kind") == "memory_call_start" for r in mem_start)

    parallel = transform.transform(
        {
            "_source": "boundary",
            "kind": ObsEventKind.ENVELOPE_ACTIVITY.value,
            "correlation_id": 10,
            "payload": {
                "activity": "parallel_group",
                "boundary": "start",
                "group_id": "grp-1",
                "tools": [{"tool_name": "t1"}, {"tool_name": "t2"}],
            },
        },
        ctx=ctx,
    )
    assert any(r.get("kind") == "parallel_group_start" for r in parallel)


def test_native_transform_wait_state_emits_processing_wait_and_resume() -> None:
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )

    transform = NativeObservabilityTransform()
    ctx = TransformContext(agent_id="a1", run_id="r1")
    wait_start = transform.transform(
        {
            "_source": "boundary",
            "kind": ObsEventKind.ENVELOPE_ACTIVITY.value,
            "correlation_id": 33,
            "payload": {
                "activity": "wait_state",
                "boundary": "start",
                "op": "TOOL_CALL",
                "tool_name": "delegate_to_worker",
                "wait_link_id": "delegate-33",
                "wait_role": "WAIT",
                "wait_scope": "delegation",
            },
        },
        ctx=ctx,
    )
    wait_end = transform.transform(
        {
            "_source": "boundary",
            "kind": ObsEventKind.ENVELOPE_ACTIVITY.value,
            "correlation_id": 33,
            "payload": {
                "activity": "wait_state",
                "boundary": "end",
                "op": "TOOL_CALL",
                "tool_name": "delegate_to_worker",
                "wait_link_id": "delegate-33",
                "wait_role": "RESUME",
                "wait_scope": "delegation",
            },
        },
        ctx=ctx,
    )

    assert any(r.get("kind") == "wait_state_start" for r in wait_start)
    assert any(r.get("kind") == "wait_state_end" for r in wait_end)
    assert any(
        r.get("kind") == "processing_call_start"
        and r.get("processing_type") == "wait_state"
        and r.get("wait_role") == "WAIT"
        for r in wait_start
    )
    assert any(
        r.get("kind") == "processing_call_start"
        and r.get("processing_type") == "wait_state"
        and r.get("wait_role") == "RESUME"
        for r in wait_end
    )


def test_context_mutation_resolves_real_parent_for_1to1_op() -> None:
    """A context_mutation carrying its own `op` (the wm_append case, always
    tied to one specific LLM_CALL/TOOL_CALL) is a CHILD of that call (its
    result being committed to working memory), so its real parent must be
    that call's own call_id — not null, and not that call's own parent. A
    mutation without `op` (turn/session-scoped: turn_start, wm_clear, ...)
    correctly resolves no parent at all."""
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")

    op.record_engine_io(correlation_id=4, op="TOOL_CALL", tool_name="lookup_schedule")
    tool_start = cap.seen[-1]
    op.record_engine_io_return(correlation_id=4, op="TOOL_CALL", text="ok")

    op.record_context_mutation(
        action="wm_append",
        correlation_id=4,
        role="tool",
        op="TOOL_CALL",
    )
    mutation = cap.seen[-1]
    assert mutation.parent_call_id == tool_start.call_id, (mutation, tool_start)

    # No op (turn/session-scoped) — no parent to guess, correctly none.
    op.record_context_mutation(action="turn_start", correlation_id=0)
    turn_event = cap.seen[-1]
    assert turn_event.parent_call_id is None, turn_event

    op.pop_call_frame()


def test_context_assembled_shares_call_id_with_its_llm_call() -> None:
    """context_assembled always fires synchronously inside one specific
    LLM_CALL dispatch — it must resolve to the SAME call_id that call's own
    llm_call_start/end use, not a synthetic id needing later reconstruction."""
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.push_call_frame("exec-001")

    op.record_context_assembled(correlation_id=6, messages=[{"role": "user", "content": "hi"}])
    assembled = cap.seen[-1]
    assert assembled.parent_call_id == "exec-001", assembled

    op.record_engine_io(correlation_id=6, op="LLM_CALL")
    llm_start = cap.seen[-1]
    assert llm_start.call_id == assembled.call_id, (llm_start, assembled)

    op.pop_call_frame()

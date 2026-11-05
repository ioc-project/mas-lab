#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kernel driver loop integration."""

from dataclasses import dataclass, field

from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.transition import TransitionEvent
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimMode, SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _CapturePlugin(ObservabilityPlugin):
    seen: list[TransitionEvent] = field(default_factory=list)

    def on_transition(self, event: TransitionEvent) -> None:
        self.seen.append(event)


def test_runtime_instance_run_user_text_produces_client_response():
    engine = SimulatedEngine(
        script={
            1: EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="hello back",
            )
        }
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    inst.capture_session_baseline()
    trace = inst.run_user_text("hello")
    assert trace.client_responses or trace.steps


def test_runtime_instance_pause_and_resume():
    inst = RuntimeInstance.from_parts()
    inst.capture_session_baseline()
    pause_trace = inst.pause(reason="operator")
    assert pause_trace is not None
    resume_trace = inst.resume()
    assert resume_trace is not None


def test_runtime_instance_reset_after_baseline():
    inst = RuntimeInstance.from_parts()
    inst.capture_session_baseline()
    inst.run_user_text("turn one")
    inst.reset_session()
    assert inst.kernel.q.dp in {"IDLE", "AWAITING_INGRESS", "EGRESS_PENDING", "EVALUATING"}


def test_driver_attaches_own_call_id_to_tool_call_invocation():
    """driver.py resolves this TOOL_CALL's own call_id (via
    ObservabilityOperator.call_id_for — already assigned by the
    CONTRACT_EXECUTE observability step just before the engine is invoked)
    and attaches it onto the InvokeEngineIo passed to engine.invoke(). This
    is the mechanism a delegate_to_X tool call relies on to forward its own
    identity as the delegate's caller_call_id (see InvokeEngineIo.call_id
    and execute_engine_tool) — no closure capture, no timestamp heuristics,
    a real field resolved once at the true origin."""
    seen_ios: list = []

    class _RecordingEngine(SimulatedEngine):
        def invoke(self, io):
            seen_ios.append(io)
            return super().invoke(io)

    engine = _RecordingEngine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("some_tool", {"x": 1}),
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    inst.capture_session_baseline()
    inst.run_user_text("trigger a tool call")

    tool_call_ios = [io for io in seen_ios if io.op == "TOOL_CALL"]
    assert tool_call_ios, "expected at least one TOOL_CALL dispatch"
    assert all(io.call_id for io in tool_call_ios), tool_call_ios


def test_driver_attaches_parent_call_id_to_tool_call_invocation():
    """The tool call's own parent_call_id must also be attached (see
    InvokeEngineIo.parent_call_id and ObservabilityOperator.parent_call_id_for)
    — the enclosing LLM_CALL that decided to make this tool call, resolved
    once at the true origin instead of every consumer reconstructing it from
    an observability call-frame stack of its own."""
    seen_ios: list = []

    class _RecordingEngine(SimulatedEngine):
        def invoke(self, io):
            seen_ios.append(io)
            return super().invoke(io)

    engine = _RecordingEngine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("some_tool", {"x": 1}),
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    # run_user_text only pushes the turn's own enclosing exec frame (the
    # true parent of every LLM_CALL/TOOL_CALL opened during that turn) when
    # an observability plugin set is attached — force it on for this test
    # without needing a full plugin wiring. call_id/parent_call_id resolution
    # itself (_resolve_transition_ids) is also gated on having at least one
    # subscriber (see ObservabilityOperator._notify_subscribers).
    inst.obs_plugin_set = object()
    inst.driver.observability.subscribe(_CapturePlugin())
    inst.capture_session_baseline()
    inst.run_user_text("trigger a tool call")

    llm_call_ios = [io for io in seen_ios if io.op == "LLM_CALL"]
    tool_call_ios = [io for io in seen_ios if io.op == "TOOL_CALL"]
    assert llm_call_ios and tool_call_ios
    assert all(io.parent_call_id for io in tool_call_ios), tool_call_ios
    # LLM_CALL and TOOL_CALL are both opened directly under the turn's own
    # exec frame (siblings, not nested) — they share the same parent.
    assert {io.parent_call_id for io in tool_call_ios} == {llm_call_ios[0].parent_call_id}


def test_driver_parallel_tool_call_siblings_share_one_parent_call_id():
    """End-to-end regression for the sibling-batch case (see
    schedule_parallel_tools_egress/begin_sibling_batch): N tool calls
    scheduled together in one agentic step must all resolve the SAME
    parent_call_id — the enclosing LLM_CALL — never chained onto each other.
    """
    from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec

    seen_ios: list = []

    class _ParallelEngine(SimulatedEngine):
        def invoke(self, io):
            seen_ios.append(io)
            if io.op == "LLM_CALL" and io.correlation_id == 1:
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="PARALLEL_TOOL_CALLS",
                    parallel_tools=(
                        ToolCallSpec(tool_name="tool_a", tool_arguments={}),
                        ToolCallSpec(tool_name="tool_b", tool_arguments={}),
                        ToolCallSpec(tool_name="tool_c", tool_arguments={}),
                    ),
                )
            if io.op == "LLM_CALL":
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="STOP",
                    text="done",
                )
            if io.op == "TOOL_CALL":
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="TOOL_RESULT",
                    next_step="STOP",
                    text="ok",
                )
            return EngineIoReturn(correlation_id=io.correlation_id, response_kind="ERROR", next_step="STOP")

    engine = _ParallelEngine()
    inst = RuntimeInstance.from_parts(engine=engine)
    inst.obs_plugin_set = object()
    inst.driver.observability.subscribe(_CapturePlugin())
    inst.capture_session_baseline()
    inst.run_user_text("trigger parallel tool calls")

    tool_call_ios = [io for io in seen_ios if io.op == "TOOL_CALL"]
    assert len(tool_call_ios) == 3, tool_call_ios
    assert all(io.parent_call_id for io in tool_call_ios), tool_call_ios
    # All three siblings share the SAME parent — the turn's own enclosing
    # exec frame — never chained onto a preceding sibling's own call_id.
    assert len({io.parent_call_id for io in tool_call_ios}) == 1
    # And, just as importantly, no sibling's parent is another sibling's own
    # call_id (the exact bug begin_sibling_batch fixes).
    sibling_call_ids = {io.call_id for io in tool_call_ios}
    assert not ({io.parent_call_id for io in tool_call_ios} & sibling_call_ids)


def test_driver_records_real_tool_call_end_matching_start_call_id():
    """Regression: ObsEnvelopeMachine's OBSERVABILITY_POST_EXECUTE branch used
    to be permanently shadowed by an earlier elif matching the same symbol
    (see obs_envelope.py), so record_engine_io_return never fired for
    TOOL_CALL — tool_call_end was never emitted by any real trace, for any
    tool call. This drives a full kernel/driver turn end-to-end and asserts
    the tool call's own ENGINE_IO_RETURN now fires, with the SAME call_id its
    own ENGINE_IO start used."""
    engine = SimulatedEngine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("some_tool", {"x": 1}),
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    cap = _CapturePlugin()
    inst.driver.observability.subscribe(cap)
    inst.capture_session_baseline()
    inst.run_user_text("trigger a tool call")

    starts = [e for e in cap.seen if e.contract_id == "tool" and e.phase == "start"]
    ends = [e for e in cap.seen if e.contract_id == "tool" and e.phase == "end"]
    assert starts, "expected at least one tool call start"
    assert ends, "expected at least one tool call end (tool_call_end) — was never emitted before the fix"
    assert {e.call_id for e in starts} == {e.call_id for e in ends}


def test_driver_llm_call_gets_exactly_one_end_event():
    """Regression guard: fixing the ObsEnvelopeMachine shadowing bug must not
    double-record llm_call_end. driver.py used to have its own hand-written
    special case (record_engine_llm_return) that fired for every LLM_CALL
    return, independent of the (permanently shadowed) envelope-machine path —
    once that path actually runs, both would fire unless the hand-written
    special case is removed."""
    inst = RuntimeInstance.from_parts()
    cap = _CapturePlugin()
    inst.driver.observability.subscribe(cap)
    inst.capture_session_baseline()
    inst.run_user_text("hello")

    llm_ends = [e for e in cap.seen if e.contract_id == "model" and e.phase == "end"]
    by_call_id: dict = {}
    for e in llm_ends:
        by_call_id.setdefault(e.call_id, 0)
        by_call_id[e.call_id] += 1
    duplicated = {cid: n for cid, n in by_call_id.items() if n > 1}
    assert not duplicated, duplicated


def test_driver_records_real_memory_call_end_matching_start_call_id():
    """Regression: apply_engine_io_return (ingress_step.py) only distinguished
    TOOL_CALL vs LLM_CALL — a MEMORY_OP return fell through to "LLM_CALL",
    resolving via a DIFFERENT (correlation_id, op)-keyed call_id than its own
    ENGINE_IO start used, permanently orphaning memory_call_end from
    memory_call_start. Drives a full turn through the react design pattern's
    memory-egress path (SimMode.MEMORY_PROBE's "DELEGATE" next_step)."""
    engine = SimulatedEngine(sim_mode=SimMode.MEMORY_PROBE)
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(enable_memory_egress=True),
    )
    cap = _CapturePlugin()
    inst.driver.observability.subscribe(cap)
    inst.capture_session_baseline()
    inst.run_user_text("trigger a memory op")

    starts = [e for e in cap.seen if e.contract_id == "memory" and e.phase == "start"]
    ends = [e for e in cap.seen if e.contract_id == "memory" and e.phase == "end"]
    assert starts, "expected at least one memory op start"
    assert ends, "expected at least one memory_call_end — was misclassified as LLM_CALL before the fix"
    assert {e.call_id for e in starts} == {e.call_id for e in ends}


class _RaiseOnTool:
    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "LLM_CALL":
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name="get_deployments",
                tool_arguments={"service": "payment-service"},
            )
        raise RuntimeError("Tool 'get_deployments' not found in manifest or overlays")


def test_driver_records_tool_call_end_when_engine_raises() -> None:
    """A tool-call start is always paired with an end, including when invoke
    raises. The failure text is the end event's output.
    """
    inst = RuntimeInstance.from_parts(engine=_RaiseOnTool())
    cap = _CapturePlugin()
    inst.driver.observability.subscribe(cap)
    inst.capture_session_baseline()
    inst.run_user_text("investigate payment-service")

    starts = [e for e in cap.seen if e.contract_id == "tool" and e.phase == "start"]
    ends = [e for e in cap.seen if e.contract_id == "tool" and e.phase == "end"]
    assert starts
    assert ends
    assert {e.call_id for e in starts} == {e.call_id for e in ends}
    error_ends = [
        e
        for e in ends
        if e.attributes.get("response_kind") == "ERROR" or "get_deployments" in str(e.attributes.get("text") or "")
    ]
    assert error_ends, ends
    assert "get_deployments" in str(error_ends[-1].attributes.get("text") or "")

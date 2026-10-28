# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Peer steer against every design pattern, using real session objects."""

from __future__ import annotations

import asyncio

import pytest
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.library.standard.lib.comm.payload import label_untrusted_peer
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.design_patterns.cot import CotPlugin
from mas.library.standard.plugins.design_patterns.introspection import IntrospectionPlugin
from mas.library.standard.plugins.design_patterns.linear import DeterministicLinearPlugin
from mas.library.standard.plugins.design_patterns.parallel import DeterministicParallelPlugin
from mas.library.standard.plugins.design_patterns.plan_execute import PlanExecutePlugin
from mas.library.standard.plugins.design_patterns.react import ReactPlugin
from mas.library.standard.plugins.design_patterns.single import DeterministicSingleAgentPlugin
from mas.library.standard.plugins.design_patterns.single_pass import SinglePassPlugin
from mas.library.standard.plugins.design_patterns.tree_of_thoughts import TreeOfThoughtsPlugin
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext
from mas.runtime.boundary.control.contract import ControlCapability
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.inflight_llm import has_inflight
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.egress import RequestCtxAssembly
from mas.runtime.schema.ingress import EngineIoReturn, OperatorSteerReceived, UserInputReceived

LLM_PATTERNS = (
    "react@v1",
    "single_pass@v1",
    "cot@v1",
    "tree_of_thoughts@v1",
    "plan_execute@v1",
    "introspection@v1",
)
DETERMINISTIC_PATTERNS = (
    "deterministic_linear@v1",
    "deterministic_parallel@v1",
    "deterministic_single@v1",
)
ALL_PATTERNS = LLM_PATTERNS + DETERMINISTIC_PATTERNS
_PATTERN_PLUGINS = {
    "react@v1": ReactPlugin,
    "single_pass@v1": SinglePassPlugin,
    "cot@v1": CotPlugin,
    "tree_of_thoughts@v1": TreeOfThoughtsPlugin,
    "plan_execute@v1": PlanExecutePlugin,
    "introspection@v1": IntrospectionPlugin,
    "deterministic_linear@v1": DeterministicLinearPlugin,
    "deterministic_parallel@v1": DeterministicParallelPlugin,
    "deterministic_single@v1": DeterministicSingleAgentPlugin,
}


class _Display:
    def on_system(self, *_a: object, **_k: object) -> None:
        return None

    def on_user(self, *_a: object, **_k: object) -> None:
        return None

    def on_agent(self, *_a: object, **_k: object) -> None:
        return None

    def on_turn_error(self, *_a: object, **_k: object) -> None:
        return None

    def on_hitl_request(self, *_a: object, **_k: object) -> None:
        return None


def _live_session(pattern: str, session_id: str, *, engine: object | None = None):
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(),
        engine=engine or SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="ok"),
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = _PATTERN_PLUGINS[pattern]()
    instance.driver.session_id = session_id
    controller = SessionController(instance=instance, display=_Display())
    manager = SessionManager()
    session = manager.create(
        instance,
        controller,
        {"name": "agent", "spec": {"design_pattern": {"type": pattern}}},
        session_id=session_id,
    )
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": session_id},
    )
    return manager, session, admin, comm


@pytest.mark.parametrize("pattern", ALL_PATTERNS)
def test_peer_after_steer_is_applied_or_queued(pattern: str) -> None:
    _manager, session, admin, comm = _live_session(pattern, f"s-{pattern}")
    labelled = label_untrusted_peer("nudge", sender="planner", intent="steer")
    assert comm.send("worker", "nudge", context=SendContext(intent="steer")) == "delivered"
    steer_events = [
        event
        for event in admin.events
        if event.method == "steer" and event.session_id == session.session_id
    ]
    assert steer_events
    assert steer_events[0].actor == "planner"
    assert steer_events[0].surface == "peer"
    queued = admin.inspect_queue(session.session_id).items
    assert queued
    assert queued[0].text == labelled
    assert queued[0].source == "peer"
    result = session.controller.run_turn("", auto_hitl=False)
    kinds = [step.ingress.__class__.__name__ for step in result.trace.steps]
    assert "UserInputReceived" in kinds
    assembled = [
        item
        for step in result.trace.steps
        for item in step.egress
        if isinstance(item, RequestCtxAssembly)
    ]
    assert assembled
    first_user = next(
        step.ingress for step in result.trace.steps if isinstance(step.ingress, UserInputReceived)
    )
    assert first_user.sender == "planner"
    assert first_user.intent == "steer"
    assert first_user.source == "peer"
    assert labelled in first_user.text


@pytest.mark.parametrize("pattern", DETERMINISTIC_PATTERNS)
def test_deterministic_preempt_is_refused_not_dropped(pattern: str) -> None:
    _manager, session, _admin, comm = _live_session(pattern, f"d-{pattern}")
    with pytest.raises(AgentCommError, match="does not support steer mode 'preempt'"):
        comm.send("worker", "cut", context=SendContext(intent="steer", mode="preempt"))
    with pytest.raises(AgentCommError, match="does not support steer mode 'replace'"):
        comm.send("worker", "cut", context=SendContext(intent="steer", mode="replace"))
    assert session.controller.turn_queue.peek(session.session_id) == []


@pytest.mark.parametrize("pattern", LLM_PATTERNS)
def test_llm_pattern_supports_preempt_replace_and_after(pattern: str) -> None:
    _manager, session, admin, _comm = _live_session(pattern, f"m-{pattern}")
    modes = admin.steer_modes(session.session_id)
    assert modes == frozenset({"after", "preempt", "replace"})


class _PartialHangEngine:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.calls = 0
        self.kept = ""

    async def ainvoke(self, io):
        from mas.runtime.engine.inflight_llm import append_partial
        from mas.runtime.schema.ingress import EngineIoReturn

        self.calls += 1
        if self.calls == 1:
            append_partial(self.session_id, "Hello ")
            await asyncio.sleep(30)
            raise AssertionError("should have been preempted")
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text="steered",
        )


@pytest.mark.asyncio
async def test_peer_preempt_keeps_partials_and_applies_steer() -> None:
    engine = _PartialHangEngine("stream-react")
    _manager, session, admin, comm = _live_session(
        "react@v1", "stream-react", engine=engine
    )
    labelled = label_untrusted_peer("Answer Lyon.", sender="planner", intent="steer")
    task = asyncio.create_task(session.controller.arun_turn("hello", auto_hitl=False))
    for _ in range(80):
        await asyncio.sleep(0.02)
        if has_inflight("stream-react"):
            break
    assert has_inflight("stream-react")
    seen: list[object] = []
    assert (
        comm.send(
            "worker",
            "Answer Lyon.",
            context=SendContext(intent="steer", mode="preempt"),
        )
        == "delivered"
    )
    result = await asyncio.wait_for(task, timeout=5)
    for step in result.trace.steps:
        if isinstance(step.ingress, OperatorSteerReceived):
            seen.append(step.ingress)
    assert seen
    event = seen[0]
    assert event.sender == "planner"
    assert event.intent == "steer"
    assert event.source == "peer"
    assert event.context_text == labelled
    assembled = [
        item
        for step in result.trace.steps
        for item in step.egress
        if isinstance(item, RequestCtxAssembly)
    ]
    assert assembled
    assert any(item.operator_context == labelled for item in assembled)
    preempted = next(
        step.ingress
        for step in result.trace.steps
        if isinstance(step.ingress, EngineIoReturn) and step.ingress.finish_reason == "preempted"
    )
    assert preempted.text == "Hello "
    events = [item for item in admin.events if item.method == "steer"]
    assert events[-1].actor == "planner"
    assert events[-1].surface == "peer"
    assert engine.calls >= 1

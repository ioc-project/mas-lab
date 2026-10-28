# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Peer steer through a real SessionControl and SessionManager."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.library.standard.lib.comm.payload import (
    label_untrusted_peer,
    peer_steer_control,
)
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.design_patterns.linear import DeterministicLinearPlugin
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext
from mas.runtime.boundary.control.contract import ControlCapability, ControlDenied, SessionPaused
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.egress import RequestCtxAssembly
from mas.runtime.schema.ingress import UserInputReceived
from mas.runtime.session import Session

_LLM = SimpleNamespace(
    plugin_id="react@v1",
    STEER_MODES=frozenset({"after", "preempt", "replace"}),
)


def _session(manager: SessionManager, session_id: str = "s1", *, plugin: object | None = None) -> Session:
    ctx = AutoCtxAssembler()
    fed: list[object] = []
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: None,
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx),
        kernel=SimpleNamespace(plugin=plugin),
        feed=lambda event: (
            fed.append(event) or SimpleNamespace(client_responses=[], hitl_requests=[], boundary_errors=[])
        ),
        fed=fed,
    )

    class _Display:
        def on_system(self, *_a: object, **_k: object) -> None:
            return None

        def on_user(self, *_a: object, **_k: object) -> None:
            return None

    controller = SessionController(instance=instance, display=_Display())
    return manager.create(instance, controller, {"name": "agent", "spec": {}}, session_id=session_id)


def test_peer_steer_trace_actor_is_the_sender_not_admin() -> None:
    manager = SessionManager()
    _session(manager, "worker-sess")
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "worker-sess"},
    )
    assert comm.send("worker", "nudge", context=SendContext(intent="steer")) == "delivered"
    events = [event for event in manager.control_events if event.method == "steer"]
    assert events
    assert events[0].actor == "planner"
    assert events[0].surface == "peer"
    assert events[0].actor != "admin"
    scoped = peer_steer_control(admin, actor="planner", session_id="worker-sess")
    with pytest.raises(ControlDenied, match="capability"):
        scoped.pause("worker-sess", reason="no")
    with pytest.raises(ControlDenied, match="capability"):
        scoped.steer("other-sess", text="no")


def test_peer_after_steer_does_not_jump_operator_queue() -> None:
    manager = SessionManager()
    _session(manager, "worker-sess")
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    admin.enqueue_input("worker-sess", text="OPERATOR queued turn", source="user")
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "worker-sess"},
    )
    comm.send("worker", "peer turn", context=SendContext(intent="steer"))
    labelled = label_untrusted_peer("peer turn", sender="planner", intent="steer")
    texts = [item.text for item in admin.inspect_queue("worker-sess").items]
    assert texts[0] == "OPERATOR queued turn"
    assert texts[1] == labelled
    sources = [item.source for item in admin.inspect_queue("worker-sess").items]
    assert sources == ["user", "peer"]


def test_peer_idle_preempt_is_queued_and_does_not_block() -> None:
    manager = SessionManager()
    session = _session(manager, "worker-sess", plugin=_LLM)

    def boom(event: object) -> object:
        session.instance.fed.append(event)
        raise RuntimeError("target died")

    session.instance.feed = boom
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "worker-sess"},
    )
    assert (
        comm.send(
            "worker",
            "start over",
            context=SendContext(intent="steer", mode="preempt"),
        )
        == "delivered"
    )
    assert session.instance.fed == []
    queued = admin.inspect_queue("worker-sess").items
    assert queued[0].source == "peer"
    assert queued[0].action == "steer"


def test_unknown_session_is_an_agent_comm_error() -> None:
    manager = SessionManager()
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "no-such-session"},
    )
    with pytest.raises(AgentCommError, match="unknown session"):
        comm.send("worker", "nudge", context=SendContext(intent="steer"))


def test_denied_steer_is_an_agent_comm_error() -> None:
    class _Deny:
        capability = ControlCapability(
            actor="planner",
            surface="peer",
            session_ids=frozenset({"worker-sess"}),
            methods=frozenset({"steer"}),
        )

        def steer(self, session_id: str, **_kwargs: object) -> None:
            raise ControlDenied("steer", session_id, reason="no")

    with pytest.raises(AgentCommError, match="denied"):
        LocalAgentComm(
            lambda *_a, **_k: "ok",
            agent_id="planner",
            control=_Deny(),
            steer_peers={"worker": "worker-sess"},
        ).send("worker", "nudge", context=SendContext(intent="steer"))


def test_overprivileged_control_without_manager_is_rejected() -> None:
    admin = SimpleNamespace(
        steer=lambda *_a, **_k: None,
        capability=ControlCapability(actor="admin", surface="admin"),
    )
    with pytest.raises(AgentCommError, match="more than steer"):
        peer_steer_control(admin, actor="planner", session_id="worker-sess")


def test_deterministic_preempt_is_refused() -> None:
    manager = SessionManager()
    _session(manager, "worker-sess", plugin=DeterministicLinearPlugin())
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "worker-sess"},
    )
    with pytest.raises(AgentCommError, match="does not support steer mode 'preempt'"):
        comm.send(
            "worker",
            "cut",
            context=SendContext(intent="steer", mode="preempt"),
        )
    assert comm.send("worker", "later", context=SendContext(intent="steer")) == "delivered"


def test_pause_only_control_cannot_steer() -> None:
    manager = SessionManager()
    _session(manager, "s-worker")
    _session(manager, "other")
    pause_only = manager.control(
        capability=ControlCapability(
            actor="link",
            surface="plugin",
            session_ids=frozenset({"other"}),
            methods=frozenset({"pause"}),
        )
    )
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=pause_only,
        steer_peers={"worker": "s-worker"},
    )
    with pytest.raises(AgentCommError, match="not allowed"):
        comm.send("worker", "nudge", context=SendContext(intent="steer"))
    assert not [event for event in manager.control_events if event.method == "steer"]


def test_peer_preempt_does_not_jump_operator_while_busy() -> None:
    manager = SessionManager()
    session = _session(manager, "s-worker", plugin=_LLM)
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    admin.enqueue_input("s-worker", text="OPERATOR queued turn", source="user")
    session.controller.inflight = True
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "s-worker"},
    )
    comm.send("worker", "peer steer", context=SendContext(intent="steer", mode="preempt"))
    comm.send("worker", "peer turn", context=SendContext(intent="steer", mode="replace"))
    items = admin.inspect_queue("s-worker").items
    assert [(item.source, item.action) for item in items] == [
        ("user", "turn"),
        ("peer", "steer"),
        ("peer", "turn"),
    ]


def test_steer_sender_and_source_come_from_capability() -> None:
    manager = SessionManager()
    session = _session(manager, "s-worker", plugin=_LLM)
    session.controller.inflight = True
    llm = manager.control(capability=ControlCapability(actor="llm-agent", surface="llm"))
    names = inspect.signature(llm.steer).parameters
    assert "sender" not in names
    assert "source" not in names
    llm.steer("s-worker", text="nudge", mode="preempt")
    items = llm.inspect_queue("s-worker").items
    assert items[0].source == "llm"
    assert items[0].actor == "llm-agent"


def test_paused_session_queues_after_and_refuses_preempt() -> None:
    manager = SessionManager()
    _session(manager, "s-worker", plugin=_LLM)
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    admin.pause("s-worker", reason="hold")
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "s-worker"},
    )
    labelled = label_untrusted_peer("nudge", sender="planner", intent="steer")
    assert comm.send("worker", "nudge", context=SendContext(intent="steer")) == "delivered"
    assert admin.inspect_queue("s-worker").items[0].text == labelled
    with pytest.raises(AgentCommError, match="paused"):
        comm.send("worker", "cut", context=SendContext(intent="steer", mode="preempt"))
    with pytest.raises(SessionPaused, match="paused"):
        admin.steer("s-worker", text="cut", mode="preempt")


def test_unknown_pattern_allows_after_only() -> None:
    manager = SessionManager()
    _session(manager, "s-worker", plugin=None)
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    assert admin.steer_modes("s-worker") == frozenset({"after"})
    with pytest.raises(ValueError, match="does not support steer mode 'preempt'"):
        admin.steer("s-worker", text="cut", mode="preempt")
    admin.steer("s-worker", text="later", mode="after")
    assert admin.inspect_queue("s-worker").items[0].text == "later"


def test_deterministic_runtime_instance_after_steer_reaches_the_turn() -> None:
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(),
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="ok"),
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = DeterministicLinearPlugin()
    instance.driver.session_id = "s-linear"

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

    controller = SessionController(instance=instance, display=_Display())
    manager = SessionManager()
    session = manager.create(
        instance,
        controller,
        {"name": "agent", "spec": {"design_pattern": {"type": "deterministic_linear@v1"}}},
        session_id="s-linear",
    )
    admin = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    comm = LocalAgentComm(
        lambda *_a, **_k: "ok",
        agent_id="planner",
        control=admin,
        steer_peers={"worker": "s-linear"},
    )
    labelled = label_untrusted_peer("nudge", sender="planner", intent="steer")
    assert comm.send("worker", "nudge", context=SendContext(intent="steer")) == "delivered"
    result = session.controller.run_turn("", auto_hitl=False)
    first_user = next(step.ingress for step in result.trace.steps if isinstance(step.ingress, UserInputReceived))
    assert first_user.sender == "planner"
    assert first_user.intent == "steer"
    assert first_user.source == "peer"
    assert labelled in first_user.text
    assembled = [item for step in result.trace.steps for item in step.egress if isinstance(item, RequestCtxAssembly)]
    assert assembled

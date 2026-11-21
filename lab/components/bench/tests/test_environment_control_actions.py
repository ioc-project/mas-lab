#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import pytest
from mas.lab.benchmark.environment.control import EnvironmentControlAdapter
from mas.lab.benchmark.environment.engine import RunEnvironment
from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment


class _Contract:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def steer(self, session_id, *, text="", **kwargs):
        self.calls.append(("steer", (session_id,), {"text": text, **kwargs}))

    def enqueue_input(self, session_id, *, text, source="user", **kwargs):
        self.calls.append(("enqueue_input", (session_id,), {"text": text, "source": source, **kwargs}))

    def pause(self, session_id, *, reason="", **kwargs):
        self.calls.append(("pause", (session_id,), {"reason": reason, **kwargs}))

    def resume(self, session_id, **kwargs):
        self.calls.append(("resume", (session_id,), kwargs))

    def snapshot(self, session_id, *, label="", **kwargs):
        self.calls.append(("snapshot", (session_id,), {"label": label, **kwargs}))

    def persist(self, session_id, *, label="", **kwargs):
        self.calls.append(("persist", (session_id,), {"label": label, **kwargs}))

    def cancel_inflight(self, session_id):
        self.calls.append(("cancel_inflight", (session_id,), {}))

    def disable_tool(self, session_id, *, name, reason="", **kwargs):
        self.calls.append(("disable_tool", (session_id,), {"name": name, "reason": reason, **kwargs}))

    def enable_tool(self, session_id, *, name, reason="", **kwargs):
        self.calls.append(("enable_tool", (session_id,), {"name": name, "reason": reason, **kwargs}))


def _run(do: list, control=None) -> RunEnvironment:
    spec = compile_environment({"triggers": [{"id": "t", "when": {"on": "tool_result", "occurrence": 1}, "do": do}]})
    env = RunEnvironment(spec, run=RunCoordinates(), control=control, session_id="s1")

    class A:
        hook = "ingress"
        op = "TOOL_RESULT"
        tool = ""
        agent = ""
        occurrence = 1

    env.on_transition("tool_result", anchor=A())
    return env


@pytest.mark.parametrize(
    "step_type,verbs",
    [
        ("steer", ["steer"]),
        ("inject", ["enqueue_input"]),
        ("pause", ["pause"]),
        ("resume", ["resume"]),
        ("checkpoint", ["snapshot"]),
        ("kill_switch", ["cancel_inflight", "persist"]),
        ("hitl_answer", ["enqueue_input"]),
    ],
)
def test_each_control_action_goes_through_contract(step_type: str, verbs: list[str]) -> None:
    contract = _Contract()
    _run([{"type": step_type, "config": {"text": "hi", "label": "x"}}], control=EnvironmentControlAdapter(contract))
    assert [row[0] for row in contract.calls] == verbs
    if step_type == "kill_switch":
        assert contract.calls[-1][2].get("auto_stop") is True


def test_refused_without_capability() -> None:
    env = _run([{"type": "steer", "config": {}}])
    failed = [r for r in env.events.records() if r["name"] == "action.failed"]
    assert failed


def test_kill_switch_persist_auto_stop_pauses_the_session(tmp_path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore
    from mas.ctl.session.controller import SessionController
    from mas.ctl.session.manager import SessionManager
    from mas.runtime.boundary.control.contract import ControlCapability, SessionPaused
    from mas.runtime.driver.mocks import AutoCtxAssembler
    from mas.runtime.session import SessionStatus

    ctx = AutoCtxAssembler()
    instance = type(
        "Inst",
        (),
        {
            "snapshot": lambda self: {"q": {}, "run": {}},
            "load_checkpoint": lambda self, data: None,
            "pause": lambda self, **kw: None,
            "resume": lambda self: None,
            "driver": type("D", (), {"ctx": ctx})(),
            "kernel": type(
                "K",
                (),
                {"plugin": type("P", (), {"plugin_id": "react@v1", "STEER_MODES": frozenset({"after"})})()},
            )(),
            "feed": lambda self, event: type(
                "F", (), {"client_responses": [], "hitl_requests": [], "boundary_errors": []}
            )(),
        },
    )()

    class _Display:
        def on_system(self, *_a, **_k):
            return None

        def on_user(self, *_a, **_k):
            return None

    store = JsonCheckpointStore(tmp_path)
    manager = SessionManager(checkpoint_store=store)
    controller = SessionController(instance=instance, display=_Display())
    session = manager.create(instance, controller, {"name": "agent", "spec": {}}, session_id="s1")
    control = manager.control(
        capability=ControlCapability(actor="environment", surface="plugin", session_ids=frozenset({"s1"}))
    )
    _run([{"type": "kill_switch", "config": {"reason": "stop"}}], control=EnvironmentControlAdapter(control))
    assert session.status is SessionStatus.PAUSED
    with pytest.raises(SessionPaused):
        session.controller.run_turn("hello")

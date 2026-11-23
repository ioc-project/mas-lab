#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from types import SimpleNamespace

from mas.library.standard.lib.environment_port import reset_environment_port, set_environment_port
from mas.library.standard.lib.triggers import compile_trigger
from mas.library.standard.plugins.governance.environment import EnvironmentPlugin
from mas.runtime.boundary.gov.transition import build_gov_transition
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.ingress import EngineIoReturn, UserInputReceived


class _Port:
    def __init__(self, triggers=()):
        self._triggers = triggers
        self.fired: list[tuple[str, object]] = []
        self.errors: list[str] = []
        self.raise_on_fire = False

    def triggers(self):
        return self._triggers

    def fire(self, trigger_id, anchor):
        if self.raise_on_fire:
            raise RuntimeError("boom")
        self.fired.append((trigger_id, anchor))

    def note_error(self, message):
        self.errors.append(message)

    def observe(self, on, *, tool="", agent="", hook="", op=""):
        from mas.library.standard.lib.triggers import TriggerMatcher

        matcher = TriggerMatcher(tuple(self._triggers))
        hits = matcher.match(on=on, tool=tool, agent=agent)
        for trigger in hits:
            self.fire(
                trigger.id,
                SimpleNamespace(
                    hook=hook,
                    op=op,
                    tool=tool,
                    agent=agent,
                    occurrence=matcher.count(trigger.id),
                ),
            )

    def bind_control(self, control, *, session_id="", entry_agent=""):
        return None

    def run_named_action(self, name):
        return None

    def emit_named(self, name, payload=None, *, source=None):
        return {}

    def deliver_event(self, name, payload=None, *, source=None):
        return False

    def fixture_url(self, name):
        return ""


def _user_input_transition():
    return build_gov_transition(
        "ingress",
        UserInputReceived(user_turn_id="u1", text="hello"),
        q=QProduct(),
    )


def test_always_allow() -> None:
    plugin = EnvironmentPlugin()
    decision, _id, _reason = plugin.evaluate_egress(SimpleNamespace(), config=SimpleNamespace())
    assert decision is GovDecision.ALLOW


def test_no_port_is_noop() -> None:
    plugin = EnvironmentPlugin()
    q = QProduct()
    q.pending_tool_name = "pay_fare"
    plugin.on_transition(
        build_gov_transition(
            "ingress",
            EngineIoReturn(correlation_id=1, response_kind="TOOL_RESULT", next_step="STOP"),
            q=q,
        )
    )


def test_port_raising_is_recorded() -> None:
    trigger = compile_trigger({"id": "t", "when": {"on": "tool_result", "occurrence": "every"}})
    port = _Port((trigger,))
    port.raise_on_fire = True
    token = set_environment_port(port)
    try:
        plugin = EnvironmentPlugin()
        q = QProduct()
        q.pending_tool_name = "pay_fare"
        plugin.on_transition(
            build_gov_transition(
                "ingress",
                EngineIoReturn(correlation_id=1, response_kind="TOOL_RESULT", next_step="STOP"),
                q=q,
            )
        )
        assert port.errors
    finally:
        reset_environment_port(token)


def test_bind_session_asks_manager_for_control() -> None:
    seen: dict[str, object] = {}

    class RecordingPort(_Port):
        def bind_control(self, control, *, session_id="", entry_agent=""):
            seen["control"] = control
            seen["session_id"] = session_id
            seen["entry_agent"] = entry_agent

    class Manager:
        def control(self, *, capability):
            seen["capability"] = capability
            return SimpleNamespace(actor="environment", capability=capability)

    port = RecordingPort()
    token = set_environment_port(port)
    try:
        plugin = EnvironmentPlugin()
        plugin.bind_session(
            SimpleNamespace(session_id="s1", entry_agent_id="entry"),
            manager=Manager(),
        )
        assert seen["session_id"] == "s1"
        assert seen["entry_agent"] == "entry"
        cap = seen["capability"]
        assert cap.actor == "environment"
        assert seen["control"].actor == "environment"
        empty = EnvironmentPlugin()
        empty.bind_session(SimpleNamespace())
        nobody = empty.transition_filters()
        assert nobody
        assert nobody == empty.transition_filters()
        assert not any(item.matches(_user_input_transition()) for item in nobody)
    finally:
        reset_environment_port(token)


def test_filters_without_port_and_llm_turn_kinds() -> None:
    from mas.library.standard.plugins.governance.environment import _filters_for, _on_for

    plugin = EnvironmentPlugin()
    plugin.bind_session(None)
    nobody = plugin.transition_filters()
    assert nobody
    assert not any(item.matches(_user_input_transition()) for item in nobody)
    call = SimpleNamespace(hook="egress", op="TOOL_CALL", response_kind="", kind="")
    assert _on_for(call) == "tool_call"
    llm = SimpleNamespace(hook="egress", op="LLM_CALL", response_kind="", kind="")
    assert _on_for(llm) == "llm_call"
    start = SimpleNamespace(hook="ingress", op="", response_kind="", kind="USER_INPUT_RECEIVED")
    assert _on_for(start) == "turn_start"
    end = SimpleNamespace(hook="ingress", op="", response_kind="MODEL_TEXT", kind="")
    assert _on_for(end) == "turn_end"
    other = SimpleNamespace(hook="egress", op="OTHER", response_kind="", kind="")
    assert _on_for(other) == ""
    packed = _filters_for(
        (
            compile_trigger({"id": "a", "when": {"on": "llm_call"}}),
            compile_trigger({"id": "b", "when": {"on": "turn_start"}}),
            compile_trigger({"id": "c", "when": {"on": "turn_end"}}),
            compile_trigger({"id": "d", "when": {"on": "tool_call"}}),
        )
    )
    assert len(packed) >= 4


def test_filters_derived_from_triggers() -> None:
    trigger = compile_trigger({"id": "t", "when": {"on": "tool_result"}})
    port = _Port((trigger,))
    token = set_environment_port(port)
    try:
        plugin = EnvironmentPlugin()
        filters = plugin.transition_filters()
        assert any(getattr(item, "response_kind", ()) == ("TOOL_RESULT",) for item in filters)
    finally:
        reset_environment_port(token)


def test_filters_are_not_cached_across_port_binds() -> None:
    plugin = EnvironmentPlugin()
    nobody = plugin.transition_filters()
    assert nobody
    trigger = compile_trigger({"id": "t", "when": {"on": "tool_result"}})
    token = set_environment_port(_Port((trigger,)))
    try:
        filters = plugin.transition_filters()
        assert any(getattr(item, "response_kind", ()) == ("TOOL_RESULT",) for item in filters)
        assert filters != nobody
    finally:
        reset_environment_port(token)


def test_nobody_is_not_subscribed_at_notify_governance() -> None:
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.simulated import SimulatedEngine
    from mas.runtime.kernel.config import KernelConfig

    class Counting(EnvironmentPlugin):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def on_transition(self, transition: object) -> None:
            self.calls += 1
            super().on_transition(transition)

    plugin = Counting()
    engine = SimulatedEngine(
        script={1: EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP", text="ok")}
    )
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=plugin))
    inst.capture_session_baseline()
    inst.run_user_text("hello")
    assert plugin.calls == 0


def test_pay_fare_tool_result_observe_through_notify_governance() -> None:
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.simulated import SimulatedEngine
    from mas.runtime.kernel.config import KernelConfig

    class Recording(_Port):
        def __init__(self) -> None:
            super().__init__(
                (
                    compile_trigger(
                        {"id": "t", "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": "every"}}
                    ),
                )
            )
            self.seen: list[dict[str, str]] = []

        def observe(self, on, *, tool="", agent="", hook="", op=""):
            self.seen.append({"on": on, "tool": tool, "hook": hook, "op": op})
            super().observe(on, tool=tool, agent=agent, hook=hook, op=op)

    port = Recording()
    token = set_environment_port(port)
    try:
        plugin = EnvironmentPlugin()
        engine = SimulatedEngine(
            llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
            llm_tool_intent=lambda cid: ("pay_fare", {}),
        )
        inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=plugin))
        inst.capture_session_baseline()
        inst.run_user_text("pay")
        observed = [row for row in port.seen if row["on"] == "tool_result"]
        assert observed
        assert observed[0]["hook"] == "ingress"
        assert observed[0]["op"] == "TOOL_RESULT"
        assert observed[0]["tool"] == "pay_fare"
    finally:
        reset_environment_port(token)

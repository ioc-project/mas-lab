# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.library.standard.lib.environment_port import reset_environment_port, set_environment_port
from mas.library.standard.lib.triggers import compile_trigger
from mas.library.standard.plugins.governance.environment import EnvironmentPlugin
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig


class _Port:
    def __init__(self, triggers=()):
        self._triggers = tuple(triggers)
        self.seen: list[dict[str, str]] = []

    def triggers(self):
        return self._triggers

    def fire(self, trigger_id, anchor):
        del trigger_id, anchor

    def note_error(self, message):
        return None

    def observe(self, on, *, tool="", agent="", hook="", op=""):
        self.seen.append({"on": on, "tool": tool, "hook": hook, "op": op})

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


def test_port_reaches_notify_governance() -> None:
    port = _Port(
        (compile_trigger({"id": "t", "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": "every"}}),)
    )
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

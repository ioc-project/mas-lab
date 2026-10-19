#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SessionController._setup_exchange_tracing() must subscribe BOTH a
CliTraceExchangePlugin and a tool-error/listener bridge -- regression guard
for a real bug: an earlier merge of feat/agent-hitl-runtime's on_exchange
callback with feat/skills-plugin-library's subscribe_exchange() silently
reverted to the single-callback approach, dropping always-on tool-call
error surfacing and exchange_listener forwarding, with no test catching it
(confirmed happening more than once across manual git-history reconciliation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mas.ctl.session.controller import ConversationConfig, SessionController
from mas.ctl.session.exchange_log import CliTraceExchangePlugin


@dataclass
class _FakeDriver:
    exchange_plugins: list[Any] = field(default_factory=list)
    capture_engine_io: bool = False

    def subscribe_exchange(self, plugin: Any) -> None:
        if plugin not in self.exchange_plugins:
            self.exchange_plugins.append(plugin)


@dataclass
class _FakeInstance:
    driver: _FakeDriver = field(default_factory=_FakeDriver)


def _controller() -> SessionController:
    return SessionController(
        instance=_FakeInstance(),
        display=None,
        config=ConversationConfig(),
        trace=True,
    )


def test_subscribes_both_trace_and_bridge_plugins_exactly_once():
    controller = _controller()

    controller._setup_exchange_tracing()
    controller._setup_exchange_tracing()  # every turn calls this again

    plugins = controller.instance.driver.exchange_plugins
    assert len(plugins) == 2
    assert any(isinstance(p, CliTraceExchangePlugin) for p in plugins)
    # The bridge is intentionally module-private; assert by behavior
    # (has on_exchange, is not the trace plugin) rather than importing it.
    bridge_candidates = [p for p in plugins if not isinstance(p, CliTraceExchangePlugin)]
    assert len(bridge_candidates) == 1
    assert hasattr(bridge_candidates[0], "on_exchange")


def test_subscribes_hub_plugin_only_when_hub_is_injected():
    from mas.runtime.boundary.obs.exchange_hub import ExchangeHub, HubExchangePlugin

    controller = _controller()
    controller.exchange_hub = ExchangeHub()
    controller._setup_exchange_tracing()
    controller._setup_exchange_tracing()
    plugins = controller.instance.driver.exchange_plugins
    assert sum(isinstance(p, HubExchangePlugin) for p in plugins) == 1


def test_bridge_forwards_to_exchange_listener_even_when_trace_is_off():
    """The bridge (errors + exchange_listener forwarding) must stay active
    even when normal --trace/--verbose display is off -- only CliTrace
    should go quiet, not the bridge."""
    from mas.runtime.driver.driver import ExchangeRecord

    received: list[Any] = []
    controller = SessionController(
        instance=_FakeInstance(),
        display=None,
        config=ConversationConfig(),
        trace=False,
        verbose=0,
        exchange_listener=received.append,
    )
    controller._setup_exchange_tracing()

    record = ExchangeRecord(kind="user_out", text="hi", ts_mono=0.0, ts_wall="")
    bridge = next(
        p for p in controller.instance.driver.exchange_plugins if not isinstance(p, CliTraceExchangePlugin)
    )
    bridge.on_exchange(record)

    assert received == [record]

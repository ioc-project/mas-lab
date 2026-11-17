#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance tells a peer or tool never declared from one declared but unavailable; the breaker owns peer circuits."""

from __future__ import annotations

from typing import Any

from mas.library.standard.plugins.governance.no_undeclared_peer import NoUndeclaredPeerPlugin
from mas.library.standard.plugins.governance.no_undeclared_tool import NoUndeclaredToolPlugin
from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.layers.state import MemorySpecPort
from mas.runtime.reliability.classes import FailureClass

DECLARED = {
    "tools": [{"name": "lookup"}],
    "communication": {"peers": [{"id": "billing"}, {"id": "outage"}]},
}


def _config() -> KernelConfig:
    return KernelConfig(agent_spec=DECLARED, agent_id="triage")


def _call(tool: str, running: dict[str, Any] | None, offered: tuple[str, ...] | None = None) -> EgressIntentView:
    return EgressIntentView(
        op="TOOL_CALL", destructive=False, correlation_id=1, tool_name=tool, offered_tools=offered, running_spec=running
    )


def test_a_peer_never_declared_is_blocked_as_undeclared() -> None:
    decision, _, reason = NoUndeclaredPeerPlugin().evaluate_egress(
        _call("delegate_to_stranger", DECLARED), config=_config()
    )
    assert decision == GovDecision.BLOCK and "not in this agent's delegates_to" in reason


def test_a_declared_peer_that_is_away_is_blocked_as_unavailable() -> None:
    running = {**DECLARED, "communication": {"peers": [{"id": "billing", "enabled": False}, {"id": "outage"}]}}
    decision, _, reason = NoUndeclaredPeerPlugin().evaluate_egress(
        _call("delegate_to_billing", running), config=_config()
    )
    assert decision == GovDecision.BLOCK and "declared but unavailable" in reason and "outage" in reason
    decision, *_ = NoUndeclaredPeerPlugin().evaluate_egress(_call("delegate_to_outage", running), config=_config())
    assert decision == GovDecision.ALLOW


def test_a_declared_tool_switched_off_is_blocked_as_unavailable_not_undeclared() -> None:
    running = {"tools": [{"name": "lookup", "enabled": False}]}
    plugin = NoUndeclaredToolPlugin()
    _, _, reason = plugin.evaluate_egress(_call("lookup", running, offered=()), config=_config())
    assert "declared but unavailable" in reason
    _, _, reason = plugin.evaluate_egress(_call("ghost", running, offered=()), config=_config())
    assert "was not in the tools list" in reason


class _PeerPort(MemorySpecPort):
    def __init__(self) -> None:
        super().__init__()
        self.away: list[str] = []
        self.back: list[str] = []

    def disable_peer(self, name: str, *, by: str, reason: str) -> bool:
        self.away.append(name)
        return True

    def enable_peer(self, name: str, *, by: str, reason: str) -> bool:
        self.back.append(name)
        return True


async def test_the_breaker_takes_a_failing_peer_away_and_offers_it_again_after_the_timeout() -> None:
    now = {"t": 1000.0}

    class _Body:
        owns_call_identity = True
        api_base = "http://llm.test"
        model = "m"
        ctx = None

        def capabilities(self) -> dict[str, bool]:
            return {}

        async def atick(self) -> None:
            return None

    port = _PeerPort()
    layer = CircuitBreakerLayer(inner=_Body(), failure_threshold=1, reset_timeout_s=30, clock=lambda: now["t"])
    layer._port = lambda: port  # type: ignore[method-assign]
    layer.record_failure("peer:billing", FailureClass.UNAVAILABLE, port)
    assert port.away == ["billing"]
    await layer.atick()
    assert port.back == []
    now["t"] = 1031.0
    await layer.atick()
    assert port.back == ["billing"]


async def test_a_send_to_a_declared_but_unavailable_peer_says_so_and_offers_only_the_rest() -> None:
    from types import SimpleNamespace

    from mas.library.standard.plugins.system_tools.send import SendTool

    spec = {"communication": {"peers": [{"id": "billing", "enabled": False}, {"id": "outage"}]}}
    ctx = SimpleNamespace(
        agent_id="triage",
        manifest={"metadata": {"name": "triage"}, "spec": DECLARED},
        current_spec=spec,
        agent_comm=None,
        agent_comm_routes={},
        delegation_peer_descriptions=None,
        send_peer_descriptions=None,
    )
    tool = SendTool(allowed_peers=["billing", "outage"])
    assert [s["name"] for s in tool.on_collect_tools(ctx=ctx)] == ["delegate_to_outage"]
    assert "declared but unavailable" in await tool.asend("billing", "hi", ctx=ctx)


async def test_a_peer_probe_goes_through_the_link_or_says_it_cannot() -> None:
    from types import SimpleNamespace

    from mas.library.standard.plugins.system_tools.send import SendTool

    seen: list[str] = []

    class Probing:
        plugin_id = "a2a"
        fabric_id = None
        endpoint = ""

        async def aprobe(self, target: str) -> bool:
            seen.append(target)
            return True

    class Silent:
        plugin_id = "local"
        fabric_id = None
        endpoint = ""

    def ctx_with(link: object) -> SimpleNamespace:
        return SimpleNamespace(
            agent_id="triage",
            manifest=None,
            agent_comm=link,
            agent_comm_routes={},
            delegation_peer_descriptions=None,
            send_peer_descriptions=None,
        )

    tool = SendTool(allowed_peers=["billing"])
    assert await tool.aprobe("billing", ctx=ctx_with(Probing())) is True and seen == ["billing"]
    assert await tool.aprobe("billing", ctx=ctx_with(Silent())) is None

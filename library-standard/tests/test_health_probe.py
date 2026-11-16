#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The health probe layer checks declared tools and peers ahead of the model's next call."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.plugins.layers.health_probe import (
    HealthProbeLayer,
    declared_peer_probes,
    declared_tool_probes,
    parse_params,
)
from mas.runtime.layers.probe import PeerProber, ToolProber
from mas.runtime.layers.state import MemorySpecPort

SPEC = {
    "tools": [{"name": "lookup", "probe": {"arguments": {"q": "ping"}}}, {"name": "plain"}],
    "communication": {"peers": [{"id": "billing", "probe": True}, {"id": "notes"}, "outage"]},
}


class _Port(MemorySpecPort):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str, str]] = []

    def disable_tool(self, name: str, *, by: str, reason: str) -> bool:
        self.events.append(("disable_tool", name, by))
        return True

    def enable_tool(self, name: str, *, by: str, reason: str) -> bool:
        self.events.append(("enable_tool", name, by))
        return True

    def disable_peer(self, name: str, *, by: str, reason: str) -> bool:
        self.events.append(("disable_peer", name, by))
        return True

    def enable_peer(self, name: str, *, by: str, reason: str) -> bool:
        self.events.append(("enable_peer", name, by))
        return True


class _Body(ToolProber, PeerProber):
    owns_call_identity = True
    api_base = "http://llm.test"
    model = "m"
    ctx = None

    def __init__(self) -> None:
        self.tool_fails = False
        self.peer_result: bool | None = True
        self.peer_fails = False
        self.tools: list[str] = []
        self.peers: list[str] = []

    def capabilities(self) -> dict[str, bool]:
        return {}

    async def atick(self) -> None:
        return None

    async def aprobe_tool(self, name: str, arguments: Any) -> None:
        self.tools.append(name)
        if self.tool_fails:
            raise RuntimeError("down")

    async def aprobe_peer(self, name: str) -> bool | None:
        self.peers.append(name)
        if self.peer_fails:
            raise RuntimeError("no answer")
        return self.peer_result


def _layer(body: _Body, port: _Port, now: dict[str, float], **params: Any) -> HealthProbeLayer:
    layer = HealthProbeLayer(inner=body, clock=lambda: now["t"], **params)
    layer._port = lambda: port  # type: ignore[method-assign]
    import mas.library.standard.plugins.layers.health_probe as module

    module.running_spec_of = lambda _ctx: SPEC  # type: ignore[assignment]
    return layer


def test_only_what_the_spec_marks_probeable_is_declared() -> None:
    assert declared_tool_probes(SPEC) == {"lookup": {"q": "ping"}}
    assert declared_peer_probes(SPEC) == ["billing"]


def test_the_interval_is_a_positive_number() -> None:
    assert parse_params({}) == (30.0, True)
    assert parse_params({"interval_s": 5, "enabled": False}) == (5.0, False)
    for bad in ({"interval_s": 0}, {"interval_s": "x"}, {"nope": 1}):
        with pytest.raises(ValueError):
            parse_params(bad)


async def test_a_failing_probe_takes_the_target_out_and_a_passing_one_puts_it_back() -> None:
    now = {"t": 1000.0}
    body, port = _Body(), _Port()
    layer = _layer(body, port, now, interval_s=30)
    body.tool_fails, body.peer_fails = True, True
    await layer.atick()
    assert port.events == [("disable_tool", "lookup", "health_probe"), ("disable_peer", "billing", "health_probe")]
    await layer.atick()  # not due yet
    assert body.tools == ["lookup"] and body.peers == ["billing"]
    now["t"] = 1031.0
    body.tool_fails, body.peer_fails = False, False
    await layer.atick()
    assert port.events[2:] == [("enable_tool", "lookup", "health_probe"), ("enable_peer", "billing", "health_probe")]


async def test_a_link_without_a_probe_changes_nothing_and_a_pass_with_nothing_to_undo_enables_nothing() -> None:
    now = {"t": 1000.0}
    body, port = _Body(), _Port()
    body.peer_result = None
    layer = _layer(body, port, now)
    await layer.atick()
    assert port.events == []  # the tool passed (nothing was failing), the peer link has no probe
    assert port.read("health_probe", "peer:billing") == {}


async def test_a_disabled_layer_probes_nothing() -> None:
    body, port = _Body(), _Port()
    layer = _layer(body, port, {"t": 1.0}, enabled=False)
    await layer.atick()
    assert body.tools == [] and body.peers == []

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP and SLIM hooks: unique call keys, gated when off, bounded, and never raising."""

from __future__ import annotations

import pytest
from library_ioa.plugins.mcp import hooks as mcp_hooks
from library_ioa.plugins.slim import hooks as slim_hooks
from mas.runtime.schema.transport import TransportEvent
from mas.runtime.transport import emit
from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig


@pytest.fixture
def events():
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    emit.bind_sink(Sink())
    yield captured
    emit.reset()


def test_two_calls_of_one_tool_get_different_keys() -> None:
    """Mutation: the key is f'call:{tool}' again, so concurrent calls of a tool are joined wrongly."""
    assert mcp_hooks.new_call_key("echo") != mcp_hooks.new_call_key("echo")


def test_mcp_hooks_are_off_without_config() -> None:
    """Mutation: _emit skips the hooks check, so events are built and handed on with the bus off."""
    emit.reset()
    assert mcp_hooks.hooks_enabled() is False
    assert slim_hooks.hooks_enabled() is False


def test_tools_discovered_is_bounded_and_tolerates_junk(events) -> None:
    """Mutation: emit_tools_discovered copies every tool, whole, and raises on a malformed entry."""
    tools = [{"name": f"t{i}", "description": "d" * 5000} for i in range(1000)] + ["junk", None, 5]
    mcp_hooks.emit_tools_discovered(tools, agent_id="listener")  # type: ignore[arg-type]
    (event,) = events
    listed = event.attributes["tools"]["tools"]
    assert len(listed) == 256 and len(listed[0]["description"]) == 1024
    assert event.attributes["tools"]["truncated"] is True


def test_slim_key_is_shared_by_both_ends_or_fresh() -> None:
    """Mutation: an envelope that names no ids gets the key '0', so every such call shares it."""
    assert slim_hooks.call_key("ctx-1", 7) == "ctx-1"
    assert slim_hooks.call_key("", 7) == "7"
    first, second = slim_hooks.call_key("", 0), slim_hooks.call_key("", "0")
    assert first != second and first.startswith("slim:")


def test_slim_session_events_follow_internals(events) -> None:
    """Mutation: emit_session ignores the internals switch."""
    slim_hooks.emit_session(side="client", key="s")
    assert events == []
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig(internals=True)))
    slim_hooks.emit_session(side="client", key="s")
    assert [e.kind for e in events] == ["transport.session"]


def test_slim_error_event_carries_the_call_key(events) -> None:
    slim_hooks.emit_sent(source="a", target="b", key="k")
    slim_hooks.emit_error(side="client", key="k", error_type="TimeoutError")
    assert [e.kind for e in events] == ["transport.message.sent", "transport.error"]
    assert events[0].correlation_key == events[1].correlation_key == "k"


def test_mcp_call_hooks_emit_the_call_key(events) -> None:
    mcp_hooks.emit_call_sent(tool="echo", source="a", target="b", key="k1")
    mcp_hooks.emit_call_received(tool="echo", source="a", target="b", key="k1")
    mcp_hooks.emit_call_completed(side="client", tool="echo", source="a", target="b", key="k1")
    mcp_hooks.emit_call_error(side="client", tool="echo", key="k1", error_type="TimeoutError")
    assert [e.kind for e in events] == [
        "transport.message.sent",
        "transport.message.received",
        "transport.message.completed",
        "transport.error",
    ]
    assert {e.correlation_key for e in events} == {"k1"}


def test_mcp_and_slim_hooks_swallow_a_raising_sink() -> None:
    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))

    class Boom:
        def emit(self, event: TransportEvent) -> None:
            raise RuntimeError("sink")

    emit.bind_sink(Boom())
    mcp_hooks.emit_call_sent(tool="echo", source="a", target="b", key="k")
    slim_hooks.emit_received(source="a", target="b", key="k")
    slim_hooks.emit_completed(side="server", source="a", target="b", key="k")
    emit.reset()


def test_tools_discovered_is_silent_when_hooks_are_off_or_the_list_is_unreadable() -> None:
    emit.reset()
    mcp_hooks.emit_tools_discovered([{"name": "t"}], agent_id="worker")
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))

    class Boom(list):
        def __iter__(self):
            raise RuntimeError("no")

    mcp_hooks.emit_tools_discovered(Boom(), agent_id="worker")
    emit.reset()

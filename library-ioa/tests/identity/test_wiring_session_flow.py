#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP session setup with an identity: one presentation per request, a refusal that reaches the tool result."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from library_ioa.plugins.http_layer import HttpCallTransport
from library_ioa.plugins.mcp.client import MCPClient
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_builders import close, identity_step, mcp_provider
from wiring_schemes import SingleUse
from wiring_servers import FakeMcp


async def test_a_refusal_during_a_tool_call_reaches_the_tool_result_with_its_code() -> None:
    """Kills: a refusal raised by the transport's auth on ``tools/call`` surfacing as a closed connection."""
    with FakeMcp() as mcp:
        provider = mcp_provider(mcp.url, identity_step("wiring_fail_on_call"))
        try:
            result = await provider.acall_tool("echo", {})
        finally:
            await close(provider)
    assert "refused:no_tools" in str(result), result
    assert "Connection closed" not in str(result) and mcp.tool_calls == []


def _held_client(server: FakeMcp, scheme: str) -> tuple[MCPClient, HttpCallTransport]:
    transport = HttpCallTransport(url=server.url + "/mcp")
    IdentityLayer.wrap(transport, {"identity": "id1", "scheme": scheme, "params": {}}, call_kinds=("tool",))
    client = MCPClient(
        url=server.url + "/mcp", http_client=lambda: transport.aclient, session_hooks=transport.session_hooks()
    )
    return client, transport


async def test_a_refusal_in_a_held_session_reaches_the_caller_with_its_code() -> None:
    with FakeMcp() as server:
        client, transport = _held_client(server, "wiring_fail_on_call")
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                for _ in range(2):
                    with pytest.raises(AgentCommError, match="refused:no_tools"):
                        await client.call_tool("echo", {})
        finally:
            await client.disconnect()
            transport.stop()


async def test_two_principals_each_keep_their_own_held_session() -> None:
    """Kills: a held session closed whenever another principal's call comes in (reconnect on every alternation)."""
    with FakeMcp() as server:
        client, transport = _held_client(server, "wiring_token")
        await client.connect()
        try:

            async def call(actor: str) -> None:
                with bind({"actor": actor}):
                    await client.call_tool("echo", {})

            for actor in ("alice", "bob", "alice", "bob", "alice"):
                await call(actor)
            await asyncio.gather(*(call(a) for a in ("alice", "bob", "alice", "bob")))
            assert len(server.posts("initialize")) == 2  # one session per principal, not one per alternation
            by_actor = {p.headers["authorization"] for p in server.posts("initialize")}
            assert by_actor == {"Bearer tok:alice:initialize", "Bearer tok:bob:initialize"}
        finally:
            await client.disconnect()
            transport.stop()
        assert not client.held


async def test_opening_a_session_presents_once_for_the_initialize_request() -> None:
    """Kills: ``begin`` and the auth each presenting (a single-use credential was spent twice)."""
    with FakeMcp() as mcp:
        provider = mcp_provider(mcp.url, identity_step("wiring_single_use"))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
        finally:
            await close(provider)
    init = [s for s in mcp.posts("initialize")]
    assert len(init) == 1 and init[0].headers["authorization"] == "Bearer use-1"
    assert SingleUse.PRESENTED[0] == "initialize" and SingleUse.PRESENTED.count("initialize") == 1


async def test_a_refusal_is_given_back_to_the_call_that_made_it_and_no_other() -> None:
    """Kills: refusals kept by scope alone (an overlapping call lost or took another call's refusal)."""
    with FakeMcp() as server:
        client, transport = _held_client(server, "wiring_fail_on_call")
        try:
            (hook,) = transport.session_hooks()
            with bind({"actor": "agent-a"}):
                first, second = AgentCommError("identity: refused:a"), AgentCommError("identity: refused:b")
                hook.refused("tools/call:a", first)
                hook.refused("tools/call:b", second)
                assert hook.take_refusal("tools/list") is None
                assert hook.take_refusal("tools/call:b") is second and hook.take_refusal("tools/call:b") is None
                assert hook.take_refusal("tools/call:a") is first
            with bind({"actor": "agent-b"}):
                assert hook.take_refusal("tools/call:a") is None
        finally:
            transport.stop()


async def test_a_presentation_whose_request_never_came_is_dropped() -> None:
    """Kills: begin presentations kept for ever when the request they were for is not sent."""
    from mas.library.standard.plugins.identity.layer import MAX_FACT_SCOPES

    with FakeMcp() as server:
        client, transport = _held_client(server, "wiring_token")
        try:
            (hook,) = transport.session_hooks()
            with bind({"actor": "agent-a"}):
                for n in range(MAX_FACT_SCOPES * 3):
                    await hook.begin(frozenset(), f"task-{n}")
            assert len(hook._begun) <= MAX_FACT_SCOPES
        finally:
            transport.stop()


async def test_a_refusal_on_a_session_setup_request_reaches_the_call_that_opened_the_session() -> None:
    """Kills: a refusal stored under the setup request's own key (the opening call got 'Connection closed' and a stale
    refusal was left behind)."""
    with FakeMcp() as server:
        transport = HttpCallTransport(url=server.url + "/mcp")
        IdentityLayer.wrap(
            transport,
            {"identity": "id1", "scheme": "wiring_fail_on", "params": {"on": "notifications/initialized"}},
            call_kinds=("tool",),
        )
        client = MCPClient(
            url=server.url + "/mcp", http_client=lambda: transport.aclient, session_hooks=transport.session_hooks()
        )
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                with pytest.raises(AgentCommError, match="refused:no_setup"):
                    await client.call_tool("echo", {})
                (hook,) = transport.session_hooks()
                assert hook.take_refusal("") is None  # nothing stale is left for the next call
        finally:
            await client.disconnect()
            transport.stop()


async def test_a_held_session_the_server_forgot_is_replaced_once_and_the_call_succeeds() -> None:
    """Kills: a call that fails because the server lost the session (a restart): it reconnects and goes on."""
    from wiring_servers import Reply

    gone = {"left": 1}

    def gate(request: Any) -> Reply | None:
        if request.rpc_method() == "tools/call" and gone["left"]:
            gone["left"] -= 1
            return Reply(
                404, body={"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Session not found"}}
            )
        return None

    with FakeMcp(gate=gate) as server:
        client, transport = _held_client(server, "wiring_token")
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                result = await client.call_tool("echo", {})
            assert result["isError"] is False and gone["left"] == 0
            assert len(server.posts("initialize")) == 2  # a new session was opened for the second attempt
        finally:
            await client.disconnect()
            transport.stop()


async def test_a_refusal_is_not_retried() -> None:
    with FakeMcp() as server:
        client, transport = _held_client(server, "wiring_fail_on_call")
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                with pytest.raises(AgentCommError, match="refused:no_tools"):
                    await client.call_tool("echo", {})
            assert len(server.posts("initialize")) == 1
        finally:
            await client.disconnect()
            transport.stop()


async def test_refusals_nobody_asked_for_expire() -> None:
    """Kills: refusals kept per scope for ever when no call comes back for them."""
    from mas.library.standard.plugins.identity import layer as layer_module

    with FakeMcp() as server:
        client, transport = _held_client(server, "wiring_token")
        try:
            (hook,) = transport.session_hooks()
            with bind({"actor": "agent-a"}):
                hook.refused("tools/call:x", AgentCommError("identity: refused:x"))
                layer_module._KEEP_S, kept = 0.0, layer_module._KEEP_S
                try:
                    assert hook.take_refusal("tools/call:x") is None
                    hook.refused("tools/call:y", AgentCommError("identity: refused:y"))
                    hook.refused("tools/call:z", AgentCommError("identity: refused:z"))
                    assert len(hook._refusals) == 1  # the expired one was dropped when the next came
                finally:
                    layer_module._KEEP_S = kept
        finally:
            transport.stop()


# --- a refused setup reaches the tool result as the layer's own refusal, and is not retried for ever --------------


def _failing_client(server: FakeMcp, on: str, *, hooks: bool = True) -> tuple[MCPClient, HttpCallTransport]:
    transport = HttpCallTransport(url=server.url + "/mcp")
    IdentityLayer.wrap(
        transport, {"identity": "id1", "scheme": "wiring_fail_on", "params": {"on": on}}, call_kinds=("tool",)
    )
    client = MCPClient(
        url=server.url + "/mcp",
        http_client=lambda: transport.aclient,
        session_hooks=transport.session_hooks() if hooks else [],
    )
    return client, transport


@pytest.mark.parametrize("sticky", [False, True], ids=["one-shot-session", "held-session"])
async def test_a_refusal_of_the_request_the_sdk_adds_to_a_call_reaches_the_tool_result(sticky: bool) -> None:
    """Kills: a refusal kept under ``tools/list`` (the SDK lists the tools to check a result) that nobody asks for,
    so the model got ``unhandled errors in a TaskGroup`` or ``Connection closed`` instead of the closed code."""
    with FakeMcp() as server:
        client, transport = _failing_client(server, "tools/list")
        if sticky:
            await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                with pytest.raises(AgentCommError, match=r"^identity: refused:no_setup$"):
                    await client.call_tool("echo", {})
        finally:
            await client.disconnect()
            transport.stop()


async def test_a_task_group_that_holds_the_layers_refusal_stands_for_it_even_with_no_hook_to_ask() -> None:
    """Kills: a client with no session hook that lets the SDK's task-group error reach the model."""
    with FakeMcp() as server:
        client, transport = _failing_client(server, "initialize", hooks=False)
        try:
            with bind({"actor": "agent-a"}):
                with pytest.raises(AgentCommError, match=r"^identity: refused:no_setup$"):
                    await client.call_tool("echo", {})
        finally:
            transport.stop()


async def test_a_refused_session_setup_is_tried_once_more_and_then_fails_closed_without_asking_the_peer() -> None:
    """Kills: a client that opens a new session for every call whose setup the peer keeps refusing (the lab saw 49)."""
    with FakeMcp() as server:
        client, transport = _failing_client(server, "notifications/initialized")
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                for _ in range(6):
                    with pytest.raises(AgentCommError, match="refused:no_setup"):
                        await client.call_tool("echo", {})
            assert len(server.posts("initialize")) == 2  # the attempt and one retry, then closed
        finally:
            await client.disconnect()
            transport.stop()


async def test_a_closed_setup_opens_again_after_its_window_and_a_working_setup_clears_the_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kills: a failure memory that never expires, or that survives a setup that worked."""
    from library_ioa.plugins.mcp import client as client_module

    with FakeMcp() as server:
        client, transport = _failing_client(server, "notifications/initialized")
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                for _ in range(3):
                    with pytest.raises(AgentCommError):
                        await client.call_tool("echo", {})
                assert len(server.posts("initialize")) == 2
                monkeypatch.setattr(client_module, "_REFUSED_SETUP_TTL_S", 0.0)  # the window is over
                with pytest.raises(AgentCommError):
                    await client.call_tool("echo", {})
                assert len(server.posts("initialize")) == 3
                monkeypatch.setattr(client_module, "_REFUSED_SETUP_TTL_S", 10.0)
                client._refused_setups.clear()
                with pytest.raises(AgentCommError):
                    await client.call_tool("echo", {})
                assert next(iter(client._refused_setups.values()))[0] == 1  # one refusal counted
                (hook,) = transport.session_hooks()
                hook._caller.scheme._on = ""  # the peer now accepts the setup
                assert (await client.call_tool("echo", {}))["isError"] is False
                assert client._refused_setups == {}
        finally:
            await client.disconnect()
            transport.stop()


@pytest.mark.parametrize(
    ("transport", "extra"),
    [("sse", {"url": "http://127.0.0.1:9/sse"}), ("stdio", {"command": "true"})],
)
def test_an_mcp_row_whose_transport_cannot_carry_its_identity_step_is_refused(
    transport: str, extra: dict[str, str]
) -> None:
    """Kills: an sse or stdio row that silently runs without the identity layer its pipeline names."""
    from library_ioa.plugins.mcp.provider import MCPToolProvider

    spec = {
        "name": "fares",
        "kind": "mcp",
        "protocol": "mcp",
        "transport": transport,
        **extra,
        "pipeline": [{"layer": "identity", "params": {"identity": "id1", "scheme": "none", "params": {}}}],
    }
    with pytest.raises(ValueError, match="cannot carry the layer 'identity'"):
        MCPToolProvider.from_provider_spec(spec)
    plain = {k: v for k, v in spec.items() if k != "pipeline"}
    MCPToolProvider.from_provider_spec(plain)  # without an identity step the same row builds, as before

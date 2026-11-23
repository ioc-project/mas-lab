#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Revocation reaches the stack: a held session that used the identity is closed, and the next call is denied."""

from __future__ import annotations

import asyncio

import pytest
from library_ioa.plugins.http_layer import HttpCallTransport
from library_ioa.plugins.mcp.client import MCPClient
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_builders import key_ref
from wiring_servers import FakeMcp

KEY = "k" * 16
PARAMS = {"key": key_ref(KEY), "subject": "alice"}
SHARED = {"identity": "id1", "scheme": "signed_badge", "params": PARAMS, "scope": "process"}


async def test_revoking_the_identity_closes_the_held_session_and_the_next_call_is_denied() -> None:
    """Kills: a layer that never subscribes (the session outlives the revocation); a call after revoke that is sent."""
    with FakeMcp() as server:
        transport = HttpCallTransport(url=server.url + "/mcp")
        IdentityLayer.wrap(transport, SHARED, call_kinds=("tool",))
        client = MCPClient(
            url=server.url + "/mcp", http_client=lambda: transport.aclient, session_hooks=transport.session_hooks()
        )
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                first = await client.call_tool("echo", {})
                assert first["isError"] is False and client.held
                sent = len(server.posts())
                await IdentityLayer(**SHARED).caller.revoke("badge")
                await asyncio.sleep(0.1)  # the owner task closes the transport
                assert not client.held  # the old session is closed
                with pytest.raises(AgentCommError, match="identity: revoked"):
                    await client.call_tool("echo", {})
                assert all(p.rpc_method() != "tools/call" for p in server.posts()[sent:])  # nothing was sent
        finally:
            await client.disconnect()
            transport.stop()


async def test_a_revocation_of_another_identity_leaves_the_session_alone() -> None:
    with FakeMcp() as server:
        transport = HttpCallTransport(url=server.url + "/mcp")
        IdentityLayer.wrap(transport, SHARED, call_kinds=("tool",))
        other = {**SHARED, "identity": "id2"}
        client = MCPClient(
            url=server.url + "/mcp", http_client=lambda: transport.aclient, session_hooks=transport.session_hooks()
        )
        await client.connect()
        try:
            with bind({"actor": "agent-a"}):
                await client.call_tool("echo", {})
                await IdentityLayer(**other).caller.revoke("badge")  # another record: this layer's hooks stay open
                await asyncio.sleep(0.1)
                assert client.held
                assert (await client.call_tool("echo", {}))["isError"] is False
        finally:
            await client.disconnect()
            transport.stop()


def test_layers_share_one_wallet_and_a_stopped_layer_is_not_told() -> None:
    """Kills: a stopped layer still closing sessions; a process-wide wallet that is not shared."""
    first = IdentityLayer(**SHARED)
    layers = [IdentityLayer(**SHARED) for _ in range(5)]
    assert all(layer.caller is first.caller for layer in layers)
    told: list[str] = []
    for layer in (first, *layers):
        layer.invalidate_sessions = lambda name=layer: told.append(str(id(name)))  # type: ignore[method-assign]
    layers[0].stop()
    first.caller.notify_revoked("badge")
    assert len(told) == 5
    assert str(id(layers[0])) not in told

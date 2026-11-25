#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Two callers (scopes) on one MCP client never see each other's session setup or session loss."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from library_ioa.plugins.http_layer import HttpCallTransport
from library_ioa.plugins.mcp.client import MCPClient
from mas.library.standard.lib.identity.contract import Evidence, IdentityError, IdentityScheme
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.plugins.identity.layer import IdentityLayer
from wiring_schemes import OneIdentity
from wiring_servers import FakeMcp, Reply, Seen


class _PerActor(OneIdentity, IdentityScheme):
    """Presents `Bearer <actor>`; `alice` is refused (slowly), `bob` is slow to be presented."""

    scheme: ClassVar[str] = "per_actor"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[dict] = {"type": "object"}
    refuse_alice = False

    @classmethod
    def from_spec(cls, params: Any) -> _PerActor:
        return cls()

    async def present(self, req: Any) -> Evidence:
        actor = req["principal"].get("actor", "")
        if actor == "alice" and self.refuse_alice:
            await asyncio.sleep(0.05)
            raise IdentityError("refused", "nope_alice")
        if actor == "bob" and self.refuse_alice:
            await asyncio.sleep(0.3)
        return Evidence(headers={"authorization": f"Bearer {actor}"})


def _client(server: FakeMcp) -> tuple[MCPClient, HttpCallTransport]:
    transport = HttpCallTransport(url=server.url + "/mcp")
    IdentityLayer.wrap(
        transport,
        {"identity": "id1", "scheme": "per_actor", "params": {}, "schemes": {"per_actor": _PerActor}},
        call_kinds=("tool",),
    )
    client = MCPClient(
        url=server.url + "/mcp", http_client=lambda: transport.aclient, session_hooks=transport.session_hooks()
    )
    return client, transport


async def _call(client: MCPClient, actor: str) -> Any:
    with bind({"actor": actor}):
        return await client.call_tool("echo", {})


async def test_one_scopes_failing_setup_does_not_surface_as_the_error_of_another() -> None:
    """Kills: one shared hook-error field, so bob's failing setup is reported as alice's refusal (or the reverse)."""

    def gate(request: Seen) -> Reply | None:
        if request.rpc_method() == "initialize" and request.headers.get("authorization") == "Bearer bob":
            return Reply(500, body={"error": "server broke"})
        return None

    _PerActor.refuse_alice = True
    try:
        with FakeMcp(gate=gate) as server:
            client, transport = _client(server)
            await client.connect()
            try:
                alice, bob = await asyncio.gather(_call(client, "alice"), _call(client, "bob"), return_exceptions=True)
            finally:
                await client.disconnect()
                transport.stop()
    finally:
        _PerActor.refuse_alice = False
    assert isinstance(alice, BaseException) and "nope_alice" in str(alice), alice
    assert isinstance(bob, BaseException) and "nope_alice" not in str(bob), bob


async def test_a_session_the_server_forgot_closes_that_scopes_session_only() -> None:
    """Kills: a "session gone" for alice that closes bob's held session too (bob would reconnect, mid-conversation)."""
    forgot: list[str] = []

    def gate(request: Seen) -> Reply | None:
        who = request.headers.get("authorization", "")
        if who == "Bearer alice" and request.rpc_method() == "tools/call" and not forgot:
            forgot.append(who)
            return Reply(
                404,
                body={
                    "jsonrpc": "2.0",
                    "id": "server-error",
                    "error": {"code": -32600, "message": "Session not found"},
                },
            )
        return None

    with FakeMcp(gate=gate) as server:
        client, transport = _client(server)
        await client.connect()
        try:
            await _call(client, "alice")  # opens alice's session... and the gate forgets it once
            await _call(client, "bob")
            held_before = dict(client._held)
            assert len(held_before) == 2
            forgot.clear()
            await _call(client, "alice")  # the server forgot this session: a new one for alice, the call served
            assert forgot == ["Bearer alice"]
            bob_scope = next(scope for scope in held_before if "bob" in scope)
            assert client._held.get(bob_scope) is held_before[bob_scope]  # bob's session was left alone
            initializes = [p.headers["authorization"] for p in server.posts("initialize")]
            assert initializes.count("Bearer bob") == 1
        finally:
            await client.disconnect()
            transport.stop()


async def test_events_of_a_held_session_are_not_attributed_to_the_call_that_opened_it(monkeypatch) -> None:
    """Kills: the session's task inheriting the opening tool call, so later calls' events carry its id."""
    from mas.library.standard.lib.identity import observe
    from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observe, "publish_event", seen.append)
    monkeypatch.setattr(observe, "emit_to_channel", lambda event: None)
    with FakeMcp() as server:
        client, transport = _client(server)
        await client.connect()
        try:
            for call in (11, 12):
                token = IN_FLIGHT_TOOL.set(InFlightTool(name="echo", call=call, owner=0, arguments={}))
                try:
                    await _call(client, "alice")
                finally:
                    IN_FLIGHT_TOOL.reset(token)
        finally:
            await client.disconnect()
            transport.stop()
    presents = [e for e in seen if e["kind"] == "identity.present"]
    by_operation = [(e.get("operation"), e.get("call")) for e in presents]
    # the session's own setup belongs to no tool call; each call's request carries that call's id, never the opener's
    assert ("initialize", None) in by_operation and ("initialize", "11") not in by_operation, by_operation
    assert ("tools/call:echo", "12") in by_operation and by_operation.count(("tools/call:echo", "11")) <= 2


async def test_a_client_with_no_identity_never_replays_a_call_after_its_session_is_gone() -> None:
    """Kills: the replay of a sticky call after "Session not found" reaching clients that have no identity layer."""
    from library_ioa.plugins.http_layer import HttpCallTransport as Plain

    seen_calls = []

    def gate(request: Seen) -> Reply | None:
        if request.rpc_method() == "tools/call":
            seen_calls.append(1)
            return Reply(
                404, body={"jsonrpc": "2.0", "id": "x", "error": {"code": -32600, "message": "Session not found"}}
            )
        return None

    with FakeMcp(gate=gate) as server:
        transport = Plain(url=server.url + "/mcp")
        client = MCPClient(url=server.url + "/mcp", http_client=lambda: transport.aclient)
        await client.connect()
        try:
            try:
                await client.call_tool("echo", {})
            except Exception as exc:  # whatever the SDK raises for a gone session: the point is how often it was sent
                failed = exc
            else:
                failed = None
            assert failed is not None
        finally:
            await client.disconnect()
            transport.stop()
    assert seen_calls == [1]  # sent once

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound verification on a served MCP app: a refused caller gets a 401 and the app never runs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from library_ioa.plugins.mcp.server import MCPToolServerFactory
from library_ioa.plugins.mcp.server.inbound import InboundGate
from mas.library.standard.lib.identity.principal import current_principal
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.library.standard.testing.identity_signed_badge import BADGE_HEADER, sign_badge
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from test_wiring_conversations import _open, _Probe
from wiring_builders import key_ref

KEY = "k" * 16


class _App:
    """A stand-in for the SDK's app: records what it was asked and what principal was bound meanwhile."""

    def __init__(self) -> None:
        self.runs: list[dict[str, str]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.runs.append(dict(current_principal()))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _layer(**extra: Any) -> IdentityLayer:
    return IdentityLayer(
        identity="srv",
        scheme="signed_badge",
        params={"key": key_ref(KEY), "subject": "server", "requirements": ["group"], **extra},
        agent_id="srv-agent",
    )


async def test_a_caller_without_a_badge_is_challenged_and_the_app_never_runs() -> None:
    """Kills: a gate that lets a POST through before verifying; a refusal that is not a 401 with WWW-Authenticate."""
    app = _App()
    gate = InboundGate(app, _layer().serve)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://srv") as client:
        refused = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert refused.status_code == 401 and refused.headers["www-authenticate"].startswith("Bearer")
        assert refused.json()["error"]["code"] == "refused:no_badge" and app.runs == []
        forged = await client.post(
            "/mcp", json={}, headers={BADGE_HEADER: sign_badge("wrong-key-0000", "mallory", {"group": "x"})}
        )
        assert forged.status_code == 401 and app.runs == []
        ok = await client.post("/mcp", json={}, headers={BADGE_HEADER: sign_badge(KEY, "alice", {"group": "infra"})})
        assert ok.status_code == 200 and app.runs == [{"subject": "alice", "actor": "srv-agent"}]


async def test_the_stream_get_and_the_session_delete_are_gated_and_a_bare_factory_serves_the_bare_app() -> None:
    """Kills: a gate that lets GET and DELETE through, so a session id alone reads or ends a session."""
    app = _App()
    gate = InboundGate(app, _layer().serve)
    good = {BADGE_HEADER: sign_badge(KEY, "alice", {"group": "infra"})}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://srv") as client:
        for method in ("GET", "DELETE"):
            refused = await client.request(method, "/mcp", headers={"mcp-session-id": "s1"})
            assert refused.status_code == 401, method
            assert refused.json()["error"]["code"] == "refused:no_badge"
        assert app.runs == []
        assert (await client.get("/mcp", headers=good)).status_code == 200
    assert not isinstance(MCPToolServerFactory().asgi_app(), InboundGate)
    assert isinstance(MCPToolServerFactory(inbound=_layer().serve).asgi_app(), InboundGate)


async def _listing(client: httpx.AsyncClient, headers: dict[str, str], session: str) -> httpx.Response:
    held = {"mcp-session-id": session, "mcp-protocol-version": "2025-06-18"}
    return await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers={**headers, **held}
    )


class _AsUser:
    """The SDK app with the user a caller named in ``x-owner`` (what an authenticating layer does for a request)."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            owner = dict(scope["headers"]).get(b"x-owner", b"").decode()
            if owner:
                scope["user"] = AuthenticatedUser(AccessToken(token="", client_id=owner, scopes=[]))
        await self._app(scope, receive, send)


async def test_sdk_binds_session_to_creator() -> None:
    """The contract the gate relies on, with no gate: a session opened by one requestor is not found by another. Kills:
    an SDK release that drops the binding (the gate keeps no table of its own any more)."""
    factory = MCPToolServerFactory()
    app = _AsUser(factory.asgi_app())
    a, b = "own:" + "a" * 40, "own:" + "b" * 40
    async with factory._server.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
            sid = (await c.post("/mcp", json=_Probe.INIT, headers={**_Probe.H, "x-owner": a})).headers["mcp-session-id"]
            assert (await _listing(c, {**_Probe.H, "x-owner": a}, sid)).status_code == 200
            other = await _listing(c, {**_Probe.H, "x-owner": b}, sid)
            assert other.status_code == 404 and "Session not found" in other.text


async def test_a_gated_session_used_by_another_owner_is_not_found() -> None:
    """Kills: a gate that does not hand the verified owner to the SDK as the requestor (the session was then open to
    any verified caller that learned its id), and a gate that answers 403 where the SDK says 404."""
    factory = MCPToolServerFactory(inbound=_layer().serve)
    app = factory.asgi_app()
    async with factory._server.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
            sid = (await _open(c, "alice")).headers["mcp-session-id"]
            stolen = await _listing(c, _Probe.who("mallory"), sid)
            ghost = await _listing(c, _Probe.who("mallory"), "not-a-session")
            assert stolen.status_code == ghost.status_code == 404 and stolen.text == ghost.text
            assert (await _listing(c, _Probe.who("alice"), sid)).status_code == 200
            assert (await c.delete("/mcp", headers={**_Probe.who("mallory"), "mcp-session-id": sid})).status_code == 404
            assert (await _listing(c, _Probe.who("alice"), sid)).status_code == 200


async def test_an_owner_is_never_capped_by_the_gate_and_idle_sessions_go_with_the_sdk() -> None:
    """Kills: a per-owner count that leaks (sessions the SDK dropped kept counting: a permanent 429 for the owner)."""
    factory = MCPToolServerFactory(inbound=_layer().serve)
    app = factory.asgi_app(session_idle_timeout=0.3)
    async with factory._server.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
            first = (await _open(c, "alice")).headers["mcp-session-id"]
            codes = {(await _open(c, "alice")).status_code for _ in range(1100)}
            assert codes == {200}
            await asyncio.sleep(0.8)
            assert (await _listing(c, _Probe.who("alice"), first)).status_code == 404  # the SDK let it go
            assert (await _open(c, "alice")).status_code == 200


def test_an_mcp_server_with_an_inbound_chain_is_not_served_over_another_transport() -> None:
    import pytest

    """Kills: run(transport="sse") serving the tools unverified while the factory holds a verifier."""
    from library_ioa.plugins.mcp.server import MCPToolServerFactory

    factory = MCPToolServerFactory(inbound=lambda handler: handler)
    for transport in ("sse", "stdio"):
        with pytest.raises(ValueError, match="streamable-http"):
            factory.run(transport=transport)

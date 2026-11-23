#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound verification for an MCP server: the layers' inbound chain in front of the streamable-HTTP app.

This is a hook of ``MCPToolServerFactory(inbound=...)``: a program that builds the factory with the chain of a stack
gets it. Neither ``mas-mcp serve`` nor ``mas-ctl`` builds that chain from a row's pipeline today, so a deployment
started from them serves its tools as it always did; ``run()`` refuses an inbound chain on any transport but
streamable-http.
A caller reaches the Starlette app the SDK builds; this ASGI wrapper runs the stack's inbound chain (``Stack.serve``,
which an identity layer's ``serve`` is part of) on every HTTP
request (POST, and the stream GET and the session DELETE) before the SDK sees it. A layer that refuses the caller
raises ``PeerChallenge`` and the request is answered 401 with ``WWW-Authenticate``; the MCP app never runs.

A session id alone does not protect a session, and this gate keeps no table of who holds one. When a verifying layer
says who is calling (``Envelope.meta["owner"]``), the request runs as an authenticated user whose client id is that
owner, and the MCP SDK binds a session to the requestor that opened it: another requestor that names the session gets
the answer for an unknown session (404 "Session not found"), exactly as a restart or an idle timeout produces. The
binding lives and dies with the session in the SDK (``DELETE``, its idle timeout, a crash), so there is nothing here to
expire, to fill up or to leak. The SDK's own ``max_sessions`` (503) bounds the sessions in all; a per-owner cap needs
an SDK hook. With no verifying layer in the chain nothing is bound and the app is served as it was.

If the server is also given the SDK's own authentication, the SDK overwrites the user inside the app and sessions bind
to its token principal instead: refuse that combination where the server is configured.

The verified principal is bound while the app handles the request, in the same task. What this does not do: the
SDK's session manager may run a tool call in a task of its own, where that binding is not visible (a tool function
reads no principal today), and a verifier that needs message text (A2A-style message-level evidence) has none here:
an MCP request body is JSON-RPC, never text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mas.library.standard.lib.stack import Envelope, Handler, PeerChallenge
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.responses import JSONResponse

from library_ioa.plugins.http_layer.challenge import challenge_headers


def _body(refused: PeerChallenge) -> dict[str, Any]:
    body: dict[str, Any] = {"code": refused.code, "challenge": refused.challenge}
    if refused.wire is not None:
        body["wire"] = refused.wire
    return body


class InboundGate:
    """ASGI middleware: ``inbound`` wraps the handler that lets the request through to ``app``."""

    def __init__(self, app: Any, inbound: Callable[[Handler], Handler]) -> None:
        self._app = app

        async def proceed(envelope: Envelope) -> None:
            scope, receive, send = envelope.meta["asgi"]
            owner = str(envelope.meta.get("owner") or "")
            if owner:
                # the SDK binds a session to the requestor that opened it: the owner key is that requestor
                scope["user"] = AuthenticatedUser(AccessToken(token="", client_id=owner, scopes=[]))
            await self._app(scope, receive, send)

        self._chain = inbound(proceed)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        # headers are what the caller sent; the transport's own facts go in meta, never from the caller
        envelope = Envelope(
            payload="",
            headers={k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]},
            meta={"protocol": "mcp", "asgi": (scope, receive, send)},
        )
        try:
            await self._chain(envelope)
        except PeerChallenge as refused:
            response = JSONResponse(
                {"error": _body(refused)},
                status_code=401,
                headers=challenge_headers(refused, "Bearer"),
            )
            await response(scope, receive, send)

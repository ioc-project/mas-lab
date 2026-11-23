#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Secret headers go only to the origin they were configured for, revealed per request.

The servers here are real loopback HTTP servers: a redirect and an agent card are followed by the real
clients, not by mocks.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from library_ioa.plugins.a2a.client import A2AClient
from library_ioa.plugins.http_layer.transport import HttpCallTransport
from library_ioa.plugins.mcp.layer import McpLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.secrets import SecretRef
from secret_fakes import bound_secrets

CANARY = "CANARY-7f3a9c"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def _server(handler: type[BaseHTTPRequestHandler], port: int) -> Iterator[None]:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


def _recorder(seen: list[tuple[str, dict[str, str]]], label: str, respond: Any) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return None

        def _handle(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)
            seen.append((label, {k.lower(): v for k, v in self.headers.items()}))
            respond(self)

        do_GET = _handle
        do_POST = _handle

    return Handler


def _secret_seen(seen: list[tuple[str, dict[str, str]]], label: str) -> bool:
    return any(where == label and CANARY in " ".join(headers.values()) for where, headers in seen)


def _store(**values: str) -> Any:
    return bound_secrets(values, ttl_s=0.0)  # ttl 0: a rotation is seen on the next request


# --- MCP over http: redirects ------------------------------------------------------------------------


def _redirect_to(port: int):
    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(307)
        handler.send_header("Location", f"http://localhost:{port}/mcp")
        handler.send_header("content-length", "0")
        handler.end_headers()

    return respond


def _answer(status: int):
    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(status)
        handler.send_header("content-length", "0")
        handler.end_headers()

    return respond


MCP_HEADERS = {"Authorization": {"secret": {"name": "tok", "prefix": "Bearer "}}, "X-Api-Key": {"secret": "key"}}


def test_mcp_http_step_does_not_follow_a_redirect_to_another_origin_with_secret_headers() -> None:
    # kills: follow_redirects on the transport's client copying X-Api-Key to the redirect's origin
    seen: list[tuple[str, dict[str, str]]] = []
    first, other = _free_port(), _free_port()
    with (
        _store(tok=CANARY, key=CANARY),
        _server(_recorder(seen, "A", _redirect_to(other)), first),
        _server(_recorder(seen, "B", _answer(500)), other),
    ):
        steps = McpLayer.protocol_layers({"url": f"http://127.0.0.1:{first}/mcp", "headers": MCP_HEADERS})
        transport = HttpCallTransport.from_params(steps[0]["params"])

        async def go() -> None:
            await transport.aclient.post(transport.url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})

        with pytest.raises(AgentCommError) as raised:
            asyncio.run(go())
    assert _secret_seen(seen, "A")
    assert not any(where == "B" for where, _ in seen)
    assert f"http://localhost:{other}" in str(raised.value)
    assert CANARY not in str(raised.value)


# --- per-request reveal --------------------------------------------------------------------------------


def test_http_call_transport_picks_up_a_rotated_header_and_keeps_none_in_the_client() -> None:
    # kills: headers revealed once into httpx.AsyncClient(headers=...)
    seen: list[httpx.Request] = []
    with _store(key="one") as resolver:
        transport = HttpCallTransport.from_params(
            {"url": "http://127.0.0.1:9/v1", "headers": {"X-Api-Key": {"secret": "key"}}}
        )

        async def go() -> None:
            client = transport.aclient
            client._transport = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200))
            for _ in range(2):
                await client.get(transport.url)
                resolver.store.put("key", "two")
            assert "x-api-key" not in client.headers

        asyncio.run(go())
    assert [request.headers["x-api-key"] for request in seen] == ["one", "two"]


@pytest.mark.parametrize(
    ("url", "target", "named"),
    [
        ("https://gw.example/v1", "https://elsewhere.example/v1", "https://elsewhere.example"),
        ("http://gw.example/v1", "http://gw.example/v1", "plain http"),
    ],
)
def test_http_call_transport_with_a_secret_header_refuses_another_origin_or_plain_http(
    url: str, target: str, named: str
) -> None:
    with _store(key=CANARY):
        transport = HttpCallTransport.from_params({"url": url, "headers": {"X-Api-Key": {"secret": "key"}}})
        seen: list[httpx.Request] = []
        client = transport.client
        client._transport = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200))
        with pytest.raises(AgentCommError) as raised:
            client.get(target)
    assert seen == []
    assert named in str(raised.value) and CANARY not in str(raised.value)


# --- A2A outbound ------------------------------------------------------------------------------------


def _card_server(port: int):
    interface = {"url": f"http://127.0.0.1:{port}/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    card = {"name": "x", "description": "d", "version": "1", "capabilities": {}, "skills": []}
    card |= {"defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"]}
    body = json.dumps({**card, "supportedInterfaces": [interface]}).encode()

    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    return respond


AUTH = {"Authorization": SecretRef(name="peer", prefix="Bearer ")}


@pytest.mark.parametrize("allowed", [False, True])
def test_a2a_client_sends_its_bearer_to_a_host_the_agent_card_names_only_when_listed(allowed: bool) -> None:
    # kills: the bearer attached to every request, including the card-chosen interface url
    seen: list[tuple[str, dict[str, str]]] = []
    first, other = _free_port(), _free_port()
    extra = [f"http://127.0.0.1:{other}"] if allowed else []
    with (
        _store(peer=CANARY),
        _server(_recorder(seen, "A", _card_server(other)), first),
        _server(_recorder(seen, "B", _answer(500)), other),
    ):
        client = A2AClient(url=f"http://127.0.0.1:{first}", headers=AUTH, allowed_origins=extra)
        with pytest.raises(Exception) as raised:  # noqa: B017 - B answers 500; the test is where the bearer went
            asyncio.run(client.asend_message("hi"))
        client.close()
    assert _secret_seen(seen, "A") and _secret_seen(seen, "B") is allowed
    assert CANARY not in str(raised.value)
    if not allowed:
        assert f"http://127.0.0.1:{other}" in str(raised.value)

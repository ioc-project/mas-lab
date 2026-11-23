#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Secret headers never travel over plain http to a remote host, nor through a proxy to a loopback one."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from library_ioa.plugins.http_layer.transport import HttpCallTransport
from library_ioa.plugins.mcp.client import MCPClient
from secret_fakes import PROVIDER_KEY as KEY
from secret_fakes import bound_secrets


@contextmanager
def _echo(respond: Any) -> Iterator[tuple[str, list[tuple[str, dict[str, str]]]]]:
    seen: list[tuple[str, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def _handle(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)
            headers = {k.lower(): v for k, v in self.headers.items()}
            seen.append((self.path, headers))
            status, extra, body = respond(self.path, headers)
            self.send_response(status)
            for name, value in extra.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _handle
        do_POST = _handle

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()


# --- plain http to a remote host -----------------------------------------------------------------------


def test_mcp_client_refuses_a_secret_header_over_plain_http_to_a_remote_host() -> None:
    # kills: the MCP client revealing a SecretRef header for http://<remote>
    with bound_secrets({"key": KEY}):
        client = MCPClient(url="http://tools.example/mcp", headers={"X-Api-Key": {"secret": "key"}})
        with pytest.raises(ValueError, match="plain http") as raised:
            client._revealed_headers()
    assert KEY not in str(raised.value)


def test_mcp_stdio_env_is_revealed_for_the_child_only() -> None:
    client = MCPClient(command="server", env={"API_KEY": {"secret": "key"}, "MODE": "x"})
    with bound_secrets({"key": KEY}):
        assert client._stdio_env() == {"API_KEY": KEY, "MODE": "x"}
    assert client.env["API_KEY"] != KEY


# --- a loopback target is not reached through the environment's proxy ----------------------------------


def _through_proxy(monkeypatch: pytest.MonkeyPatch, proxy_base: str) -> None:
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, proxy_base)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


def _refuse(_path: str, _headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    return 404, {"content-type": "text/plain"}, b"no"


@pytest.mark.parametrize("kind", ["mcp", "a2a", "http-layer"])
def test_a_secret_header_for_a_loopback_target_never_reaches_a_proxy(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # kills: HTTP_PROXY receiving the plain-http secret header meant for a loopback server
    from library_ioa.plugins.a2a.client import A2AClient

    with _echo(_refuse) as (proxy_base, proxy_seen), _echo(_refuse) as (base, seen), bound_secrets({"key": KEY}):
        _through_proxy(monkeypatch, proxy_base)
        target = base.replace("127.0.0.1", "localhost")
        headers = {"X-Api-Key": {"secret": "key"}}
        if kind == "mcp":
            client: Any = MCPClient(url=f"{target}/mcp", headers=headers)
            run: Any = client.list_tools()
        elif kind == "a2a":
            client = A2AClient(url=target, headers=headers)
            run = client.aget_agent_card()
        else:
            transport = HttpCallTransport.from_params({"url": target, "headers": headers})

            async def get() -> None:
                (await transport.aclient.get(f"{target}/x")).raise_for_status()

            run = get()
        with contextlib.suppress(Exception):  # the target answers 404 to everything
            asyncio.run(asyncio.wait_for(run, 10))
    assert proxy_seen == []
    assert seen and seen[0][1].get("x-api-key") == KEY

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One rule for every secret: https, or a loopback host; nothing else. A loopback LLM call skips the proxy."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest
from mas.runtime.reliability.classes import ClassifiedFailure
from mas.runtime.secrets import SecretRefError, check_plain_http
from mas.runtime.secrets.transport import PlainHttpRefused
from secret_fakes import bound_secrets

KEY = "sk-canary-Ph44"


@pytest.mark.parametrize("url", ["https://gw.example/v1", "wss://gw.example/", "http://[::1]/"])
def test_https_or_loopback_is_accepted(url: str) -> None:
    check_plain_http(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://gw.example/v1",
        "gw.example/v1",
        "http://u:p@127.0.0.1/",
        "http://127.0.0.1\\@evil.example/",
    ],
)
def test_anything_else_is_refused_naming_the_origin_only(url: str) -> None:
    with pytest.raises(PlainHttpRefused) as raised:
        check_plain_http(url + "?api_key=key-canary", what="API key")
    assert isinstance(raised.value, SecretRefError) and "API key" in str(raised.value)
    assert "key-canary" not in str(raised.value) and "/v1" not in str(raised.value)


@contextmanager
def _server() -> Iterator[tuple[int, list[str]]]:
    auths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: Any) -> None:
            return None

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            auths.append(self.headers.get("Authorization", ""))
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield server.server_port, auths
    finally:
        server.shutdown()
        server.server_close()


async def _call(api_base: str) -> dict[str, Any]:
    with bound_secrets({"gw": KEY}):
        row = {"api_base": api_base, "api_key": {"secret": "gw"}, "allowed": ["m"], "protocol": "openai"}
        provider = llm_providers_from_manifest(None, llm_proxies={"gw": row})
        return await provider.achat_completion(model="m", messages=[{"role": "user", "content": "hi"}])


async def test_a_remote_plain_http_row_fails_before_anything_is_sent() -> None:
    # ``localhost.`` resolves to this server but is not a loopback name: it stands for a remote host
    with _server() as (port, auths), pytest.raises(ClassifiedFailure) as raised:
        await _call(f"http://localhost.:{port}/v1")
    assert auths == [] and raised.value.code == "PLAIN_HTTP_KEY" and KEY not in str(raised.value)


async def test_a_loopback_llm_call_does_not_go_through_the_environment_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    with _server() as (proxy_port, proxy_auths), _server() as (port, auths):
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy_port}")
        for name in ("http_proxy", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        out = await _call(f"http://127.0.0.1:{port}/v1")
    assert out["content"] == "ok" and proxy_auths == [] and auths == [f"Bearer {KEY}"]

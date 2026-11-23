#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The demo-ui ingest token stays with the configured host: no redirect, no plain http to a remote host, no
proxy for a loopback host."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from mas.lab.benchmark.pipeline.ui_bridge import IngestRefused, open_ingest, post_ingest
from secret_fakes import bound_secrets

TOKEN = "ui-canary-Tk55"


@contextmanager
def _server(respond: Any) -> Iterator[tuple[int, list[dict[str, str]]]]:
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: Any) -> None:
            return None

        def _answer(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)
            seen.append({k.lower(): v for k, v in self.headers.items()})
            status, extra = respond()
            self.send_response(status)
            for name, value in extra.items():
                self.send_header(name, value)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = do_POST = _answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()


def _request(url: str) -> urllib.request.Request:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
    return urllib.request.Request(url, data=b"{}", headers=headers, method="POST")


def test_a_redirect_is_not_followed_with_the_token() -> None:
    # kills: urllib following a redirect to another host, Authorization kept
    status = 307
    with _server(lambda: (200, {})) as (other_port, other_seen):
        with _server(lambda: (status, {"Location": f"http://localhost:{other_port}/collect"})) as (port, seen):
            with pytest.raises(urllib.error.HTTPError) as raised:
                open_ingest(_request(f"http://127.0.0.1:{port}/api/ingest"), timeout=5)
    assert raised.value.code == status
    assert seen and other_seen == []


@pytest.mark.parametrize("url", ["http://192.168.0.222:8001/api/ingest", "http://ui.example/api/ingest"])
def test_the_token_is_refused_over_plain_http_to_a_remote_host(url: str) -> None:
    # kills: the bearer token sent in clear text to a host that is not loopback
    with pytest.raises(IngestRefused, match="plain http") as raised:
        open_ingest(_request(url), timeout=0.1)
    assert TOKEN not in str(raised.value)


def test_a_loopback_host_is_not_reached_through_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: HTTP_PROXY receiving the plain-http token meant for a loopback demo-ui
    with _server(lambda: (200, {})) as (proxy_port, proxy_seen), _server(lambda: (200, {})) as (port, seen):
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy_port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        with open_ingest(_request(f"http://localhost:{port}/api/ingest"), timeout=5) as resp:
            resp.read()
    assert proxy_seen == [] and seen and seen[0]["authorization"] == f"Bearer {TOKEN}"


def test_post_ingest_does_not_send_the_token_to_a_remote_plain_http_host(caplog: pytest.LogCaptureFixture) -> None:
    # kills: post_ingest attaching the token to http://<lan host> (it never raises: a warning says why)
    with bound_secrets({"ui-ingest": TOKEN}):
        post_ingest({"kind": "x"}, url="http://192.168.0.223:9/api/ingest")
    assert any("plain http" in r.getMessage() for r in caplog.records)
    assert all(TOKEN not in r.getMessage() for r in caplog.records)

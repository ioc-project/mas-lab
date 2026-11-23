#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A client that sends a secret to a loopback host goes there without the environment's proxy and
follows no redirect."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from mas.runtime.secrets.transport import httpx_client_kwargs_for, requests_session_for


@contextmanager
def _server() -> Iterator[tuple[int, list[str]]]:
    """A server answering 200 to everything; it records each request line (a proxy sees absolute urls)."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            return None

        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.path)
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def proxy_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """``HTTP_PROXY`` / ``HTTPS_PROXY`` name a recording proxy; nothing is excluded by ``NO_PROXY``."""
    with _server() as (port, seen):
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        yield seen


def _httpx_get(url: str) -> int:
    with httpx.Client(timeout=5, **httpx_client_kwargs_for(url)) as client:
        return client.get(url).status_code


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_an_httpx_client_reaches_a_loopback_host_without_the_environment_proxy(host: str, proxy_env: list[str]) -> None:
    # kills: HTTP_PROXY receiving a plain-http request (and its secret) meant for a loopback server
    with _server() as (port, seen):
        assert _httpx_get(f"http://{host}:{port}/x") == 200
    assert proxy_env == [] and seen == ["/x"]


def test_an_httpx_client_still_goes_through_the_proxy_to_a_remote_host(proxy_env: list[str]) -> None:
    assert httpx_client_kwargs_for("http://remote.example/x") == {}
    assert _httpx_get("http://remote.example/x") == 200
    assert proxy_env == ["http://remote.example/x"]


def test_a_requests_session_reaches_a_loopback_host_without_the_environment_proxy(proxy_env: list[str]) -> None:
    with _server() as (port, seen), requests_session_for(f"http://127.0.0.1:{port}/") as session:
        assert session.get(f"http://127.0.0.1:{port}/x", timeout=5).status_code == 200
    assert proxy_env == [] and seen == ["/x"]


def test_a_requests_session_still_goes_through_the_proxy_to_a_remote_host(proxy_env: list[str]) -> None:
    with requests_session_for("http://remote.example/") as session:
        assert session.get("http://remote.example/x", timeout=5).status_code == 200
    assert proxy_env == ["http://remote.example/x"]


@contextmanager
def _redirecting(location: str) -> Iterator[tuple[int, list[tuple[str, str | None]]]]:
    """A server answering 307 to ``location``; it records each request line and its ``x-api-key``."""
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            return None

        def do_POST(self) -> None:  # noqa: N802
            seen.append((self.path, self.headers.get("x-api-key")))
            self.send_response(307)
            self.send_header("location", location)
            self.send_header("content-length", "0")
            self.end_headers()

        do_GET = do_POST  # noqa: N815

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()


def test_a_requests_session_to_a_loopback_host_does_not_follow_a_redirect_to_another_host(
    proxy_env: list[str],
) -> None:
    # kills: a loopback endpoint's 307 sending the session's secret headers to another host (directly or via the proxy)
    with _redirecting("http://collector.evil.test/v1/traces") as (port, seen):
        url = f"http://127.0.0.1:{port}/v1/traces"
        with requests_session_for(url) as session:
            session.headers["x-api-key"] = "SECRET-HDR"
            response = session.post(url, data=b"x", timeout=5)
            again = session.post(url, data=b"x", timeout=5, allow_redirects=True)
    assert response.status_code == again.status_code == 307
    assert proxy_env == [] and seen == [("/v1/traces", "SECRET-HDR")] * 2


def test_a_requests_session_to_a_remote_host_does_not_follow_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: the proxied (non-loopback) session following a 307 to another host with the secret headers
    with _redirecting("http://other.example/steal") as (port, seen):
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        with requests_session_for("http://remote.example/") as session:
            session.headers["x-api-key"] = "SECRET-HDR"
            assert session.post("http://remote.example/x", data=b"x", timeout=5).status_code == 307
    assert seen == [("http://remote.example/x", "SECRET-HDR")]


def test_a_urllib_opener_to_a_loopback_host_does_not_follow_a_redirect_through_the_proxy(
    proxy_env: list[str],
) -> None:
    # kills: a loopback endpoint's 307 to a remote host reaching the environment's proxy
    import urllib.error
    import urllib.request

    from mas.runtime.secrets.transport import opener_for

    with _redirecting("http://remote.example/steal") as (port, seen):
        url = f"http://127.0.0.1:{port}/q"
        request = urllib.request.Request(url, headers={"x-api-key": "SECRET-HDR"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            opener_for(url).open(request, timeout=5)
        caught.value.close()
    assert caught.value.code == 307
    assert proxy_env == [] and seen == [("/q", "SECRET-HDR")]

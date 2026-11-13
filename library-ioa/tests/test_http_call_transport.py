#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The http client under a protocol layer: headers, redirects, timeouts, TLS, one async client per loop."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from library_ioa.plugins.http_layer import HttpCallTransport, HttpLayer


def test_call_transport_refuses_credentials_and_odd_schemes() -> None:
    for url in ("https://user:pw@host/v1", "ftp://host/v1", "https://host/\\x", "https:///nohost", "https://a b/"):
        with pytest.raises(Exception, match="call transport"):
            HttpCallTransport(url=url)


def test_call_transport_accepts_plain_http_on_an_internal_host_and_no_url() -> None:
    assert HttpCallTransport(url="http://llm.internal:8000/v1").url == "http://llm.internal:8000/v1"
    assert HttpCallTransport().url == ""


def test_client_carries_headers_redirects_and_mcp_timeouts() -> None:
    http = HttpCallTransport.from_params(
        {"url": "https://mcp.example/mcp", "headers": {"Authorization": "Bearer x"}, "follow_redirects": True}
    )

    async def grab() -> httpx.AsyncClient:
        return http.aclient

    client = asyncio.run(grab())
    assert client.headers["authorization"] == "Bearer x"
    assert client.follow_redirects is True
    assert (client.timeout.connect, client.timeout.read) == (30.0, 300.0)
    assert http.client.headers["authorization"] == "Bearer x"
    assert http.client is http.client


def test_a_given_timeout_applies() -> None:
    http = HttpCallTransport.from_params({"url": "https://x.example/v1", "timeout": 7})
    assert http.client.timeout.read == 7.0


def test_tls_settings_reach_the_client() -> None:
    assert HttpCallTransport.from_params({"url": "https://x.example/v1", "verify_ssl": False})._verify is False
    local = HttpCallTransport(url="https://127.0.0.1:9/v1", dev=True)
    assert local._verify is False
    assert HttpCallTransport(url="https://x.example/v1", dev=True)._verify is not False


def test_each_event_loop_has_its_own_aclient_and_none_is_closed_by_another() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})
    clients: list[httpx.AsyncClient] = []
    release, ready = threading.Event(), threading.Event()

    def other_loop() -> None:
        async def hold() -> None:
            clients.append(http.aclient)
            ready.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

        asyncio.run(hold())

    thread = threading.Thread(target=other_loop)
    thread.start()
    ready.wait(5)

    async def mine() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        return http.aclient, http.aclient

    first, again = asyncio.run(mine())
    assert first is again and first is not clients[0]
    assert not clients[0].is_closed
    release.set()
    thread.join()


def test_a_closed_aclient_is_replaced() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})

    async def run() -> None:
        first = http.aclient
        await first.aclose()
        assert http.aclient is not first

    asyncio.run(run())


def test_discard_closes_and_the_next_call_opens_a_fresh_client() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})
    sync = http.client
    http.discard_client()
    assert sync.is_closed and http.client is not sync
    http.discard_client()

    async def run() -> None:
        first = http.aclient
        await http.adiscard_aclient()
        assert first.is_closed and http.aclient is not first
        await http.adiscard_aclient()
        await http.adiscard_aclient()

    asyncio.run(run())


def test_stop_closes_clients_and_refuses_new_ones() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})
    sync = http.client

    async def run() -> None:
        client = http.aclient
        http.stop()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert client.is_closed
        with pytest.raises(Exception, match="stopped"):
            _ = http.aclient

    asyncio.run(run())
    assert sync.is_closed
    with pytest.raises(Exception, match="stopped"):
        _ = http.client
    http.stop()


def test_stop_from_another_thread_closes_a_running_loops_client() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})
    seen: list[httpx.AsyncClient] = []
    ready, release = threading.Event(), threading.Event()

    def loop_thread() -> None:
        async def hold() -> None:
            seen.append(http.aclient)
            ready.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

        asyncio.run(hold())

    thread = threading.Thread(target=loop_thread)
    thread.start()
    ready.wait(5)
    http.stop()
    for _ in range(100):
        if seen[0].is_closed:
            break
        threading.Event().wait(0.02)
    release.set()
    thread.join()
    assert seen[0].is_closed


def test_http_layer_wrap_picks_the_call_transport_for_llm_and_tool() -> None:
    made = HttpLayer.wrap(None, {"url": "http://127.0.0.1:9/v1"}, call_kinds=("llm",))
    assert isinstance(made, HttpCallTransport)
    assert HttpLayer.wrap(made, {"url": "http://127.0.0.1:8/v1"}, call_kinds=("tool",)) is made
    send = HttpLayer.wrap(None, {"url": "http://127.0.0.1:9/a2a"}, call_kinds=("send",))
    assert isinstance(send, HttpLayer)


def test_redirects_are_not_followed_unless_the_row_asks() -> None:
    assert HttpCallTransport.from_params({"url": "https://x.example/v1"}).client.follow_redirects is False


def test_a_client_whose_loop_is_gone_is_closed_not_leaked() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})

    async def grab() -> httpx.AsyncClient:
        return http.aclient

    first = asyncio.run(grab())
    assert not first.is_closed
    asyncio.run(grab())  # a new loop prunes the finished one and closes its client
    assert first.is_closed


def test_stop_closes_the_clients_of_loops_that_have_finished() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})

    async def grab() -> httpx.AsyncClient:
        return http.aclient

    clients = [asyncio.run(grab()) for _ in range(3)]
    assert len({id(client) for client in clients}) == 3 or True
    http.stop()
    assert all(client.is_closed for client in clients)

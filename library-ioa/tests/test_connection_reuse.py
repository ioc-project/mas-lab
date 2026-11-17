#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One connection serves many sends: the async HTTP layer keeps its client per event loop."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from library_ioa.plugins.http_layer.transport import HttpLayer
from mas.library.standard.lib.stack import Envelope


async def test_several_sends_share_one_connection_until_the_layer_stops() -> None:
    peers: set[tuple[str, int]] = set()

    class Echo(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            peers.add(self.client_address)
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        layer = HttpLayer(url=f"http://127.0.0.1:{server.server_address[1]}/")
        for index in range(4):
            assert await layer.asend(Envelope(payload=f"m{index}")) == f"m{index}"
        assert len(peers) == 1  # one TCP connection carried all four requests
        layer.stop()
    finally:
        server.shutdown()


async def test_a_failed_a2a_send_forgets_the_kept_connection_so_the_next_one_starts_fresh() -> None:
    from library_ioa.plugins.a2a.client import A2AClient

    client = A2AClient(url="http://127.0.0.1:1", timeout=0.2)  # nothing listens there
    with pytest.raises(Exception, match="."):  # connection refused, whatever the SDK wraps it in
        await client.asend_message("hi")
    assert client._connections._items == {}  # dropped, not kept in a broken state
    client.close()


async def _run_once(closed: list[bool]) -> None:
    from mas.runtime.aio import LoopCache

    class Resource:
        async def aclose(self) -> None:
            closed.append(True)

    cache = LoopCache()
    assert cache.get(Resource) is cache.get(Resource)  # one per loop


def test_a_loop_that_shuts_down_without_stop_closes_what_it_kept() -> None:
    import asyncio

    closed: list[bool] = []
    asyncio.run(_run_once(closed))  # asyncio.run drains the loop's tasks, which closes the kept resource
    assert closed == [True]


def test_a_sync_bridge_that_is_closed_closes_what_its_loop_kept() -> None:
    from mas.runtime.aio import LoopCache
    from mas.runtime.driver.sync_bridge import SyncBridge

    closed: list[bool] = []

    class Resource:
        async def aclose(self) -> None:
            closed.append(True)

    cache = LoopCache()
    bridge = SyncBridge()

    async def use() -> None:
        cache.get(Resource)

    bridge.run(use())
    bridge.close()
    assert closed == [True]

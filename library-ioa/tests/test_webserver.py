# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Background Uvicorn can host more than one listener in one process."""

from __future__ import annotations

import socket
import time

import httpx
from fastapi import FastAPI
from library_ioa.plugins.webserver import UvicornWebServer


def _hold_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            sockets.append(listener)
            ports.append(int(listener.getsockname()[1]))
    finally:
        for listener in sockets:
            listener.close()
    return ports


def _wait(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_exc: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05)
    raise RuntimeError(f"server did not listen on 127.0.0.1:{port}") from last_exc


def test_two_background_servers_listen_on_distinct_ports() -> None:
    def app_for(name: str) -> FastAPI:
        app = FastAPI()

        @app.get("/")
        def root() -> dict[str, str]:
            return {"name": name}

        return app

    port_a, port_b = _hold_ports(2)
    webserver = UvicornWebServer()
    handle_a = webserver.serve_background(app_for("a"), host="127.0.0.1", port=port_a)
    handle_b = webserver.serve_background(app_for("b"), host="127.0.0.1", port=port_b)
    try:
        _wait(port_a)
        _wait(port_b)
        assert httpx.get(f"http://127.0.0.1:{port_a}/").json() == {"name": "a"}
        assert httpx.get(f"http://127.0.0.1:{port_b}/").json() == {"name": "b"}
    finally:
        handle_a.close()
        handle_b.close()

# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Background Uvicorn can host more than one listener in one process."""

from __future__ import annotations

import asyncio
import contextvars
import socket
import sys
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


class _FakeUvicorn:
    """Stands in for the ``uvicorn`` module: records the config and where ``run`` and ``serve`` execute."""

    def __init__(self) -> None:
        self.configs: list[dict] = []
        self.run_saw: list[str] = []
        self.served = 0
        outer = self

        class Config:
            def __init__(self, app, **kwargs) -> None:
                outer.configs.append({"app": app, **kwargs})

        class Server:
            def __init__(self, config) -> None:
                self.config = config
                self.should_exit = False
                self.install_signal_handlers = True

            def run(self) -> None:
                outer.run_saw.append(_CALLER.get())

            async def serve(self) -> None:
                outer.served += 1

        self.Config = Config
        self.Server = Server


_CALLER: contextvars.ContextVar[str] = contextvars.ContextVar("webserver_test_caller", default="empty")


def test_serve_async_serves_with_the_given_address(monkeypatch) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake)

    asyncio.run(UvicornWebServer().serve_async("app", host="127.0.0.1", port=1234))

    assert fake.served == 1
    assert fake.configs == [{"app": "app", "host": "127.0.0.1", "port": 1234, "log_level": "warning"}]


def test_serve_background_runs_the_server_in_a_thread_with_an_empty_context(monkeypatch) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    _CALLER.set("caller")

    handle = UvicornWebServer().serve_background("app", host="127.0.0.1", port=1234)
    handle.thread.join(timeout=5)

    assert fake.run_saw == ["empty"]
    assert handle.server.install_signal_handlers is False
    assert handle.thread.daemon is True
    handle.close()
    assert handle.server.should_exit is True

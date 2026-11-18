#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``daemon.main`` starts its IPC and HTTP threads (it builds them with ``new_thread`` and keyword arguments)."""

from __future__ import annotations

import signal
import time

import pytest
from mas.lab.controller import daemon


@pytest.fixture
def restored_signals_and_shutdown():
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in handlers.items():
        signal.signal(sig, handler)
    daemon._shutdown.clear()


def _wait_for(started: list, count: int) -> None:
    deadline = time.monotonic() + 5
    while len(started) < count and time.monotonic() < deadline:
        time.sleep(0.01)


def test_main_starts_the_ipc_and_the_http_thread(temp_mas_home, monkeypatch, restored_signals_and_shutdown) -> None:
    started: list = []
    monkeypatch.setattr(daemon, "_serve_socket", lambda socket: started.append(("ipc", socket)))
    monkeypatch.setattr(daemon, "_serve_http", lambda **kwargs: started.append(("http", kwargs)))
    monkeypatch.setattr(daemon._sessions, "should_shutdown", lambda api: True)

    assert daemon.main(["--port", "18999", "--host", "127.0.0.1"]) == 0

    _wait_for(started, 2)
    by_name = dict(started)
    assert set(by_name) == {"ipc", "http"}
    assert by_name["http"]["host"] == "127.0.0.1" and by_name["http"]["port"] == 18999
    assert by_name["http"]["settings"].port == 18999


def test_main_without_http_starts_only_the_ipc_thread(
    temp_mas_home, monkeypatch, restored_signals_and_shutdown
) -> None:
    started: list = []
    monkeypatch.setattr(daemon, "_serve_socket", lambda socket: started.append("ipc"))
    monkeypatch.setattr(daemon, "_serve_http", lambda **kwargs: started.append("http"))
    monkeypatch.setattr(daemon._sessions, "should_shutdown", lambda api: True)

    assert daemon.main(["--no-http"]) == 0

    _wait_for(started, 1)
    time.sleep(0.1)
    assert started == ["ipc"]

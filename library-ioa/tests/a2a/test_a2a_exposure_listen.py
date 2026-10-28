#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Two A2A exposures in one process keep distinct agent cards."""

from __future__ import annotations

import socket
import time

import httpx
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.a2a.exposure import A2AExposure
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


def test_two_background_exposures_serve_distinct_agent_cards() -> None:
    port_assistant, port_greeter = _hold_ports(2)
    webserver = UvicornWebServer()
    assistant = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "use-and-deploy",
            "url": f"http://127.0.0.1:{port_assistant}",
        },
        webserver=webserver,
    )
    greeter = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "use-and-deploy",
            "url": f"http://127.0.0.1:{port_greeter}",
        },
        webserver=webserver,
    )
    handle_assistant = assistant.serve_background(
        {"metadata": {"name": "assistant"}, "spec": {"description": "entry"}},
        lambda prompt, **_: {"text": "ok"},
    )
    handle_greeter = greeter.serve_background(
        {"metadata": {"name": "greeter"}, "spec": {"description": "peer"}},
        lambda prompt, **_: {"text": "ok"},
    )
    try:
        _wait(port_assistant)
        _wait(port_greeter)
        assistant_card = httpx.get(
            f"http://127.0.0.1:{port_assistant}/.well-known/agent-card.json",
            timeout=5,
        )
        greeter_card = httpx.get(
            f"http://127.0.0.1:{port_greeter}/.well-known/agent-card.json",
            timeout=5,
        )
        assert assistant_card.status_code == 200
        assert greeter_card.status_code == 200
        assert assistant_card.json()["name"] == "assistant"
        assert greeter_card.json()["name"] == "greeter"
    finally:
        handle_assistant.close()
        handle_greeter.close()


def test_a2a_agent_comm_reaches_an_in_process_peer() -> None:
    (port,) = _hold_ports(1)
    url = f"http://127.0.0.1:{port}"
    received: list[str] = []
    handle = A2AExposure(
        endpoint={"protocol": "a2a", "usage": "deploy", "url": url},
        webserver=UvicornWebServer(),
    ).serve_background(
        {"metadata": {"name": "peer"}, "spec": {"description": "peer"}},
        lambda prompt, **_: (received.append(prompt) or {"text": f"peer:{prompt}"}),
    )
    comm = A2AAgentComm(url=url, timeout=10)
    try:
        _wait(port)
        assert comm.send("peer", "hello") == "peer:hello"
        assert received == ["hello"]
    finally:
        comm.close()
        handle.close()

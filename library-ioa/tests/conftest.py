#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pin a2a-sdk self-instrumentation off before tests import a2a."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import mas.third_party_otel  # noqa: F401
import pytest

pytest_plugins = ["mas.runtime.hostenv_pytest"]


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "slim: runs against an in-process SLIM node (slim-bindings, part of the dev group)"
    )


@pytest.fixture(scope="session")
def slim_node() -> Iterator[str]:
    """A real SLIM node inside this process (the bindings' own server), as an ``http://127.0.0.1:PORT`` url.

    Skips when the ``slim`` extra is not installed.
    """
    sb = pytest.importorskip("slim_bindings")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    if not sb.is_initialized():
        sb.initialize_with_defaults()
    service = sb.create_service("mas-test-node")
    service.run_server(sb.new_insecure_server_config(f"127.0.0.1:{port}"))
    deadline = time.monotonic() + 5
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        service.stop_server(f"127.0.0.1:{port}")


@pytest.fixture(scope="session")
def slim_url(slim_node: str) -> str:
    """The same in-process node as ``slim_node``: one server, two names for existing tests."""
    return slim_node

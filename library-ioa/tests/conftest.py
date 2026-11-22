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
def slim_url() -> Iterator[str]:
    """An in-process SLIM node on a free loopback port. No external node, no network."""
    import slim_bindings

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    node = slim_bindings.create_service("test-node")
    node.run_server(slim_bindings.new_insecure_server_config(f"127.0.0.1:{port}"))
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
        node.shutdown()

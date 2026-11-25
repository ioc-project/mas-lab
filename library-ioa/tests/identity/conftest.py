#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Register the wiring test schemes as ``identity_scheme`` plugins and keep every test's brokers apart."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from mas.library.standard.lib.identity.observe import validate_event
from mas.library.standard.plugins.identity.layer import reset_shared
from mas.library.standard.testing.identity_fakes import bound_secrets
from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream
from mas.runtime.registry import register_plugin, unregister_plugin
from wiring_builders import SECRETS
from wiring_schemes import (
    SCHEMES,
    AdvisoryCaller,
    AlwaysChallenge,
    DataAnswer,
    HttpChallenge,
    InboundRecorder,
    MisaudiencedExchange,
    PeerRecording,
    ReplayingExchange,
    SingleUse,
    TokenPerTask,
    WireExchange,
)


@pytest.fixture(scope="session", autouse=True)
def _wiring_schemes() -> Iterator[None]:
    urns = []
    for name, cls in SCHEMES.items():
        urn = f"mas.identity_scheme.{name}"
        register_plugin(urn, cls, shortcuts=[name])
        urns.append(urn)
    yield
    for urn in urns:
        unregister_plugin(urn)


@pytest.fixture(autouse=True)
def _fresh_brokers() -> Iterator[None]:
    reset_shared()
    TokenPerTask.CALLS.clear()
    InboundRecorder.SEEN.clear()
    HttpChallenge.PEERS.clear()
    HttpChallenge.INBOUND.clear()
    HttpChallenge._issued.clear()
    PeerRecording.SEEN.clear()
    AdvisoryCaller.ANSWERS.clear()
    AlwaysChallenge.VERIFIES.clear()
    AlwaysChallenge.ANSWERS.clear()
    MisaudiencedExchange.VERIFIES.clear()
    ReplayingExchange.FIRST.clear()
    SingleUse.PRESENTED.clear()
    DataAnswer.SEEN.clear()
    WireExchange.PEERS.clear()
    yield
    reset_shared()


@pytest.fixture(autouse=True)
def _llm_key() -> Iterator[None]:
    """The key of the fake LLM server, in the secret store the LLM provider reveals it from at the call."""
    with bound_secrets(SECRETS):
        yield


@pytest.fixture(autouse=True)
def events() -> Iterator[list[dict[str, Any]]]:
    """Every identity event the test publishes, and a hard rule: each one follows the vocabulary's rulebook."""
    stream, seen = EventStream(), []
    stream.subscribe(seen.append)
    token = set_event_stream(stream)
    yield seen
    reset_event_stream(token)
    for event in seen:
        validate_event(event)


@pytest.fixture(scope="session")
def slim_node() -> Iterator[str]:
    """A real SLIM node inside this process, as ``http://127.0.0.1:PORT``. Skips without the ``slim`` extra."""
    import socket

    sb = pytest.importorskip("slim_bindings")
    pytest.importorskip("slima2a")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    if not sb.is_initialized():
        sb.initialize_with_defaults()
    service = sb.create_service("mas-wiring-node")
    service.run_server(sb.new_insecure_server_config(f"127.0.0.1:{port}"))
    yield f"http://127.0.0.1:{port}"
    service.stop_server(f"127.0.0.1:{port}")

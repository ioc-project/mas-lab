# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""A2AAgentComm: intents, probe, and per-call headers on the wire."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.webserver import UvicornWebServer
from mas.library.standard.lib.comm.headers import bound_headers
from mas.library.standard.plugins.system_tools.send import SendTool
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.engine.tool_dispatch import aexecute_engine_tool


async def test_a2a_refuses_inform_and_ask() -> None:
    client = Mock()
    plugin = A2AAgentComm.__new__(A2AAgentComm)
    plugin._client = client
    assert plugin.honoured_send_intents() == frozenset({"task"})
    for intent in ("inform", "ask", "steer"):
        with pytest.raises(AgentCommError, match="not supported"):
            await plugin.asend("worker", "fyi", context=SendContext(intent=intent))
    client.asend_message.assert_not_called()


async def test_an_a2a_link_probe_fetches_the_card_afresh_and_forgets_the_connection_on_failure() -> None:
    client = Mock()
    client.aget_agent_card = AsyncMock(return_value={"name": "remote"})
    plugin = A2AAgentComm.__new__(A2AAgentComm)
    plugin._client = client
    assert await plugin.aprobe("worker") is True
    client.aget_agent_card.assert_awaited_with(fresh=True)

    client.aget_agent_card = AsyncMock(side_effect=OSError("refused"))
    with pytest.raises(AgentCommError, match="refused"):
        await plugin.aprobe("worker")
    client.reset.assert_called_once()


async def test_the_client_keeps_its_card_until_asked_for_a_fresh_one_or_reset() -> None:
    from library_ioa.plugins.a2a.client import A2AClient

    fetched: list[int] = []

    async def fetch(_http: object) -> dict[str, str]:
        fetched.append(1)
        return {"name": f"card-{len(fetched)}"}

    client = A2AClient(url="http://127.0.0.1:1")
    client._sdk_get_agent_card = fetch  # type: ignore[method-assign]
    assert await client.aget_agent_card() == {"name": "card-1"}
    assert await client.aget_agent_card() == {"name": "card-1"}  # kept, not fetched again
    assert await client.aget_agent_card(fresh=True) == {"name": "card-2"}  # a health check asks again
    assert await client.aget_agent_card() == {"name": "card-2"}
    client.reset()  # the next call connects afresh
    assert await client.aget_agent_card() == {"name": "card-3"}
    client.close()


async def test_building_the_sdk_client_emits_the_agent_card() -> None:
    """Mutation: client() fetches the card object and never records it, so send has no discovery event."""
    from unittest.mock import patch

    from library_ioa.plugins.a2a.client import A2AClient
    from mas.runtime.schema.transport import DISCOVERY, TransportEvent
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    class Card:
        def model_dump(self, mode: str = "json", exclude_none: bool = True) -> dict:
            return {"name": "peer", "url": "http://127.0.0.1:1"}

    class Resolver:
        def __init__(self, **_kw) -> None:
            pass

        async def get_agent_card(self):
            return Card()

    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    emit.bind_sink(Sink())
    client = A2AClient(url="http://127.0.0.1:1")
    try:
        with (
            patch("library_ioa.plugins.a2a.client.A2ACardResolver", Resolver),
            patch("library_ioa.plugins.a2a.client.create_client", AsyncMock(return_value=AsyncMock())),
        ):
            connection = await client._connection()
            await connection.client(False)
            await connection.client(False)
    finally:
        client.close()
        emit.reset()
    cards = [e for e in captured if e.kind == DISCOVERY]
    assert len(cards) == 1
    assert cards[0].attributes.get("card", {}).get("name") == "peer"


async def test_call_context_carries_identity_only_when_hooks_are_on() -> None:
    from unittest.mock import patch

    from library_ioa.plugins.a2a.client import A2AClient
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    client = A2AClient(url="http://127.0.0.1:1")
    client.local_agent_id = "planner"
    emit.reset()
    assert client._call_context() is None
    A2AClient._finish_call(None, None)
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    try:
        context = client._call_context()
        assert context is not None
        A2AClient._finish_call(context.state, None)
        with (
            patch("library_ioa.plugins.a2a.client.A2ACardResolver") as resolver,
        ):
            resolver.return_value.get_agent_card = AsyncMock(return_value={"name": "peer"})
            card = await client._sdk_get_agent_card(AsyncMock())
        assert card["name"] == "peer"
    finally:
        emit.reset()


class _Recorder:
    """ASGI wrapper that records the headers and JSON body of every POST the peer receives."""

    def __init__(self, app: Any, seen: list[tuple[dict[str, str], str]]) -> None:
        self._app = app
        self._seen = seen

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self._app(scope, receive, send)
            return
        body = b""
        more = True
        while more:
            event = await receive()
            body += event.get("body", b"")
            more = event.get("more_body", False)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        self._seen.append((headers, body.decode()))
        sent = False

        async def replay() -> Any:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay, send)


class _RecordingExposure(A2AExposure):
    seen: list[tuple[dict[str, str], str]]

    def build_app(self, manifest: dict[str, Any], handler: Any) -> Any:
        return _Recorder(super().build_app(manifest, handler), self.seen)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait(port: int) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


@pytest.fixture
def peer() -> Any:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    exposure = _RecordingExposure(
        endpoint={"protocol": "a2a", "usage": "deploy", "url": url}, webserver=UvicornWebServer()
    )
    exposure.seen = []

    def handler(prompt: str, **_: Any) -> dict[str, str]:
        time.sleep(0.2)  # long enough for concurrent sends to overlap
        return {"text": f"peer:{prompt}"}

    handle = exposure.serve_background({"metadata": {"name": "peer"}, "spec": {"description": "peer"}}, handler)
    try:
        _wait(port)
        yield url, exposure.seen
    finally:
        handle.close()


def _post_for(seen: list[tuple[dict[str, str], str]], text: str) -> dict[str, str]:
    (headers,) = [h for h, body in seen if text in body and "SendMessage" in json.dumps(json.loads(body))]
    return headers


async def test_per_call_headers_reach_the_peer_on_the_json_rpc_post(peer: Any) -> None:
    url, seen = peer
    comm = A2AAgentComm(url=url, headers={"x-static": "s"}, timeout=10)
    try:
        trace = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        assert await comm.asend("peer", "hello", headers={"TraceParent": trace, "baggage": "k=v"}) == "peer:hello"
    finally:
        comm.stop()
    got = _post_for(seen, "hello")
    assert got["traceparent"] == trace
    assert got["baggage"] == "k=v"
    assert got["x-static"] == "s"


async def test_concurrent_sends_keep_their_own_headers(peer: Any) -> None:
    url, seen = peer
    comm = A2AAgentComm(url=url, timeout=10)
    try:
        await asyncio.gather(
            comm.asend("peer", "first", headers={"traceparent": "00-" + "1" * 32 + "-" + "1" * 16 + "-01"}),
            comm.asend("peer", "second", headers={"traceparent": "00-" + "2" * 32 + "-" + "2" * 16 + "-01"}),
            comm.asend("peer", "third"),
        )
    finally:
        comm.stop()
    assert _post_for(seen, "first")["traceparent"].startswith("00-" + "1" * 32)
    assert _post_for(seen, "second")["traceparent"].startswith("00-" + "2" * 32)
    assert "traceparent" not in _post_for(seen, "third")


async def test_a_framing_header_raises_and_nothing_is_sent(peer: Any) -> None:
    url, seen = peer
    comm = A2AAgentComm(url=url, timeout=10)
    try:
        for name in ("Host", "content-length", "Transfer-Encoding"):
            with pytest.raises(AgentCommError, match="itself"):
                await comm.asend("peer", "nope", headers={name: "x"})
    finally:
        comm.stop()
    assert seen == []


async def test_without_call_headers_only_the_static_ones_go_out(peer: Any) -> None:
    url, seen = peer
    comm = A2AAgentComm(url=url, headers={"x-static": "s"}, timeout=10)
    try:
        assert await comm.asend("peer", "plain") == "peer:plain"
    finally:
        comm.stop()
    got = _post_for(seen, "plain")
    assert got["x-static"] == "s"
    assert not {"traceparent", "tracestate", "baggage"} & set(got)


async def test_ambient_hop_headers_reach_the_peer_when_asend_omits_headers(peer: Any) -> None:
    url, seen = peer
    comm = A2AAgentComm(url=url, timeout=10)
    trace = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    try:
        with bound_headers({"traceparent": trace, "baggage": "k=v", "tracestate": "vendor=1"}):
            assert await comm.asend("peer", "ambient") == "peer:ambient"
    finally:
        comm.stop()
    got = _post_for(seen, "ambient")
    assert got["traceparent"] == trace
    assert got["baggage"] == "k=v"
    assert got["tracestate"] == "vendor=1"


async def test_delegate_to_carries_ambient_hop_headers_on_the_json_rpc_post(peer: Any) -> None:
    url, seen = peer
    comm = A2AAgentComm(url=url, timeout=10)
    try:
        tool = SendTool(comm=comm, allowed_peers=["peer"])
        ctx = SimpleNamespace(
            agent_id="planner",
            manifest=None,
            agent_comm=comm,
            agent_comm_routes={"peer": AgentCommRoute("peer", "a2a", comm)},
            delegation_peer_descriptions=None,
            send_peer_descriptions=None,
        )
        trace = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        with bound_headers({"traceparent": trace, "baggage": "k=v"}):
            result = await aexecute_engine_tool(
                "delegate_to_peer", engine_contracts=[tool], ctx=ctx, arguments={"task": "hello"}
            )
        assert result == "peer:hello"
    finally:
        comm.stop()
    got = _post_for(seen, "hello")
    assert got["traceparent"] == trace
    assert got["baggage"] == "k=v"

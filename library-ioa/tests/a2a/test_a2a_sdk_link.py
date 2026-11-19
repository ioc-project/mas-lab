# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""A2AAgentComm accepts headers and does not put them on HTTP."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext


async def test_a2a_agent_comm_accepts_headers_and_drops_them() -> None:
    client = Mock()
    client.asend_message = AsyncMock(return_value={"status": "completed", "result": "remote response"})
    plugin = A2AAgentComm.__new__(A2AAgentComm)
    plugin._client = client

    assert isinstance(plugin, AgentCommContract)
    assert (await plugin.asend("worker", "task", headers={"traceparent": "00-aa-bb-01"})) == "remote response"
    assert (
        await plugin.asend("worker", "task", context=SendContext(headers={"k": "v"}, sender="evil"))
    ) == "remote response"
    assert await plugin.asend("worker", "task", headers={"baggage": "k=v"}) == "remote response"
    client.asend_message.assert_called_with(
        "task",
        context_id="",
        metadata={"mas.correlation_id": "0", "mas.caller_call_id": ""},
    )
    assert client.asend_message.call_count == 3


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
    from unittest.mock import AsyncMock, Mock

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

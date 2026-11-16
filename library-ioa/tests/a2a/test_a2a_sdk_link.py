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

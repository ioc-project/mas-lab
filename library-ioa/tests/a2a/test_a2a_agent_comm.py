# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""A2AAgentComm accepts headers and does not put them on HTTP."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract


def test_a2a_agent_comm_accepts_headers_and_drops_them() -> None:
    client = Mock()
    client.send_message.return_value = {
        "status": "completed",
        "result": "remote response",
    }
    plugin = A2AAgentComm.__new__(A2AAgentComm)
    plugin._client = client

    assert isinstance(plugin, AgentCommContract)
    assert plugin.send("worker", "task", headers={"traceparent": "00-aa-bb-01"}) == "remote response"
    assert (
        asyncio.run(plugin.asend("worker", "task", headers={"baggage": "k=v"}))
        == "remote response"
    )
    client.send_message.assert_called_with(
        "task",
        context_id="",
        metadata={"mas.correlation_id": "0", "mas.caller_call_id": ""},
    )
    assert client.send_message.call_count == 2

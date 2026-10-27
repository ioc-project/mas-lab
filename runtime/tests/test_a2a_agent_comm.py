from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract


def test_local_agent_comm_implements_agent_comm_contract() -> None:
    local = LocalAgentComm(lambda *_: "local response")

    assert isinstance(local, AgentCommContract)
    assert local.send("worker", "task") == "local response"


def test_a2a_agent_comm_implements_the_peer_transport_contract() -> None:
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm

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


def test_a2a_client_sync_bridge_is_safe_inside_async_executor() -> None:
    from library_ioa.plugins.a2a.client import A2AClient

    async def exercise() -> str:
        client = A2AClient()
        try:
            return client._run_sync(asyncio.sleep(0, result="ok"))
        finally:
            client.close()

    assert asyncio.run(exercise()) == "ok"


def test_a2a_executor_emits_streaming_status_and_artifact_events() -> None:
    from unittest.mock import AsyncMock

    from a2a.types import Task, TaskArtifactUpdateEvent, TaskState
    from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor

    queue = SimpleNamespace(enqueue_event=AsyncMock())
    context = SimpleNamespace(task_id="task-1", context_id="context-1")
    executor = MasLabAgentExecutor()

    asyncio.run(executor._emit_stream(context, queue, ["one", "two"]))

    events = [call.args[0] for call in queue.enqueue_event.await_args_list]

    assert len(events) == 5
    assert isinstance(events[0], Task)
    assert events[0].status.state == TaskState.TASK_STATE_SUBMITTED
    artifact_events = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert [event.append for event in artifact_events] == [False, True]
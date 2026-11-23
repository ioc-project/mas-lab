#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A remote task that failed is an error, not reply text; a completed task's reply is its text."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.webserver import UvicornWebServer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"nothing listens on {port}")


@pytest.fixture
def serve() -> Iterator[Callable[[Callable[..., Any]], A2AAgentComm]]:
    handles: list[Any] = []
    comms: list[A2AAgentComm] = []

    def start(handler: Callable[..., Any]) -> A2AAgentComm:
        port = _free_port()
        exposure = A2AExposure(
            endpoint={"protocol": "a2a", "usage": "use-and-deploy", "url": f"http://127.0.0.1:{port}"},
            webserver=UvicornWebServer(),
        )
        handles.append(
            exposure.serve_background({"metadata": {"name": "worker"}, "spec": {"description": "w"}}, handler)
        )
        _wait(port)
        comm = A2AAgentComm(f"http://127.0.0.1:{port}")
        comms.append(comm)
        return comm

    yield start
    for comm in comms:
        comm.stop()
    for handle in handles:
        handle.close()


async def test_a_failed_remote_task_raises_and_shows_no_peer_text(serve) -> None:
    def handler(prompt: str, **_: Any) -> dict[str, str]:
        raise RuntimeError("secret-from-the-peer")

    comm = serve(handler)
    with pytest.raises(AgentCommError) as caught:
        await comm.asend("worker", "hello")
    assert str(caught.value) == "remote A2A task failed"


async def test_a_completed_remote_task_returns_its_artifact_text_not_the_task_dump(serve) -> None:
    comm = serve(lambda prompt, **_: {"artifacts": [{"text": "pong"}], "text": ""})
    reply = await comm.asend("worker", "ping")
    assert reply == "pong"


@pytest.mark.parametrize(
    ("state", "word"),
    [("TASK_STATE_REJECTED", "rejected"), ("TASK_STATE_CANCELED", "canceled"), ("TASK_STATE_FAILED", "failed")],
)
def test_rejected_and_canceled_tasks_raise_with_their_own_word(state: str, word: str) -> None:
    from a2a.types.a2a_pb2 import StreamResponse, Task, TaskState, TaskStatus
    from library_ioa.plugins.a2a.client import raise_if_failed

    task = Task(id="t", status=TaskStatus(state=getattr(TaskState, state)))
    with pytest.raises(AgentCommError) as caught:
        raise_if_failed([StreamResponse(task=task)])
    assert str(caught.value) == f"remote A2A task {word}"
    done = Task(id="t", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    raise_if_failed([StreamResponse(task=done)])


def test_a_streamed_status_update_that_failed_raises() -> None:
    from a2a.types.a2a_pb2 import StreamResponse, TaskState, TaskStatus, TaskStatusUpdateEvent
    from library_ioa.plugins.a2a.client import raise_if_failed

    update = TaskStatusUpdateEvent(task_id="t", status=TaskStatus(state=TaskState.TASK_STATE_FAILED))
    with pytest.raises(AgentCommError) as caught:
        raise_if_failed([StreamResponse(status_update=update)])
    assert str(caught.value) == "remote A2A task failed"
    working = TaskStatusUpdateEvent(task_id="t", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    raise_if_failed([StreamResponse(status_update=working)])


def test_the_reply_of_a_task_with_only_a_status_message_is_its_text() -> None:
    from library_ioa.plugins.a2a.agent_comm import _task_text

    task = {"status": {"message": {"parts": [{"text": "from "}, {"text": "status"}]}}}
    assert _task_text(task) == "from status"
    both = {**task, "artifacts": [{"parts": [{"text": "artifact"}]}]}
    assert _task_text(both) == "artifact"
    assert _task_text({"artifacts": [{"parts": [{"data": {}}]}]}) == ""


def test_a_stream_that_ends_on_a_status_update_replies_with_its_artifact_text() -> None:
    from a2a.types.a2a_pb2 import (
        Artifact,
        Part,
        StreamResponse,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TaskStatusUpdateEvent,
    )
    from library_ioa.plugins.a2a.client import streamed_text

    def artifact(text: str) -> StreamResponse:
        return StreamResponse(artifact_update=TaskArtifactUpdateEvent(artifact=Artifact(parts=[Part(text=text)])))

    done = StreamResponse(
        status_update=TaskStatusUpdateEvent(task_id="t", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    )
    assert streamed_text([artifact("po"), artifact("ng"), done]) == "pong"
    assert streamed_text([done]) == ""


def test_a_stream_ending_on_an_artifact_or_after_a_message_replies_with_text() -> None:
    from a2a.types.a2a_pb2 import (
        Artifact,
        Message,
        Part,
        StreamResponse,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TaskStatusUpdateEvent,
    )
    from library_ioa.plugins.a2a.client import streamed_text

    artifact = StreamResponse(artifact_update=TaskArtifactUpdateEvent(artifact=Artifact(parts=[Part(text="hi")])))
    message = StreamResponse(message=Message(parts=[Part(text="said")]))
    done = StreamResponse(
        status_update=TaskStatusUpdateEvent(task_id="t", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    )
    assert streamed_text([artifact]) == "hi"
    assert streamed_text([message, done]) == "said"


def test_chunks_that_are_not_stream_responses_are_ignored_by_the_failure_check() -> None:
    from library_ioa.plugins.a2a.client import raise_if_failed

    raise_if_failed([{"a": 1}, "text", object()])


def test_closing_text_is_used_unless_the_closing_response_has_text_of_its_own() -> None:
    from a2a.types.a2a_pb2 import (
        Artifact,
        Message,
        Part,
        StreamResponse,
        Task,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
    )
    from library_ioa.plugins.a2a.client import closing_text

    artifact = StreamResponse(artifact_update=TaskArtifactUpdateEvent(artifact=Artifact(parts=[Part(text="hi")])))
    bare = StreamResponse(task=Task(id="t", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)))
    own = StreamResponse(task=Task(id="t", artifacts=[Artifact(parts=[Part(text="own")])]))
    message = StreamResponse(message=Message(parts=[Part(text="said")]))
    assert closing_text([artifact]) == "hi"
    assert closing_text([artifact, bare]) == "hi"
    assert closing_text([artifact, own]) == ""
    assert closing_text([message]) == ""

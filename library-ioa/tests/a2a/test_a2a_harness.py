#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The a2a-harness runner: a remote A2A agent behind the agent runner interface."""

from __future__ import annotations

import socket
import time

import pytest
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.a2a.harness import A2AHarnessRunner
from library_ioa.plugins.webserver import UvicornWebServer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.boundary.runner import RunnerError, RunnerTask


class RecordingComm:
    """A link as a stack would hand it over: records what the runner sends."""

    def __init__(self, reply: str = "ok", error: Exception | None = None) -> None:
        self.sent: list[tuple] = []
        self._reply = reply
        self._error = error

    async def asend(self, target, task, **kwargs) -> str:
        self.sent.append((target, task, kwargs))
        if self._error is not None:
            raise self._error
        return self._reply


def test_the_task_and_its_context_go_through_the_comm_with_target_ids_and_headers() -> None:
    comm = RecordingComm(reply="remote says hi")
    runner = A2AHarnessRunner(comm=comm, target="remote")
    reply = runner.run(
        RunnerTask(
            text="do it",
            session_id="ctx-1",
            task_id="t1",
            headers={"traceparent": "00-aa-bb-01"},
            parent_call_id="call-9",
            upstream_correlation_id=7,
        )
    )
    assert reply.text == "remote says hi"
    assert reply.context_id == "ctx-1"
    ((target, task, kwargs),) = comm.sent
    assert (target, task) == ("remote", "do it")
    assert kwargs["context_id"] == "ctx-1"
    assert kwargs["caller_call_id"] == "call-9"
    assert kwargs["correlation_id"] == 7
    assert kwargs["headers"] == {"traceparent": "00-aa-bb-01"}
    assert dict(kwargs["context"].headers) == {"traceparent": "00-aa-bb-01"}


def test_a_comm_error_is_a_runner_error() -> None:
    runner = A2AHarnessRunner(comm=RecordingComm(error=AgentCommError("peer down")))
    with pytest.raises(RunnerError, match="peer down"):
        runner.run(RunnerTask(text="x"))


def test_a_url_or_a_comm_but_not_both_and_not_neither() -> None:
    with pytest.raises(ValueError):
        A2AHarnessRunner()
    with pytest.raises(ValueError):
        A2AHarnessRunner("http://127.0.0.1:1", comm=RecordingComm())


def test_close_leaves_a_comm_it_was_given_and_is_safe_twice() -> None:
    runner = A2AHarnessRunner(comm=RecordingComm())
    runner.close()
    runner.close()


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("server did not listen")


def test_it_drives_a_real_a2a_server_over_http_and_keeps_the_conversation_id() -> None:
    port = _port()
    seen: list[dict] = []

    def remote(prompt: str, **kwargs):
        seen.append({"prompt": prompt, **kwargs})
        return {"text": f"remote:{prompt}"}

    handle = A2AExposure(
        endpoint={"protocol": "a2a", "usage": "deploy", "url": f"http://127.0.0.1:{port}"},
        webserver=UvicornWebServer(),
    ).serve_background({"metadata": {"name": "remote"}, "spec": {"description": "a harness"}}, remote)
    runner = A2AHarnessRunner(f"http://127.0.0.1:{port}", timeout=10)
    try:
        _wait(port)
        first = runner.run(RunnerTask(text="hello", session_id="conv-1", parent_call_id="p-1"))
        second = runner.run(RunnerTask(text="again", session_id="conv-1"))
    finally:
        runner.close()
        handle.close()
    assert first.text == "remote:hello"
    assert second.text == "remote:again"
    assert [item["session_id"] for item in seen] == ["conv-1", "conv-1"]
    assert seen[0]["parent_call_id"] == "p-1"


def test_a_remote_task_that_failed_is_an_error_not_reply_text() -> None:
    port = _port()

    def refuses(prompt: str, **_):
        raise RuntimeError("no")

    handle = A2AExposure(
        endpoint={"protocol": "a2a", "usage": "deploy", "url": f"http://127.0.0.1:{port}"},
        webserver=UvicornWebServer(),
    ).serve_background({"metadata": {"name": "remote"}, "spec": {"description": "refuses"}}, refuses)
    runner = A2AHarnessRunner(f"http://127.0.0.1:{port}", timeout=10)
    try:
        _wait(port)
        with pytest.raises(RunnerError, match="failed"):
            runner.run(RunnerTask(text="hello", session_id="c"))
    finally:
        runner.close()
        handle.close()

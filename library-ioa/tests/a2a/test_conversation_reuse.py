# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from uuid import uuid4

import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext, bound_conversation


class Client:
    def __init__(self):
        self.calls = []
        self.ids = count()
        self.response = None

    def send_message(self, task, *, context_id, metadata):
        self.calls.append((task, context_id, metadata))
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        return {
            "result": {
                "context_id": context_id or str(uuid4()),
                "id": str(next(self.ids)),
                "parts": [{"text": "answer"}],
            }
        }

    async def asend_message(self, task, **kwargs):
        return self.send_message(task, **kwargs)

    def close(self):
        pass


@pytest.fixture
def comm(monkeypatch):
    client = Client()
    monkeypatch.setattr("library_ioa.plugins.a2a.agent_comm.A2AClient", lambda **_: client)
    link = A2AAgentComm("http://peer", conversation="per_relationship")
    yield link, client
    link.close()


def test_reuse_and_independent_threads(comm):
    link, client = comm
    with bound_conversation("incoming", "caller"):
        link.send("peer", "assignment")
        link.send("peer", "detail")
        link.send("peer", "other thread", context=SendContext(thread="research"))
        link.send("peer", "research detail", context=SendContext(thread="research"))
        link.send("another-peer", "assignment")
    assert client.calls[0][1] == client.calls[2][1] == client.calls[4][1] == ""
    assert client.calls[1][1] and client.calls[3][1]
    assert client.calls[1][1] != client.calls[3][1]
    with bound_conversation("other-incoming", "caller"):
        link.send("peer", "new conversation")
    with bound_conversation("incoming", "another-caller"):
        link.send("peer", "new caller")
    assert client.calls[-1][1] == client.calls[-2][1] == ""


def test_explicit_context_and_no_active_conversation(comm):
    link, client = comm
    link.send("peer", "explicit", context_id="protocol-id")
    assert client.calls[-1][1] == "protocol-id"
    with pytest.raises(AgentCommError, match="active caller"):
        link.send("peer", "automatic")
    with pytest.raises(AgentCommError, match="either"):
        link.send("peer", "conflict", context_id="id", context=SendContext(thread="thread"))


def test_failed_task_retains_context(comm):
    link, client = comm
    with bound_conversation("incoming", "caller"):
        client.response = {"status": "failed", "context_id": "issued", "error": "business error"}
        with pytest.raises(AgentCommError, match="business error"):
            link.send("peer", "assignment")
        client.response = None
        link.send("peer", "follow up")
    assert client.calls[-1][1] == "issued"


@pytest.mark.parametrize(
    "response", [TimeoutError("timeout"), {"result": {}}, {"context_id": "a", "result": {"contextId": "b"}}]
)
def test_ambiguous_initial_response_is_not_retried(comm, response):
    link, client = comm
    with bound_conversation("incoming", "caller"):
        client.response = response
        with pytest.raises(AgentCommError):
            link.send("peer", "possible write")
        client.response = None
        with pytest.raises(AgentCommError, match="unresolved"):
            link.send("peer", "follow up")
    assert len(client.calls) == 1


def test_established_transport_failure_can_be_followed_up(comm):
    link, client = comm
    with bound_conversation("incoming", "caller"):
        link.send("peer", "first")
        client.response = TimeoutError("timeout")
        with pytest.raises(AgentCommError):
            link.send("peer", "second")
        client.response = None
        link.send("peer", "check result")
    assert client.calls[1][1] == client.calls[2][1] != ""


def test_concurrent_initial_calls_share_one_context(comm):
    link, client = comm

    def send(_):
        with bound_conversation("incoming", "caller"):
            return link.send("peer", "request")

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(send, range(8))) == ["answer"] * 8
    assert sum(not context for _, context, _ in client.calls) == 1
    assert len({context for _, context, _ in client.calls if context}) == 1


def test_async_route_and_causal_metadata(comm):
    link, client = comm

    async def send():
        with bound_conversation("incoming", "caller"):
            await link.asend("peer", "first", correlation_id=7, caller_call_id="parent")
            await link.asend("peer", "follow up")

    asyncio.run(send())
    assert client.calls[1][1]
    assert client.calls[0][2] == {"mas.correlation_id": "7", "mas.caller_call_id": "parent"}


@pytest.mark.asyncio
@pytest.mark.parametrize("synchronous_first", [False, True])
async def test_cancelled_queued_write_never_dispatches(comm, synchronous_first):
    from threading import Event, Lock

    link, client = comm
    entered, queued = asyncio.Event(), asyncio.Event()
    proceed = Event()
    loop = asyncio.get_running_loop()
    original = client.send_message

    def first_send(task, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert proceed.wait(5)
        return original(task, **kwargs)

    async def first_asend(task, **kwargs):
        entered.set()
        await asyncio.to_thread(proceed.wait, 5)
        return original(task, **kwargs)

    class ObservedLock:
        def __init__(self):
            self.inner = Lock()

        def acquire(self, blocking=True):
            result = self.inner.acquire(blocking=blocking)
            if not result:
                queued.set()
            return result

        def release(self):
            self.inner.release()

        def __enter__(self):
            self.acquire()

        def __exit__(self, *args):
            self.release()

    client.send_message = first_send
    client.asend_message = first_asend
    with bound_conversation("incoming", "caller"):
        relation = link._conversations.relationship("peer")
        relation.lock = ObservedLock()
        first = asyncio.create_task(
            asyncio.to_thread(link.send, "peer", "first") if synchronous_first else link.asend("peer", "first")
        )
        await entered.wait()
        second = asyncio.create_task(link.asend("peer", "cancelled write"))
        await queued.wait()
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        proceed.set()
        await first
        assert not relation.unresolved
        client.send_message = original
        await link.asend("peer", "follow up")
    assert [call[0] for call in client.calls] == ["first", "follow up"]
    assert client.calls[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("established", [False, True])
async def test_cancellation_after_dispatch_preserves_ambiguity(comm, established):
    link, client = comm
    entered = asyncio.Event()

    async def interrupted(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with bound_conversation("incoming", "caller"):
        if established:
            link.send("peer", "establish")
        original = client.asend_message
        client.asend_message = interrupted
        pending = asyncio.create_task(link.asend("peer", "possible write"))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        client.asend_message = original
        if established:
            await link.asend("peer", "inspect outcome")
            assert client.calls[-1][1]
        else:
            with pytest.raises(AgentCommError, match="unresolved"):
                await link.asend("peer", "do not replay")
            assert not client.calls


@pytest.mark.asyncio
async def test_sdk_async_send_uses_native_coroutine(monkeypatch):
    from unittest.mock import AsyncMock

    from library_ioa.plugins.a2a.client import A2AClient

    client = A2AClient(url="http://unused")
    native = AsyncMock(return_value={"result": "answer"})
    monkeypatch.setattr(client, "_sdk_send_message", native)
    assert await client.asend_message("hello", context_id="context", metadata={"parent": "call"}) == {
        "result": "answer"
    }
    native.assert_awaited_once_with("hello", context_id="context", metadata={"parent": "call"}, stream=False)
    client.close()

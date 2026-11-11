# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Shutdown closes admission before cancelling native SDK operations."""

from __future__ import annotations

import asyncio
from threading import Event, Lock
from unittest.mock import AsyncMock

import httpx
import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.a2a.client import A2AClient
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, bound_conversation


@pytest.mark.asyncio
async def test_closed_client_rejects_all_entrypoints_without_transport(monkeypatch):
    client = A2AClient(url="http://unused")
    send, card = AsyncMock(), AsyncMock()
    monkeypatch.setattr(client, "_sdk_send_message", send)
    monkeypatch.setattr(client, "_sdk_get_agent_card", card)
    client.close()
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.send_message("write")
    with pytest.raises(RuntimeError, match="closed"):
        client.get_agent_card()
    with pytest.raises(RuntimeError, match="closed"):
        await client.asend_message("write")
    await client.aclose()
    send.assert_not_called()
    card.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("synchronous_first,synchronous_write", [(False, False), (True, False), (False, True)])
async def test_close_prevents_queued_write_and_relationship_recreation(
    synchronous_first, synchronous_write, monkeypatch
):
    link = A2AAgentComm("http://unused", conversation="per_relationship")
    entered, queued = asyncio.Event(), asyncio.Event()
    proceed = Event()
    calls = []
    loop = asyncio.get_running_loop()

    async def transport(text, **kwargs):
        calls.append(text)
        loop.call_soon_threadsafe(entered.set)
        await asyncio.to_thread(proceed.wait, 5)
        return {"result": {"context_id": "issued", "parts": [{"text": "answer"}]}}

    class ObservedLock:
        def __init__(self):
            self.inner = Lock()

        def acquire(self, blocking=True):
            if self.inner.locked():
                loop.call_soon_threadsafe(queued.set)
            return self.inner.acquire(blocking=blocking)

        def release(self):
            self.inner.release()

        def __enter__(self):
            self.acquire()

        def __exit__(self, *args):
            self.release()

    monkeypatch.setattr(link._client, "_sdk_send_message", transport)
    try:
        with bound_conversation("incoming", "caller"):
            relation = link._conversations.relationship("peer")
            relation.lock = ObservedLock()
            first = asyncio.create_task(
                asyncio.to_thread(link.send, "peer", "first") if synchronous_first else link.asend("peer", "first")
            )
            await asyncio.wait_for(entered.wait(), 5)
            write = asyncio.create_task(
                asyncio.to_thread(link.send, "peer", "queued write")
                if synchronous_write
                else link.asend("peer", "queued write")
            )
            await asyncio.wait_for(queued.wait(), 5)
            link.close()  # Must not join a worker waiting on this event loop.
            proceed.set()
            results = await asyncio.wait_for(asyncio.gather(first, write, return_exceptions=True), 5)
            assert all(isinstance(result, (AgentCommError, asyncio.CancelledError)) for result in results)
            await link.aclose()
            assert not link._conversations._relationships
            with pytest.raises(AgentCommError, match="closed"):
                await link.asend("peer", "write after close")
            with pytest.raises(AgentCommError, match="closed"):
                link.send("peer", "write after close")
            assert not link._conversations._relationships
            assert calls == ["first"]
    finally:
        proceed.set()
        await link.aclose()


@pytest.mark.asyncio
async def test_close_between_async_admission_and_dispatch(monkeypatch):
    client = A2AClient(url="http://unused")
    transport = AsyncMock()
    monkeypatch.setattr(client, "_sdk_send_message", transport)
    send = asyncio.create_task(client.asend_message("must not dispatch"))
    asyncio.get_running_loop().call_soon(client.close)
    with pytest.raises((asyncio.CancelledError, RuntimeError)):
        await send
    await client.aclose()
    transport.assert_not_called()


@pytest.mark.asyncio
async def test_aclose_awaits_sdk_and_http_cleanup_from_another_thread(monkeypatch):
    from library_ioa.plugins.a2a import client as module

    entered, sdk_closed, http_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SDK:
        async def send_message(self, request):
            entered.set()
            await asyncio.Event().wait()
            yield None

        async def close(self):
            sdk_closed.set()

    class HTTP(httpx.AsyncClient):
        async def aclose(self):
            await super().aclose()
            http_closed.set()

        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            http_closed.set()

    monkeypatch.setattr(module, "create_client", AsyncMock(return_value=SDK()))
    monkeypatch.setattr(module.A2ACardResolver, "get_agent_card", AsyncMock(return_value=object()))
    monkeypatch.setattr(module.httpx, "AsyncClient", HTTP)
    client = A2AClient(url="http://unused")
    send = asyncio.create_task(client.asend_message("first"))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.to_thread(client.close)
    await client.aclose()
    with pytest.raises(asyncio.CancelledError):
        await send
    assert sdk_closed.is_set() and http_closed.is_set()
    assert not client._async_calls


@pytest.mark.asyncio
async def test_cleanup_timeout_and_cancelled_wait_do_not_reopen_or_lose_work(monkeypatch):
    client = A2AClient(url="http://unused", timeout=0.01)
    entered, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def transport(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish.wait()

    monkeypatch.setattr(client, "_sdk_send_message", transport)
    send = asyncio.create_task(client.asend_message("already dispatched"))
    await entered.wait()
    closing = asyncio.create_task(client.aclose())
    await cleaning.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with pytest.raises(TimeoutError, match="cleanup"):
        await client.aclose()
    assert not send.done() and client._async_calls
    with pytest.raises(RuntimeError, match="closed"):
        await client.asend_message("do not replay")
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await send
    await client.aclose()
    assert not client._async_calls


def test_close_outside_a_loop_drains_existing_sync_dispatch(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    client = A2AClient(url="http://unused")
    entered, finish = Event(), Event()
    closed, shutdown_started = Event(), Event()
    original_shutdown = client._executor.shutdown

    def shutdown(**kwargs):
        shutdown_started.set()
        return original_shutdown(**kwargs)

    monkeypatch.setattr(client._executor, "shutdown", shutdown)

    async def transport(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(finish.wait, 5)
        return {"result": "completed before shutdown returns"}

    monkeypatch.setattr(client, "_sdk_send_message", transport)
    with ThreadPoolExecutor(max_workers=2) as pool:
        send = pool.submit(client.send_message, "first")
        assert entered.wait(5)

        def close():
            client.close()
            closed.set()

        closing = pool.submit(close)
        try:
            assert shutdown_started.wait(5)
            assert not closed.is_set()
        finally:
            finish.set()
        assert send.result(timeout=5)["result"] == "completed before shutdown returns"
        closing.result(timeout=5)
        assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), -1])
async def test_cleanup_rejects_an_unbounded_timeout_without_reopening(timeout, monkeypatch):
    client = A2AClient(url="http://unused", timeout=timeout)
    entered = asyncio.Event()

    async def transport(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_sdk_send_message", transport)
    send = asyncio.create_task(client.asend_message("first"))
    await entered.wait()
    with pytest.raises(ValueError, match="finite and positive"):
        await client.aclose()
    with pytest.raises(asyncio.CancelledError):
        await send
    with pytest.raises(RuntimeError, match="closed"):
        await client.asend_message("write after closure")
    await client.aclose()

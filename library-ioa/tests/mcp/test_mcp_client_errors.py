#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A refused connection reaches the caller as the connection error, not as a task-group wrapper."""

from __future__ import annotations

import asyncio
import socket

import pytest
from library_ioa.plugins.mcp.client import MCPClient


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_first_error_unwraps_nested_groups_to_the_first_real_error() -> None:
    from library_ioa.plugins.mcp.client import first_error

    inner = BaseExceptionGroup("inner", [asyncio.CancelledError(), ConnectionRefusedError("refused")])
    outer = BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert isinstance(first_error(outer), ConnectionRefusedError)
    plain = ValueError("x")
    assert first_error(plain) is plain


def test_a_refused_connection_is_not_a_task_group_error() -> None:
    client = MCPClient(url=f"http://127.0.0.1:{_closed_port()}/mcp")
    with pytest.raises(Exception) as caught:
        asyncio.run(client.list_tools())
    assert not isinstance(caught.value, BaseExceptionGroup)
    assert "TaskGroup" not in str(caught.value)


def test_a_refused_connection_on_a_held_session_is_not_a_task_group_error() -> None:
    client = MCPClient(url=f"http://127.0.0.1:{_closed_port()}/mcp")

    async def run() -> None:
        await client.connect()
        try:
            await client.list_tools()
        finally:
            await client.disconnect()

    with pytest.raises(Exception) as caught:
        asyncio.run(run())
    assert not isinstance(caught.value, BaseExceptionGroup)
    assert "TaskGroup" not in str(caught.value)


def test_a_group_the_caller_raises_inside_a_session_is_not_rewritten() -> None:
    client = MCPClient(url="http://127.0.0.1:1/mcp")

    async def fake_enter(stack):
        return object()

    client._enter_session = fake_enter  # type: ignore[method-assign]
    mine = ExceptionGroup("mine", [ValueError("a"), KeyError("b")])

    async def run() -> None:
        async with client._ephemeral_session():
            raise mine

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(run())
    assert caught.value is mine


def test_the_transport_group_keeps_its_cause() -> None:
    client = MCPClient(url="http://127.0.0.1:1/mcp")
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError("refused")])

    async def refuse(stack):
        raise group

    client._enter_session = refuse  # type: ignore[method-assign]

    async def run() -> None:
        async with client._ephemeral_session():
            pass

    with pytest.raises(ConnectionRefusedError) as caught:
        asyncio.run(run())
    assert caught.value.__cause__ is group


def test_a_plain_error_in_the_body_with_a_transport_group_at_teardown_is_unwrapped() -> None:
    """The one-shot path when a transport dies mid-call: the body saw an ordinary error, teardown the group."""
    client = MCPClient(url="http://127.0.0.1:1/mcp")
    real = ConnectionError("Connection closed")

    class Transport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            raise BaseExceptionGroup("unhandled errors in a TaskGroup", [real])

    async def enter(stack):
        await stack.enter_async_context(Transport())
        return object()

    client._enter_session = enter  # type: ignore[method-assign]

    async def run() -> None:
        async with client._ephemeral_session():
            raise ValueError("body")

    with pytest.raises(ConnectionError) as caught:
        asyncio.run(run())
    assert caught.value is real


def test_first_error_never_hides_an_interrupt_behind_an_ordinary_error() -> None:
    from library_ioa.plugins.mcp.client import first_error

    interrupt = KeyboardInterrupt()
    group = BaseExceptionGroup("g", [ValueError("v"), interrupt])
    assert first_error(group) is interrupt


def test_the_unwrapped_error_keeps_its_own_cause() -> None:
    client = MCPClient(url="http://127.0.0.1:1/mcp")
    root = KeyError("root")
    leaf = ConnectionRefusedError("refused")
    leaf.__cause__ = root

    async def refuse(stack):
        raise BaseExceptionGroup("unhandled errors in a TaskGroup", [leaf])

    client._enter_session = refuse  # type: ignore[method-assign]

    async def run() -> None:
        async with client._ephemeral_session():
            pass

    with pytest.raises(ConnectionRefusedError) as caught:
        asyncio.run(run())
    assert caught.value.__cause__ is root


@pytest.mark.parametrize("body", [KeyboardInterrupt(), asyncio.CancelledError()])
def test_an_interrupt_in_the_body_is_not_turned_into_an_ordinary_error(body) -> None:
    client = MCPClient(url="http://127.0.0.1:1/mcp")

    class Transport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            raise ExceptionGroup("teardown", [OSError("td")])

    async def enter(stack):
        await stack.enter_async_context(Transport())
        return object()

    client._enter_session = enter  # type: ignore[method-assign]

    async def run() -> None:
        async with client._ephemeral_session():
            raise body

    with pytest.raises(type(body)) as caught:
        asyncio.run(run())
    assert caught.value is body


def test_a_callers_group_wins_over_a_teardown_group() -> None:
    client = MCPClient(url="http://127.0.0.1:1/mcp")
    mine = ExceptionGroup("mine", [ValueError("a")])

    class Transport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            raise ExceptionGroup("teardown", [OSError("td")])

    async def enter(stack):
        await stack.enter_async_context(Transport())
        return object()

    client._enter_session = enter  # type: ignore[method-assign]

    async def run() -> None:
        async with client._ephemeral_session():
            raise mine

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(run())
    assert caught.value is mine

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One real stdio server process is reused across calls, threads and a close."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest
from library_ioa.plugins.mcp.client import MCPClient
from library_ioa.plugins.mcp.client.wrapper import MCPClientWrapper

_SERVER = Path(__file__).with_name("_stdio_server.py")


def _pid(payload: dict) -> int:
    return int(payload["content"][0]["text"])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.fixture
def wrapper():
    inner = MCPClient(command=sys.executable, args=[str(_SERVER)], transport="stdio")
    w = MCPClientWrapper(inner)
    yield inner, w
    w.close()


def test_calls_share_one_server_and_close_ends_it(wrapper) -> None:
    inner, w = wrapper
    first = _pid(w.run(inner.call_tool("pid", {})))
    second = _pid(w.run(inner.call_tool("pid", {})))
    assert first == second
    w.close()  # must not raise a cancel-scope error
    deadline = time.monotonic() + 5
    while _alive(first) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(first)
    w.close()  # idempotent


def test_concurrent_first_calls_start_one_server(wrapper) -> None:
    inner, w = wrapper
    pids: list[int] = []
    errors: list[BaseException] = []

    def _go() -> None:
        try:
            pids.append(_pid(w.run(inner.call_tool("pid", {}))))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(pids)) == 1


def test_a_dead_server_is_replaced_on_the_next_call(wrapper) -> None:
    inner, w = wrapper
    first = _pid(w.run(inner.call_tool("pid", {})))
    os.kill(first, signal.SIGKILL)
    time.sleep(0.3)
    last: BaseException | None = None
    pid = None
    for _ in range(3):  # the call that notices the death may fail once
        try:
            pid = _pid(w.run(inner.call_tool("pid", {})))
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert pid is not None, last
    assert pid != first


def test_connection_closed_reads_a_nested_error_code() -> None:
    from library_ioa.plugins.mcp.client import _connection_closed

    class Nested:
        code = -32000

    class Wrapped(Exception):
        def __init__(self) -> None:
            super().__init__("closed")
            self.error = Nested()

    assert _connection_closed(Wrapped())
    assert not _connection_closed(RuntimeError("no"))


def test_dedicated_loop_stop_is_a_noop_until_started_then_cancels_work() -> None:
    from library_ioa.plugins.mcp.client.wrapper import _DedicatedLoop

    loop = _DedicatedLoop()
    loop.stop()

    async def hang() -> None:
        await asyncio.Event().wait()

    asyncio.run_coroutine_threadsafe(hang(), loop.ensure())
    loop.stop()
    assert not loop.started


def test_close_weak_ignores_a_dead_ref_and_a_close_error() -> None:
    import weakref

    from library_ioa.plugins.mcp.client.wrapper import _close_weak

    class Boom:
        def close(self) -> None:
            raise RuntimeError("already gone")

    obj = Boom()
    live = weakref.ref(obj)
    _close_weak(live)
    del obj
    _close_weak(live)


def test_provider_close_and_run_delegate_to_the_client() -> None:
    from library_ioa.plugins.mcp.provider import MCPToolProvider

    class Wrap:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def run(self, coro):
            return ("ran", coro)

    wrap = Wrap()
    provider = MCPToolProvider(wrap)
    provider.close()
    assert wrap.closed
    assert provider._run("coro") == ("ran", "coro")

    class NoRun:
        pass

    async def one() -> int:
        return 7

    assert MCPToolProvider(NoRun())._run(one()) == 7

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Sync plugins stay supported: they run on the worker threads and the runtime warns once."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time

import pytest
from mas.library.standard.lib.comm.send_async import asend_via
from mas.library.standard.lib.stack import Envelope, Layer
from mas.runtime.aio import acall, configure_sync_threads, run_sync, sync_threads


class _SyncLink:
    def send(self, target: str, task: str, **_: object) -> str:
        return f"{threading.current_thread().name}:{target}:{task}"


async def test_a_link_that_only_has_send_is_awaited_on_a_worker_thread_and_warned_once(caplog) -> None:
    link = _SyncLink()
    with caplog.at_level(logging.WARNING):
        first = await asend_via(link, "b", "x")
        await asend_via(link, "b", "y")
    assert first.startswith("mas-sync-plugin") and first.endswith(":b:x")
    assert len([r for r in caplog.records if "_SyncLink" in r.getMessage()]) == 1


class _Bottom(Layer):
    needs_lower = False

    def send(self, envelope: Envelope) -> str:
        return f"bottom:{envelope.payload}"


class _SyncTop(Layer):
    def send(self, envelope: Envelope) -> str:
        return f"top>{self._require().send(envelope)}"  # a sync layer reaches the one below with send


async def test_a_sync_layer_over_a_sync_layer_runs_without_deadlock_on_one_thread() -> None:
    configure_sync_threads(1)
    bottom, top = _Bottom(), _SyncTop()
    top.bind(bottom)
    assert await top.asend(Envelope(payload="p")) == "top>bottom:p"


class _AsyncMiddle(Layer):
    async def asend(self, envelope: Envelope) -> str:
        return f"middle>{await self._require().asend(envelope)}"


class _BottomUsingRunSync(Layer):
    needs_lower = False

    async def asend(self, envelope: Envelope) -> str:
        # a transport that runs a blocking call on the shared sync workers
        return await run_sync(lambda: f"bottom:{envelope.payload}")


async def test_a_sync_layer_over_an_async_layer_over_a_run_sync_bottom_does_not_deadlock_on_one_thread() -> None:
    configure_sync_threads(1)

    class _SyncOverAsync(Layer):
        def send(self, envelope: Envelope) -> str:
            # a sync layer hands the call back to the loop for the async layer below (the inherited send)
            return f"top>{super().send(envelope)}"

    bottom, middle, top = _BottomUsingRunSync(), _AsyncMiddle(), _SyncOverAsync()
    middle.bind(bottom)
    top.bind(middle)
    reply = await asyncio.wait_for(top.asend(Envelope(payload="p")), timeout=10)
    assert reply == "top>middle>bottom:p"


async def test_the_context_of_a_sync_layer_reaches_the_layer_below_through_the_loop() -> None:
    configure_sync_threads(1)
    var: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="nobody")

    class _Reads(Layer):
        needs_lower = False

        async def asend(self, envelope: Envelope) -> str:
            return var.get()

    class _Top(Layer):
        def send(self, envelope: Envelope) -> str:
            return super().send(envelope)

    bottom, top = _Reads(), _Top()
    top.bind(bottom)
    var.set("alice")
    assert await asyncio.wait_for(top.asend(Envelope(payload="p")), timeout=10) == "alice"


async def test_the_worker_pool_size_is_the_engine_setting() -> None:
    configure_sync_threads(2)
    assert sync_threads() == 2
    running: list[int] = []
    peak = [0]
    lock = threading.Lock()

    def work() -> None:
        with lock:
            running.append(1)
            peak[0] = max(peak[0], len(running))
        time.sleep(0.1)
        with lock:
            running.pop()

    import asyncio

    await asyncio.gather(*(run_sync(work) for _ in range(4)))
    assert peak[0] == 2
    configure_sync_threads(1)
    with pytest.raises(ValueError):
        configure_sync_threads(0)


async def test_acall_awaits_async_functions_and_runs_plain_ones_off_the_loop() -> None:
    async def a() -> str:
        return "async"

    def s() -> str:
        return threading.current_thread().name

    assert await acall(a) == "async"
    assert (await acall(s)).startswith("mas-sync-plugin")


async def test_a_sync_delegation_and_a_sync_engine_tool_run_on_worker_threads(caplog) -> None:
    from mas.runtime.engine.tool_dispatch import aexecute_engine_tool

    seen: list[str] = []

    class SyncDelegation:
        def is_delegate_tool(self, name: str) -> bool:
            return name.startswith("delegate_to_")

        def call_delegate_tool(self, name, arguments, *, correlation_id=0, caller_call_id=""):
            seen.append(threading.current_thread().name)
            return f"delegated:{name}:{arguments['task']}"

    class SyncEngineTool:
        def claims(self, name: str, *, ctx=None) -> bool:
            return name == "make"

        def call(self, name, arguments, **_):
            seen.append(threading.current_thread().name)
            return f"made:{arguments['x']}"

    with caplog.at_level(logging.WARNING):
        delegated = await aexecute_engine_tool("delegate_to_b", delegation=SyncDelegation(), arguments={"task": "t"})
        made = await aexecute_engine_tool("make", engine_contracts=[SyncEngineTool()], arguments={"x": 1})
    assert delegated == "delegated:delegate_to_b:t" and made == "made:1"
    assert all(name.startswith("mas-sync-plugin") for name in seen) and len(seen) == 2
    assert {r.getMessage().split()[1] for r in caplog.records if "worker thread" in r.getMessage()} == {
        "SyncDelegation",
        "SyncEngineTool",
    }


async def test_an_engine_with_only_invoke_runs_on_a_worker_thread() -> None:
    from mas.runtime.engine.ainvoke import ainvoke_call
    from mas.runtime.schema.egress import InvokeEngineIo
    from mas.runtime.schema.ingress import EngineIoReturn

    class SyncEngine:
        def invoke(self, io):
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text=threading.current_thread().name,
            )

    out = await ainvoke_call(SyncEngine(), InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert out.text.startswith("mas-sync-plugin")


class _SyncTool:
    """A tool owner written sync: ``on_execute_tool`` may answer, decline, or leave the name to ``execute``."""

    def __init__(self, *, answer=None, listed=False, legacy=None, declines=False) -> None:
        self._answer, self._listed, self._legacy, self._declines = answer, listed, legacy, declines

    def on_execute_tool(self, tool_name, arguments, **_):
        if self._declines:
            raise NotImplementedError
        return self._answer

    def on_collect_tools(self, **_):
        return [{"name": "t"}] if self._listed else []

    def execute(self, **arguments):
        if self._legacy == "raise":
            raise NotImplementedError
        return self._legacy


def _run_owner(owner) -> object:
    from mas.library.standard.plugins.tools.local import LocalToolProvider

    return LocalToolProvider._run_owner(None, owner, "t", {"a": 1})


def test_a_sync_tool_owner_answer_is_returned_as_is() -> None:
    assert _run_owner(_SyncTool(answer="done")) == "done"


def test_a_sync_tool_that_lists_the_name_and_answers_nothing_returns_an_empty_result() -> None:
    assert _run_owner(_SyncTool(listed=True, legacy="ignored")) == ""
    assert _run_owner(_SyncTool(declines=True, listed=True)) == ""


def test_a_sync_tool_not_listed_falls_back_to_its_legacy_execute() -> None:
    assert _run_owner(_SyncTool(legacy="old")) == "old"
    assert _run_owner(_SyncTool(legacy=None)) == ""
    assert _run_owner(_SyncTool(legacy="raise")) == ""

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Where a plugin tool runs, and what it sees. The two are independent.

What a tool sees
    A plugin tool gets the same :class:`ToolCallContext` wherever it runs:
    inline, in a thread, later in another process. It is a frozen copy of the
    few values a tool may read (session, agent, user, turn, the spec). A tool
    cannot reach or change shared run state, because it is never handed any,
    so running it elsewhere cannot change its result.

    A tool that must read or change session state (the skill tools) declares
    ``kernel_state = True``. It gets the live ctx and always runs inline in
    the kernel: it is a kernel-side tool, not a plugin tool, in every
    deployment.

Where it runs
    A deployment choice, set in the RuntimeEngine infra manifest
    (``spec.engine.tool_worker`` and ``spec.engine.tool_concurrency``), never
    on a tool. ``thread`` (default) lets blocking tools overlap; ``inline``
    runs them one after another on the event loop. Both give the same results
    in the same order. ``tool_concurrency`` bounds how many run at once;
    ``None`` bounds it only by the size of the batch.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import copy
import inspect
import logging
import threading
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

logger = logging.getLogger(__name__)

_FLAGGED_SYNC: set[type] = set()

WORKER_INLINE = "inline"
WORKER_THREAD = "thread"
WORKERS = (WORKER_INLINE, WORKER_THREAD)


@dataclass(frozen=True)
class ToolCallContext:
    """What a plugin tool may read. Frozen: assignment raises."""

    session_id: str = ""
    user: str = ""
    agent_id: str = ""
    turn_index: int = 0
    spec: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass
class ToolPlacement:
    """Deployment setting: where sync plugin tools run and how many at once."""

    worker: str = WORKER_THREAD
    concurrency: int | None = None
    _slots: dict[int, asyncio.Semaphore] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.worker not in WORKERS:
            raise ValueError(f"unknown tool_worker {self.worker!r}; use one of {list(WORKERS)}")
        if self.concurrency is not None and self.concurrency < 1:
            raise ValueError("tool_concurrency must be >= 1 or null")

    def slots(self) -> asyncio.Semaphore | None:
        if self.concurrency is None:
            return None
        loop_id = id(asyncio.get_running_loop())
        sem = self._slots.get(loop_id)
        if sem is None:
            sem = self._slots[loop_id] = asyncio.Semaphore(self.concurrency)
        return sem


_placement: ContextVar[ToolPlacement | None] = ContextVar("mas_tool_placement", default=None)
_DEFAULT_PLACEMENT = ToolPlacement()


def current_placement() -> ToolPlacement:
    return _placement.get() or _DEFAULT_PLACEMENT


def bind_placement(placement: ToolPlacement | None) -> contextvars.Token:
    return _placement.set(placement or ToolPlacement())


def unbind_placement(token: contextvars.Token) -> None:
    _placement.reset(token)


def seal_ctx(ctx: Any) -> ToolCallContext:
    """Copy the readable values of *ctx* into a frozen context. Idempotent."""
    if isinstance(ctx, ToolCallContext):
        return ctx
    if ctx is None:
        return ToolCallContext()
    from mas.runtime.engine.tools import current_spec_for_advertise

    spec = copy.deepcopy(current_spec_for_advertise(ctx) or {})
    return ToolCallContext(
        session_id=str(getattr(ctx, "session_id", "") or ""),
        user=str(getattr(ctx, "user", "") or ""),
        agent_id=str(getattr(ctx, "agent_id", "") or ""),
        turn_index=int(getattr(ctx, "turn_index", 0) or 0),
        spec=MappingProxyType(spec),
    )


def ctx_for_tool(owner: Any, ctx: Any) -> Any:
    """The ctx a tool gets: live for a ``kernel_state`` tool, sealed otherwise."""
    return ctx if getattr(owner, "kernel_state", False) else seal_ctx(ctx)


def provider_ctx(provider: Any, ctx: Any) -> Any:
    """The ctx to hand a provider. Kernel-side providers apply the policy per tool themselves."""
    if getattr(provider, "seals_own_ctx", False):
        return ctx
    return ctx_for_tool(provider, ctx)


def _settle(future: asyncio.Future, result: Any, error: BaseException | None) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


async def _in_new_thread(fn: Callable[[], Any]) -> Any:
    """Run *fn* in its own thread: no shared pool, so no hidden worker cap."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    snapshot = contextvars.copy_context()

    def _run() -> None:
        try:
            result = snapshot.run(fn)
        except BaseException as exc:  # noqa: BLE001 - handed to the awaiting task
            loop.call_soon_threadsafe(_settle, future, None, exc)
        else:
            loop.call_soon_threadsafe(_settle, future, result, None)

    threading.Thread(target=_run, name="mas-tool", daemon=True).start()
    return await future


def _flag_sync_tool(owner: Any, placement: ToolPlacement) -> None:
    """Say once per tool class that it is synchronous, and what that costs.

    A sync tool is the only kind that needs a placement. Under ``inline`` it
    blocks the event loop for its whole call, so that is a warning. Under
    ``thread`` it is only noted. Kernel-state tools are exempt: they are meant
    to run inline.
    """
    cls = type(owner)
    if cls in _FLAGGED_SYNC or getattr(owner, "kernel_state", False):
        return
    _FLAGGED_SYNC.add(cls)
    if placement.worker == WORKER_INLINE:
        logger.warning(
            "sync tool %s runs inline and blocks the event loop for each call; "
            "write async def on_execute_tool (or execute), or set engine.tool_worker: thread",
            cls.__name__,
        )
    else:
        logger.info(
            "sync tool %s runs in a worker thread; write async def on_execute_tool "
            "(or execute) to run it on the event loop",
            cls.__name__,
        )


_HELPER_LOCK = threading.Lock()
_HELPER_LOOP: asyncio.AbstractEventLoop | None = None


def _helper_loop() -> asyncio.AbstractEventLoop:
    """The one loop that finishes async tools for the sync path, started on first use.

    One loop for the life of the process, so a tool that keeps a loop-bound
    object (an async HTTP client, a lock) between calls sees the same loop
    every time. The async path (``acall_tool``) never uses it.
    """
    global _HELPER_LOOP
    with _HELPER_LOCK:
        if _HELPER_LOOP is None or _HELPER_LOOP.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="mas-sync-tool-loop", daemon=True).start()
            _HELPER_LOOP = loop
        return _HELPER_LOOP


def run_awaitable(awaitable: Any) -> Any:
    """Finish an awaitable from the sync tool path (``call_tool``).

    That path has no loop. The awaitable runs on one shared helper loop (see
    ``_helper_loop``) in the caller's context, and the caller waits for it.
    The async path (``acall_tool``) awaits on the loop that is already
    running. Calling ``call_tool`` from that loop is the wrong path: use
    ``acall_tool``.
    """

    async def _finish() -> Any:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise RuntimeError("call_tool cannot finish an async tool while an event loop is running; use acall_tool")
    loop = _helper_loop()
    done: concurrent.futures.Future[Any] = concurrent.futures.Future()
    context = contextvars.copy_context()

    def _start() -> None:
        task = loop.create_task(_finish(), context=context)

        def _relay(finished: asyncio.Task[Any]) -> None:
            if finished.cancelled():
                done.cancel()
            elif (error := finished.exception()) is not None:
                done.set_exception(error)
            else:
                done.set_result(finished.result())

        task.add_done_callback(_relay)

    loop.call_soon_threadsafe(_start)
    return done.result()


async def arun_sync_tool(call: Callable[..., Any], *args: Any, owner: Any, ctx: Any, **kwargs: Any) -> Any:
    """Run a sync tool call where the deployment says. The tool sees the same ctx either way."""
    tool_ctx = ctx_for_tool(owner, ctx)
    placement = current_placement()
    _flag_sync_tool(owner, placement)
    if getattr(owner, "kernel_state", False) or placement.worker == WORKER_INLINE:
        return call(*args, ctx=tool_ctx, **kwargs)
    slots = placement.slots()
    if slots is None:
        return await _in_new_thread(lambda: call(*args, ctx=tool_ctx, **kwargs))
    async with slots:
        return await _in_new_thread(lambda: call(*args, ctx=tool_ctx, **kwargs))

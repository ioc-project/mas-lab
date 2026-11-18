#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Small async helpers shared by the runtime and its plugins.

The runtime is async. A plugin may still be written sync: the runtime then runs its method on a small pool of
worker threads (``engine.sync_threads`` in the RuntimeEngine infra manifest, one by default) and warns once that
the async form is preferred. Sync plugins stay supported; async is what new plugins should use.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SYNC_THREADS = 1
_LOCK = threading.Lock()
_SIZE = DEFAULT_SYNC_THREADS
_POOL: ThreadPoolExecutor | None = None
_WARNED: set[tuple[type, str]] = set()


def new_thread(
    target: Callable[..., Any],
    *,
    args: tuple[Any, ...] = (),
    kwargs: Mapping[str, Any] | None = None,
    name: str | None = None,
    daemon: bool | None = None,
) -> threading.Thread:
    """An unstarted thread that runs *target* in an empty context.

    Python 3.14's free-threaded build starts a thread with a copy of the context variables of the thread
    that created it; every other build starts it empty. The runtime keeps per-call state (call frames, the
    event stream, the current transport) in context variables, so a thread it starts from inside a call
    must not see that call's. Start threads with this instead of ``threading.Thread``, so they behave the
    same on every build and no setting is needed. A thread that does need the caller's context takes it
    explicitly (``contextvars.copy_context().run``).
    """
    context = contextvars.Context()

    call_kwargs = dict(kwargs or {})

    def run() -> None:
        context.run(lambda: target(*args, **call_kwargs))

    return threading.Thread(target=run, name=name, daemon=daemon)


def configure_sync_threads(count: int) -> None:
    """Set how many worker threads sync plugins share in this process (at least one)."""
    global _SIZE, _POOL
    if count < 1:
        raise ValueError("sync_threads must be >= 1")
    with _LOCK:
        if count == _SIZE and _POOL is not None:
            return
        old, _POOL, _SIZE = _POOL, None, int(count)
    if old is not None:
        old.shutdown(wait=False)


def sync_threads() -> int:
    return _SIZE


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=_SIZE, thread_name_prefix="mas-sync-plugin")
        return _POOL


_HOME_LOOP: contextvars.ContextVar[asyncio.AbstractEventLoop | None] = contextvars.ContextVar(
    "mas_home_loop", default=None
)
_ON_WORKER: contextvars.ContextVar[bool] = contextvars.ContextVar("mas_on_sync_worker", default=False)


async def run_sync(fn, /, *args, **kwargs):
    """Run a blocking plugin method on the shared worker threads, in the caller's context.

    A sync plugin that waits on another sync plugin (a layer over a layer) would hold the only worker while
    the one below needs it. A call made from inside a worker therefore runs on a thread of its own.
    """
    context = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    nested = _ON_WORKER.get()

    def call():
        token = _ON_WORKER.set(True)
        home = _HOME_LOOP.set(loop)
        try:
            return fn(*args, **kwargs)
        finally:
            _HOME_LOOP.reset(home)
            _ON_WORKER.reset(token)

    return await loop.run_in_executor(None if nested else _pool(), lambda: context.run(call))


def await_on_loop(awaitable: Any) -> Any:
    """Wait, from a sync plugin method the runtime called on a worker thread, for async work on the loop.

    A sync layer calls ``self._require().send(...)`` to reach the layer below; that layer is async, so the call
    is handed back to the event loop and the worker thread waits for the answer.
    """
    loop = _HOME_LOOP.get()
    if loop is None:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise RuntimeError("a sync plugin can wait for async work only from a method the runtime called")

    async def _run() -> Any:
        return await awaitable

    return asyncio.run_coroutine_threadsafe(_run(), loop).result()


class LoopCache:
    """One async resource per event loop, kept for reuse.

    An async HTTP client belongs to the loop it first ran on. A plugin that keeps one per loop gets
    connection reuse (and one TLS handshake) from every caller on that loop. Each resource has ``aclose()``.

    Each resource has a keeper task on its own loop. When that loop shuts down (``asyncio.run`` cancels and
    drains its tasks) or the cache is closed, the keeper closes the resource while the loop is still alive, so
    a loop that is dropped without anyone calling ``close()`` does not leave sockets open.
    """

    def __init__(self) -> None:
        self._items: dict[int, tuple[asyncio.AbstractEventLoop, Any, asyncio.Task[None]]] = {}
        self._lock = threading.Lock()

    def get(self, make) -> Any:
        loop = asyncio.get_running_loop()
        with self._lock:
            for key in [k for k, (lp, _, _) in self._items.items() if lp.is_closed()]:
                del self._items[key]
            item = self._items.get(id(loop))
            if item is None:
                resource = make()
                item = self._items[id(loop)] = (loop, resource, loop.create_task(_keep(resource)))
            return item[1]

    def close(self) -> None:
        """Close every resource, from any thread: on its own loop when that runs, else here. Does not wait."""
        with self._lock:
            items, self._items = list(self._items.values()), {}
        for loop, resource, keeper in items:
            if loop.is_closed():
                continue
            try:
                if loop.is_running():
                    loop.call_soon_threadsafe(keeper.cancel)
                else:
                    keeper.cancel()
                    loop.run_until_complete(asyncio.gather(keeper, return_exceptions=True))
            except RuntimeError:  # called from inside another running loop: the resource closes with its loop
                logger.debug("could not close a %s on its loop", type(resource).__name__, exc_info=True)

    def drop(self) -> None:
        """Forget this loop's resource (it failed; the next call builds a fresh one) and close it in the background."""
        loop = asyncio.get_running_loop()
        with self._lock:
            item = self._items.pop(id(loop), None)
        if item is not None:
            item[2].cancel()


async def _keep(resource: Any) -> None:
    """Hold *resource* open until the task is cancelled (its loop shuts down, or the cache closes it)."""
    try:
        await asyncio.Event().wait()
    finally:
        try:
            await resource.aclose()
        except Exception:  # noqa: BLE001 - a close that fails is not news at shutdown
            logger.debug("closing a %s failed", type(resource).__name__, exc_info=True)


def warn_sync_plugin(owner: Any, sync_name: str, async_name: str) -> None:
    """Say once per plugin class that it is sync and what the async form is. Sync stays supported."""
    cls = owner if isinstance(owner, type) else type(owner)
    key = (cls, sync_name)
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(
        "plugin %s implements %s(), which runs on a worker thread; implement async %s() to run it on the event loop",
        cls.__name__,
        sync_name,
        async_name,
    )


async def settle(value: Any) -> Any:
    """Await *value* when a plugin hook returned an awaitable; otherwise return it as it is.

    A hook that only computes (trim a history, pick a peer) is a plain function. A hook that waits on I/O
    (summarize with an LLM) is ``async def``. The runtime awaits both with one line.
    """
    return await value if inspect.isawaitable(value) else value


async def acall(fn, /, *args, **kwargs):
    """Call a plugin callback that may be sync or async.

    An ``async def`` is awaited. A plain function runs on the worker threads, so a blocking callback
    cannot hold the event loop; a coroutine it returns is awaited.
    """
    if inspect.iscoroutinefunction(inspect.unwrap(fn)):
        return await fn(*args, **kwargs)
    return await settle(await run_sync(fn, *args, **kwargs))


__all__ = [
    "DEFAULT_SYNC_THREADS",
    "LoopCache",
    "acall",
    "configure_sync_threads",
    "run_sync",
    "settle",
    "sync_threads",
    "await_on_loop",
    "warn_sync_plugin",
]

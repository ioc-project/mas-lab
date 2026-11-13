#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import atexit
import threading
import weakref
from collections.abc import Coroutine
from typing import Any, Dict, List, TypeVar

from library_ioa.plugins.mcp.client import MCPClient

_T = TypeVar("_T")


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run an async MCP operation from sync plugin code.

    Uses ``asyncio.run`` when no loop is running. If the caller already has a
    loop, runs the coroutine on a private loop in a worker thread so a
    one-shot transport never spans loops.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    holder: dict[str, Any] = {}

    def _worker() -> None:
        try:
            holder["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
            holder["error"] = exc

    thread = threading.Thread(target=_worker, name="mcp-run-sync", daemon=True)
    thread.start()
    thread.join()
    if "error" in holder:
        raise holder["error"]
    return holder["result"]


class _DedicatedLoop:
    """One event loop for the life of an MCPClientWrapper."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._guard = threading.Lock()

    @property
    def started(self) -> bool:
        return self._loop is not None

    def ensure(self) -> asyncio.AbstractEventLoop:
        # Callers are plugin threads (parallel tool batches): start one loop only.
        with self._guard:
            if self._loop is not None:
                return self._loop

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            self._thread = threading.Thread(target=_run, name="mcp-loop", daemon=True)
            self._thread.start()
            self._ready.wait()
            assert self._loop is not None
            return self._loop

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        return asyncio.run_coroutine_threadsafe(coro, self.ensure()).result()

    def stop(self) -> None:
        with self._guard:
            self._stop_locked()

    def _stop_locked(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._ready = threading.Event()


def _close_weak(ref: "weakref.ReferenceType[MCPClientWrapper]") -> None:
    wrapper = ref()
    if wrapper is None:
        return
    try:
        wrapper.close()
    except Exception:  # noqa: BLE001 - exit path; nothing left to report to
        pass


class MCPClientWrapper:
    """Bridge the async MCP client to the synchronous runtime plugin API."""

    def __init__(self, client: MCPClient):
        self._client = client
        self._loop = _DedicatedLoop()
        self._registered = False

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run a client coroutine on the wrapper's loop, reusing one session."""

        if not self._registered:
            # A held stdio server is a child process: close it at exit.
            self._registered = True
            atexit.register(_close_weak, weakref.ref(self))

        async def _sticky() -> _T:
            await self._client.connect()
            return await coro

        return self._loop.run(_sticky())

    async def list_tools(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await self._client.list_tools(**kwargs)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], **options: Any) -> Dict[str, Any]:
        return await self._client.call_tool(tool_name, arguments, **options)

    def invalidate_list_cache(self) -> None:
        self._client.invalidate_list_cache()

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    def close(self) -> None:
        """Close the held session on the wrapper loop, then stop the loop. Idempotent."""
        if not self._loop.started:
            return
        try:
            self._loop.run(self._client.disconnect())
        finally:
            self._loop.stop()

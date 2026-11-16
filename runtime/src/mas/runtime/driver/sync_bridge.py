#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The one place a non-async caller waits for async work.

The runtime is async. A caller that is not (a script, a test, a plain CLI loop) goes through a
:class:`SyncBridge`: one private event loop per owner (a driver, a session controller), kept for the
owner's life, so anything bound to the loop (an HTTP client, a lock) stays valid between calls.
There is no background thread. A bridge refuses to run inside a running loop: async code calls the
async method.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


class SyncBridge:
    """Run an awaitable to completion on this bridge's own loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self, awaitable: Awaitable[T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise RuntimeError(
                "a sync call cannot wait for async work inside a running event loop; await the async method"
            )
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            # an owner that is dropped without close() must not leak the loop's file descriptors
            weakref.finalize(self, _close_loop, self._loop, drain=False)
        return self._loop.run_until_complete(awaitable)

    def close(self) -> None:
        loop, self._loop = self._loop, None
        _close_loop(loop)


def _close_loop(loop: asyncio.AbstractEventLoop | None, *, drain: bool = True) -> None:
    """Close *loop*. With ``drain`` (an explicit ``close()``), cancel what still runs on it first, so a kept
    HTTP client closes itself. A finalizer runs at an arbitrary moment, maybe inside another running loop, so
    it only closes the loop."""
    if loop is None or loop.is_closed() or loop.is_running():
        return
    if drain:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except RuntimeError:  # another loop is running in this thread: close without draining
            pass
    loop.close()


__all__ = ["SyncBridge"]

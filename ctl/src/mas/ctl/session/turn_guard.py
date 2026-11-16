#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One turn at a time for an agent, whichever thread or loop asks.

The guard is a plain lock taken without blocking a loop: ``async with guard.ahold()`` waits in a worker
thread, ``with guard.hold()`` waits in the caller's thread. It is re-entrant along one call chain (the
holder is remembered in a context variable), so an agent that delegates and is delegated back to by its
own call does not wait for itself. Anyone else waits up to ``wait_s`` and then gets an error.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from mas.runtime.boundary.agentcomm.protocol import AgentCommError

_HELD: contextvars.ContextVar[frozenset[int]] = contextvars.ContextVar("mas_turn_guards_held", default=frozenset())


class TurnGuard:
    def __init__(self, agent_id: str = "", wait_s: float = 120.0) -> None:
        self.agent_id = agent_id
        self.wait_s = float(wait_s)
        self._lock = threading.Lock()

    def _refused(self) -> AgentCommError:
        return AgentCommError(f"agent {self.agent_id!r} did not become free in {self.wait_s:.0f}s")

    @contextmanager
    def hold(self) -> Iterator[None]:
        held = _HELD.get()
        if id(self) in held:
            yield
            return
        if not self._lock.acquire(timeout=self.wait_s):
            raise self._refused()
        token = _HELD.set(held | {id(self)})
        try:
            yield
        finally:
            _HELD.reset(token)
            self._lock.release()

    @asynccontextmanager
    async def ahold(self) -> AsyncIterator[None]:
        held = _HELD.get()
        if id(self) in held:
            yield
            return
        # Poll without blocking a thread: a waiter that is cancelled (a request timed out) has acquired nothing,
        # so it cannot leave the lock held. A thread blocked in ``acquire`` would take it after the cancel.
        deadline = time.monotonic() + self.wait_s
        delay = 0.001
        while not self._lock.acquire(blocking=False):
            if time.monotonic() >= deadline:
                raise self._refused()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.02)
        token = _HELD.set(held | {id(self)})
        try:
            yield
        finally:
            _HELD.reset(token)
            self._lock.release()


__all__ = ["TurnGuard"]

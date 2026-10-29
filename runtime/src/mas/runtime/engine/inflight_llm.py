#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-flight LLM tasks — hard interrupt of ``ainvoke`` only.

A2A ``tasks/cancel`` maps to :func:`cancel` (drop the remainder, no follow-up).
:func:`request_preempt` is control-protocol ``steer``: keep streamed tokens
and continue the same generation. :func:`request_replace` discards the
partial and starts a new turn. A2A has no steer RPC; additional
``message/send`` traffic is queued, not preempted.

Pending steers live on one :class:`InflightStore` (``store``). Module
functions wrap that instance because the live engine, driver, and
``ControlContract`` already call ``inflight_llm.request_preempt`` by
name. New code should use ``store``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PendingSteer:
    text: str
    source: str = ""
    sender: str = ""
    intent: str = "steer"


@dataclass
class InflightStore:
    """Per-process inbox for in-flight LLM tasks and pending steers."""

    tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    partials: dict[str, str] = field(default_factory=dict)
    preempts: dict[str, PendingSteer] = field(default_factory=dict)
    replaces: dict[str, PendingSteer] = field(default_factory=dict)

    def register(self, session_id: str, task: asyncio.Task[Any]) -> None:
        if session_id:
            self.tasks[session_id] = task

    def clear(self, session_id: str) -> None:
        self.tasks.pop(session_id, None)
        if session_id not in self.preempts and session_id not in self.replaces:
            self.partials.pop(session_id, None)

    def append_partial(self, session_id: str, text: str) -> None:
        if not session_id or not text:
            return
        self.partials[session_id] = self.partials.get(session_id, "") + text

    def take_partial(self, session_id: str) -> str:
        return self.partials.pop(session_id, "")

    def peek_preempt(self, session_id: str) -> str | None:
        rec = self.preempts.get(session_id)
        return rec.text if rec is not None else None

    def pop_preempt(self, session_id: str) -> PendingSteer | None:
        return self.preempts.pop(session_id, None)

    def peek_replace(self, session_id: str) -> str | None:
        rec = self.replaces.get(session_id)
        return rec.text if rec is not None else None

    def pop_replace(self, session_id: str) -> PendingSteer | None:
        return self.replaces.pop(session_id, None)

    def request_preempt(
        self,
        session_id: str,
        text: str,
        *,
        source: str = "",
        sender: str = "",
        intent: str = "steer",
    ) -> bool:
        if session_id:
            self.preempts[session_id] = PendingSteer(
                text=text, source=source, sender=sender, intent=intent
            )
            self.replaces.pop(session_id, None)
        return self.cancel(session_id)

    def request_replace(
        self,
        session_id: str,
        text: str,
        *,
        source: str = "",
        sender: str = "",
        intent: str = "steer",
    ) -> bool:
        if session_id:
            self.replaces[session_id] = PendingSteer(
                text=text, source=source, sender=sender, intent=intent
            )
            self.preempts.pop(session_id, None)
            self.partials.pop(session_id, None)
        return self.cancel(session_id)

    def cancel(self, session_id: str) -> bool:
        task = self.tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def has_inflight(self, session_id: str) -> bool:
        task = self.tasks.get(session_id)
        return task is not None and not task.done()


store = InflightStore()


def register(session_id: str, task: asyncio.Task[Any]) -> None:
    store.register(session_id, task)


def clear(session_id: str) -> None:
    store.clear(session_id)


def append_partial(session_id: str, text: str) -> None:
    store.append_partial(session_id, text)


def take_partial(session_id: str) -> str:
    return store.take_partial(session_id)


def peek_preempt(session_id: str) -> str | None:
    return store.peek_preempt(session_id)


def pop_preempt(session_id: str) -> PendingSteer | None:
    return store.pop_preempt(session_id)


def peek_replace(session_id: str) -> str | None:
    return store.peek_replace(session_id)


def pop_replace(session_id: str) -> PendingSteer | None:
    return store.pop_replace(session_id)


def request_preempt(
    session_id: str,
    text: str,
    *,
    source: str = "",
    sender: str = "",
    intent: str = "steer",
) -> bool:
    """Keep streamed tokens and stop the rest of this decode (steer)."""
    return store.request_preempt(
        session_id, text, source=source, sender=sender, intent=intent
    )


def request_replace(
    session_id: str,
    text: str,
    *,
    source: str = "",
    sender: str = "",
    intent: str = "steer",
) -> bool:
    """Discard streamed tokens and stop this decode for a replacement turn."""
    return store.request_replace(
        session_id, text, source=source, sender=sender, intent=intent
    )


def peek_amend(session_id: str) -> str | None:
    """Alias of :func:`peek_preempt`."""
    return peek_preempt(session_id)


def pop_amend(session_id: str) -> PendingSteer | None:
    """Alias of :func:`pop_preempt`."""
    return pop_preempt(session_id)


def request_amend(
    session_id: str,
    text: str,
    *,
    source: str = "",
    sender: str = "",
    intent: str = "steer",
) -> bool:
    """Alias of :func:`request_preempt`."""
    return request_preempt(
        session_id, text, source=source, sender=sender, intent=intent
    )


def cancel(session_id: str) -> bool:
    """Cancel the in-flight LLM ``ainvoke``. Tools are not cancellable here."""
    return store.cancel(session_id)


def has_inflight(session_id: str) -> bool:
    return store.has_inflight(session_id)

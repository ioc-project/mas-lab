#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Topic-scoped in-memory pub/sub for one gateway process."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field

from mas.lab.controller.ui_stream.refs import DataSourceRef, EventEnvelope, topic_matches


@dataclass
class Subscription:
    topics: tuple[str, ...]
    ref: DataSourceRef
    nonce: str | None = None
    queue: asyncio.Queue[EventEnvelope] = field(default_factory=lambda: asyncio.Queue(maxsize=2000))


class EventBus:
    def __init__(self, *, max_history: int | None = 4000) -> None:
        self._subs: list[Subscription] = []
        self._history: list[EventEnvelope] = []
        self._max_history = max_history

    def publish(self, envelope: EventEnvelope) -> None:
        self._history.append(envelope)
        if self._max_history and len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        for sub in list(self._subs):
            if not topic_matches(sub.topics, envelope.topic):
                continue
            if not sub.ref.matches(envelope.ref):
                continue
            if envelope.nonce and sub.nonce and envelope.nonce != sub.nonce:
                if not envelope.replay and envelope.topic != "view.reset":
                    continue
            try:
                sub.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()
                except Exception:
                    pass
                try:
                    sub.queue.put_nowait(envelope)
                except Exception:
                    pass

    def subscribe(
        self, topics: Iterable[str], ref: DataSourceRef, *, nonce: str | None = None
    ) -> Subscription:
        sub = Subscription(topics=tuple(topics), ref=ref, nonce=str(nonce) if nonce else None)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        try:
            self._subs.remove(sub)
        except ValueError:
            return

    def drop(self, *, run: str | None = None, batch: str | None = None, origin: str | None = None) -> int:
        kept: list[EventEnvelope] = []
        removed = 0
        for item in self._history:
            ref = item.ref
            match = False
            if run and ref.run == run:
                match = True
            if batch and ref.batch == batch:
                match = True
            if origin and ref.origin == origin:
                match = True
            if match:
                removed += 1
                continue
            kept.append(item)
        self._history = kept
        return removed

    def clear(self) -> None:
        self._history.clear()
        for sub in list(self._subs):
            while True:
                try:
                    sub.queue.get_nowait()
                except Exception:
                    break

    def wants_topic(self, topic: str, ref: DataSourceRef) -> bool:
        """True when a live WS/plugin subscription would receive ``topic`` for ``ref``."""
        for sub in self._subs:
            if not topic_matches(sub.topics, topic):
                continue
            if sub.ref.matches(ref):
                return True
        return False

    def demands_topic(self, topic: str, ref: DataSourceRef) -> bool:
        """True when a subscription named this topic (not a catch-all ``*``)."""
        for sub in self._subs:
            globs = tuple(glob for glob in sub.topics if glob not in {"", "*"})
            if not globs:
                continue
            if not topic_matches(globs, topic):
                continue
            if sub.ref.matches(ref):
                return True
        return False

    def snapshot(self, topics: Iterable[str], ref: DataSourceRef) -> list[EventEnvelope]:
        globs = tuple(topics)
        out: list[EventEnvelope] = []
        for item in self._history:
            if not topic_matches(globs, item.topic):
                continue
            if not ref.matches(item.ref):
                continue
            out.append(item)
        return out

    def catchup(
        self,
        topics: Iterable[str],
        ref: DataSourceRef,
        *,
        nonce: str | None = None,
    ) -> list[EventEnvelope]:
        """Latest envelope per topic after the last ``view.reset``.

        A tab that connects after ingest has finished still paints from this
        list. Empty ``topics`` means nothing (do not dump the whole bus).
        """
        globs = tuple(str(item) for item in topics if str(item or "").strip())
        if not globs:
            return []
        items = self.snapshot(globs, ref)
        last_reset: EventEnvelope | None = None
        latest: dict[str, EventEnvelope] = {}
        for item in items:
            if item.nonce and nonce and item.nonce != nonce:
                if not item.replay and item.topic != "view.reset":
                    continue
            if item.topic == "view.reset":
                last_reset = item
                latest.clear()
                continue
            latest[item.topic] = item
        out: list[EventEnvelope] = []
        if last_reset is not None:
            out.append(last_reset)
        out.extend(latest.values())
        return out

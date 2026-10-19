#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process fan-out of ExchangeRecord payloads for cross-process RPC tails.

KernelDriver subscribers publish here. ControlRpcServer subscribe_exchange
consumers attach a queue per session. Catch-up copies are marked replay.
The hub is injected by the host (ctl); there is no process-wide singleton.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from typing import Any

from mas.runtime.boundary.obs.exchange_plugin import ExchangePlugin

_MAX_BUFFER = 500


def record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    return {"repr": str(record)}


class ExchangeHub:
    """Thread-safe per-session buffer + live queues."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffers: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=_MAX_BUFFER)
        )
        self._queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    def publish(self, session_id: str, record: Any, *, replay: bool = False) -> None:
        if not session_id:
            return
        item = {"session_id": session_id, "replay": replay, "record": record_to_dict(record)}
        with self._lock:
            self._buffers[session_id].append(item)
            queues = list(self._queues.get(session_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except Exception:
                    pass
                try:
                    queue.put_nowait(item)
                except Exception:
                    pass

    def buffer(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item, replay=True) for item in self._buffers.get(session_id, ())]

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_BUFFER)
        with self._lock:
            self._queues[session_id].append(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            holders = self._queues.get(session_id)
            if not holders:
                return
            try:
                holders.remove(queue)
            except ValueError:
                return
            if not holders:
                self._queues.pop(session_id, None)


class HubExchangePlugin(ExchangePlugin):
    """Driver subscriber that publishes every hop onto an ExchangeHub."""

    def __init__(self, hub: ExchangeHub, session_id: str) -> None:
        self.hub = hub
        self.session_id = session_id

    def on_exchange(self, record: Any) -> None:
        self.hub.publish(self.session_id, record, replay=False)

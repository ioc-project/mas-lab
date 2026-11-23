#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run-keyed hub: pipeline steps emit events into the live environment."""

from __future__ import annotations

import threading
from typing import Any, Mapping, Protocol

from mas.lab.benchmark.pipeline.keys import RunKey


class _EventSink(Protocol):
    def deliver_event(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> bool: ...


_LOCK = threading.Lock()
_PORTS: dict[str, _EventSink] = {}


class EnvironmentHub:
    """Routes ``emit_event`` from a live pipeline step to the run's environment."""

    def bind(self, key: RunKey, port: _EventSink) -> None:
        with _LOCK:
            _PORTS[key.stream_id()] = port

    def unbind(self, key: RunKey, port: _EventSink | None = None) -> None:
        """Drop the binding; with *port*, only if it is still this one (a rerun may have bound a new one)."""
        with _LOCK:
            if port is None or _PORTS.get(key.stream_id()) is port:
                _PORTS.pop(key.stream_id(), None)

    def emit(self, key: RunKey, name: str, payload: dict[str, Any] | None = None) -> bool:
        with _LOCK:
            port = _PORTS.get(key.stream_id())
        if port is None:
            return False
        return bool(
            port.deliver_event(
                name,
                payload or {},
                source={"type": "pipeline", "id": "pipeline"},
            )
        )


_HUB = EnvironmentHub()


def get_environment_hub() -> EnvironmentHub:
    return _HUB

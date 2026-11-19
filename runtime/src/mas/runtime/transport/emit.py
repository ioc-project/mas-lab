#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session-scoped transport event bus. Off means the sink is unset and emit is a no-op.

No OpenTelemetry import. Protocol plugins call :func:`emit_transport` from typed
hooks. The native observability plugin binds a sink that writes JSONL.
"""

from __future__ import annotations

from threading import Lock
from typing import Protocol as TypingProtocol

from mas.runtime.schema.transport import (
    DISCOVERY,
    ERROR,
    INTERNAL,
    SESSION,
    SOURCE_HOOK,
    TransportEvent,
)
from mas.runtime.transport.config import (
    DISABLED,
    ProtocolTransportConfig,
    TransportObservabilityConfig,
)


class TransportSink(TypingProtocol):
    def emit(self, event: TransportEvent) -> None: ...


class _FanOut:
    def __init__(self) -> None:
        self._sinks: list[TransportSink] = []
        self._lock = Lock()

    def add(self, sink: TransportSink) -> None:
        with self._lock:
            if all(existing is not sink for existing in self._sinks):
                self._sinks.append(sink)

    def discard(self, sink: TransportSink) -> None:
        with self._lock:
            self._sinks = [existing for existing in self._sinks if existing is not sink]

    def clear(self) -> None:
        with self._lock:
            self._sinks = []

    def emit(self, event: TransportEvent) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink.emit(event)
            except Exception:
                _note_drop()


_lock = Lock()
_config = TransportObservabilityConfig()
_fanout = _FanOut()
_dropped = 0
_enabled = False


def _note_drop() -> None:
    global _dropped
    with _lock:
        _dropped += 1


def bind_config(config: TransportObservabilityConfig | None) -> None:
    """Install the process config. ``None`` or ``present=False`` turns the bus off."""
    global _config, _enabled
    with _lock:
        _config = config or TransportObservabilityConfig()
        _enabled = _config.any_enabled()


def bind_sink(sink: TransportSink | None) -> None:
    """Subscribe a sink. ``None`` is ignored. Off still accepts a bind (no emit)."""
    if sink is None:
        return
    _fanout.add(sink)


def unbind_sink(sink: TransportSink) -> None:
    _fanout.discard(sink)


def reset() -> None:
    """Tests: drop config, sinks, and the overflow counter."""
    global _config, _enabled, _dropped
    with _lock:
        _config = TransportObservabilityConfig()
        _enabled = False
        _dropped = 0
        _fanout.clear()


def protocol_config(name: str) -> ProtocolTransportConfig:
    if not _enabled:
        return DISABLED
    cfg = _config.for_protocol(name)
    return cfg if cfg.enabled else DISABLED


def is_enabled() -> bool:
    return _enabled


def dropped_count() -> int:
    return _dropped


def emit_transport(event: TransportEvent) -> None:
    """Hand one event to bound sinks. No-op when transport observability is off."""
    if not _enabled:
        return
    cfg = _config.for_protocol(event.protocol)
    if not cfg.enabled:
        return
    if event.source == SOURCE_HOOK and not cfg.hooks:
        if event.kind.startswith("transport.message") or event.kind in {ERROR, DISCOVERY}:
            return
    if not cfg.internals and event.kind in {SESSION, INTERNAL}:
        return
    _fanout.emit(event)


__all__ = [
    "TransportSink",
    "bind_config",
    "bind_sink",
    "dropped_count",
    "emit_transport",
    "is_enabled",
    "protocol_config",
    "reset",
    "unbind_sink",
]

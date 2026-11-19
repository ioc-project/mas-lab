#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed SLIM hooks on send, receive, and session setup. No metadata on the payload."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from mas.runtime.schema.transport import (
    ERROR,
    MESSAGE_COMPLETED,
    MESSAGE_RECEIVED,
    MESSAGE_SENT,
    SESSION,
    SOURCE_HOOK,
    TransportEvent,
)
from mas.runtime.transport.emit import emit_transport, protocol_config

PROTOCOL = "slim"
_logger = logging.getLogger(__name__)


def _cfg():
    return protocol_config(PROTOCOL)


def hooks_enabled() -> bool:
    """True when SLIM message hooks should run. Call sites check this before building anything."""
    return _cfg().hooks


def call_key(context_id: Any, correlation_id: Any) -> str:
    """The key both ends derive from the envelope. A request that names neither gets a fresh key."""
    context = str(context_id or "")
    if context:
        return context
    if correlation_id not in (None, "", 0, "0"):
        return str(correlation_id)
    return f"slim:{uuid.uuid4().hex[:12]}"


def _emit(kind: str, *, side: str, key: str, **attrs: Any) -> None:
    if not (_cfg().internals if kind == SESSION else _cfg().hooks):
        return
    attributes = {k: v for k, v in attrs.items() if v not in (None, "")}
    try:
        emit_transport(
            TransportEvent(
                kind=kind,
                protocol=PROTOCOL,
                side=side,
                correlation_key=key or "slim",
                source=SOURCE_HOOK,
                timestamp=time.time(),
                session_id=str(attributes.pop("session_id", "") or ""),
                agent_id=str(attributes.pop("agent_id", "") or attributes.get("source") or ""),
                call_id=str(attributes.pop("call_id", "") or ""),
                run_id=str(attributes.pop("run_id", "") or ""),
                mas_id=str(attributes.pop("mas_id", "") or ""),
                attributes=attributes,
            )
        )
    except Exception:
        _logger.debug("slim transport emit failed", exc_info=True)


def emit_sent(*, source: str, target: str, key: str, **attrs: Any) -> None:
    _emit(MESSAGE_SENT, side="client", key=key, source=source, target=target, **attrs)


def emit_received(*, source: str, target: str, key: str, **attrs: Any) -> None:
    _emit(MESSAGE_RECEIVED, side="server", key=key, source=source, target=target, **attrs)


def emit_completed(*, side: str, source: str, target: str, key: str, **attrs: Any) -> None:
    _emit(MESSAGE_COMPLETED, side=side, key=key, source=source, target=target, **attrs)


def emit_error(*, side: str, key: str, **attrs: Any) -> None:
    _emit(ERROR, side=side, key=key, **attrs)


def emit_session(*, side: str, key: str, **attrs: Any) -> None:
    if not _cfg().internals:
        return
    _emit(SESSION, side=side, key=key, **attrs)


__all__ = [
    "PROTOCOL",
    "call_key",
    "emit_completed",
    "emit_error",
    "emit_received",
    "emit_sent",
    "emit_session",
    "hooks_enabled",
]

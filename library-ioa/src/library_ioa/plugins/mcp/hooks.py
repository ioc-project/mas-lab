#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed MCP hooks on list_tools / call_tool."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Mapping, Sequence

from mas.runtime.schema.transport import (
    DISCOVERY,
    ERROR,
    MESSAGE_COMPLETED,
    MESSAGE_RECEIVED,
    MESSAGE_SENT,
    SOURCE_HOOK,
    TransportEvent,
)
from mas.runtime.transport.emit import emit_transport, protocol_config

PROTOCOL = "mcp"
_logger = logging.getLogger(__name__)


def _cfg():
    return protocol_config(PROTOCOL)


_MAX_TOOLS = 256
_MAX_TEXT = 1024


def hooks_enabled() -> bool:
    """True when MCP message hooks should run. Call sites check this before building anything."""
    return _cfg().hooks


def new_call_key(tool: str) -> str:
    """A key for one call. Two concurrent calls of the same tool must not share it."""
    return f"call:{tool}:{uuid.uuid4().hex[:12]}"


def _emit(kind: str, *, side: str, key: str, **attrs: Any) -> None:
    if not _cfg().hooks:
        return
    attributes = {k: v for k, v in attrs.items() if v not in (None, "")}
    try:
        emit_transport(
            TransportEvent(
                kind=kind,
                protocol=PROTOCOL,
                side=side,
                correlation_key=key or "mcp",
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
        _logger.debug("mcp transport emit failed", exc_info=True)


def emit_call_sent(*, tool: str, source: str, target: str, key: str, **attrs: Any) -> None:
    _emit(
        MESSAGE_SENT,
        side="client",
        key=key,
        source=source,
        target=target,
        tool=tool,
        **attrs,
    )


def emit_call_received(*, tool: str, source: str, target: str, key: str, **attrs: Any) -> None:
    _emit(
        MESSAGE_RECEIVED,
        side="server",
        key=key,
        source=source,
        target=target,
        tool=tool,
        **attrs,
    )


def emit_call_completed(*, side: str, tool: str, source: str, target: str, key: str, **attrs: Any) -> None:
    _emit(
        MESSAGE_COMPLETED,
        side=side,
        key=key,
        source=source,
        target=target,
        tool=tool,
        **attrs,
    )


def emit_call_error(*, side: str, tool: str, key: str, **attrs: Any) -> None:
    _emit(ERROR, side=side, key=key, tool=tool, **attrs)


def emit_tools_discovered(
    tools: Sequence[Mapping[str, Any]],
    *,
    agent_id: str,
    side: str = "client",
) -> None:
    """Announce a ``tools/list`` result. Bounded, and never raises into ``list_tools``."""
    try:
        if not _cfg().hooks:
            return
        entries = [
            {
                "name": str(t.get("name") or "")[:_MAX_TEXT],
                "description": str(t.get("description") or "")[:_MAX_TEXT],
            }
            for t in list(tools)[:_MAX_TOOLS]
            if isinstance(t, Mapping)
        ]
        _emit(
            DISCOVERY,
            side=side,
            key=f"tools:{agent_id}",
            discovery_kind="tools",
            tools={"tools": entries, "truncated": len(tools) > _MAX_TOOLS},
            agent_id=agent_id,
            source=agent_id,
        )
    except Exception:
        _logger.debug("mcp tools discovered hook failed", exc_info=True)


__all__ = [
    "PROTOCOL",
    "emit_call_completed",
    "emit_call_error",
    "emit_call_received",
    "emit_call_sent",
    "emit_tools_discovered",
    "hooks_enabled",
    "new_call_key",
]

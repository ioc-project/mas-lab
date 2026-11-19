#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native transport events — protocol-agnostic records of a boundary crossing.

A record is self-contained: node and edge ids a consumer derives from it use
only these fields. The same record delivered twice, or in another order,
must produce the same graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

MESSAGE_SENT = "transport.message.sent"
MESSAGE_RECEIVED = "transport.message.received"
MESSAGE_COMPLETED = "transport.message.completed"
DISCOVERY = "transport.discovery"
SESSION = "transport.session"
INTERNAL = "transport.internal"
ERROR = "transport.error"

TRANSPORT_KINDS: frozenset[str] = frozenset(
    {
        MESSAGE_SENT,
        MESSAGE_RECEIVED,
        MESSAGE_COMPLETED,
        DISCOVERY,
        SESSION,
        INTERNAL,
        ERROR,
    }
)

SOURCE_HOOK = "hook"
SOURCE_SDK_SPAN = "sdk-span"
SOURCES: frozenset[str] = frozenset({SOURCE_HOOK, SOURCE_SDK_SPAN})

SIDES: frozenset[str] = frozenset({"client", "server"})


@dataclass(frozen=True)
class TransportEvent:
    """One transport fact. Extra keys in ``attributes`` are kept on round-trip."""

    kind: str
    protocol: str
    side: str
    correlation_key: str
    source: str
    timestamp: float
    session_id: str = ""
    agent_id: str = ""
    call_id: str = ""
    run_id: str = ""
    mas_id: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in TRANSPORT_KINDS:
            raise ValueError(f"unknown transport kind {self.kind!r}")
        if self.source not in SOURCES:
            raise ValueError(f"unknown transport source {self.source!r}")
        if self.side not in SIDES:
            raise ValueError(f"unknown transport side {self.side!r}")

    def to_record(self) -> dict[str, Any]:
        """JSONL-ready dict. Extra attribute keys are preserved."""
        record: dict[str, Any] = {
            "kind": self.kind,
            "category": "transport",
            "protocol": self.protocol,
            "side": self.side,
            "correlation_key": self.correlation_key,
            "source": self.source,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "call_id": self.call_id,
            "run_id": self.run_id,
            "mas_id": self.mas_id,
            "attributes": dict(self.attributes),
        }
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TransportEvent:
        """Rebuild from a JSONL dict. Unknown extra top-level keys are ignored;
        unknown ``attributes`` keys are kept."""
        attrs = record.get("attributes") or {}
        if not isinstance(attrs, Mapping):
            attrs = {}
        return cls(
            kind=str(record.get("kind") or ""),
            protocol=str(record.get("protocol") or ""),
            side=str(record.get("side") or ""),
            correlation_key=str(record.get("correlation_key") or ""),
            source=str(record.get("source") or SOURCE_HOOK),
            timestamp=float(record.get("timestamp") or 0.0),
            session_id=str(record.get("session_id") or ""),
            agent_id=str(record.get("agent_id") or ""),
            call_id=str(record.get("call_id") or ""),
            run_id=str(record.get("run_id") or ""),
            mas_id=str(record.get("mas_id") or ""),
            attributes=dict(attrs),
        )


__all__ = [
    "DISCOVERY",
    "ERROR",
    "INTERNAL",
    "MESSAGE_COMPLETED",
    "MESSAGE_RECEIVED",
    "MESSAGE_SENT",
    "SESSION",
    "SIDES",
    "SOURCE_HOOK",
    "SOURCE_SDK_SPAN",
    "SOURCES",
    "TRANSPORT_KINDS",
    "TransportEvent",
]

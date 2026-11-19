#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Classifier contract for protocol plugins. Runtime never imports OpenTelemetry.

A plugin supplies a classifier that reads a :class:`SpanView` (copied span
fields) and returns a decision. Span names and attributes stay in the plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class SpanView:
    """Frozen copy of the fields a classifier may read. No SDK types."""

    name: str
    scope_name: str
    scope_version: str = ""
    kind: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)
    span_id: str = ""
    parent_span_id: str = ""
    trace_id: str = ""
    status: str = ""
    start_time_ns: int = 0
    end_time_ns: int = 0


@dataclass(frozen=True)
class Drop:
    """Ignore this span. It is a duplicate of a hook event, or noise."""


@dataclass(frozen=True)
class Emit:
    """Turn the span into a native event. ``event_fields`` feeds TransportEvent."""

    event_fields: Mapping[str, Any]


@dataclass(frozen=True)
class Passthrough:
    """Unknown span. Intake degrades it to ``transport.internal``."""


Decision = Drop | Emit | Passthrough


@dataclass(frozen=True)
class Alias:
    """One native kind projected to an observe-sdk-shaped compatibility event."""

    event: str
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AliasTable:
    """Native kind → compatibility event name and attribute rename map."""

    events: Mapping[str, Alias] = field(default_factory=dict)


@runtime_checkable
class TransportClassifier(Protocol):
    """Protocol a plugin implements. Listing the registry never imports this."""

    def scope_names(self) -> tuple[str, ...]:
        """Instrumentation scope names this classifier owns."""

    def supported_sdk_range(self) -> str:
        """PEP 440 specifier of SDK versions the mapping was tested on."""

    def mapped_span_names(self) -> tuple[str, ...]:
        """Span names this classifier claims. Shutdown warns if none were seen."""

    def classify_start(self, span: SpanView) -> Decision:
        """Decide on span start (name and initial attributes)."""

    def classify_end(self, span: SpanView) -> Decision:
        """Decide on span end (final attributes)."""

    def aliases(self) -> AliasTable:
        """Native kind to observe-sdk event names."""


__all__ = [
    "Alias",
    "AliasTable",
    "Decision",
    "Drop",
    "Emit",
    "Passthrough",
    "SpanView",
    "TransportClassifier",
]

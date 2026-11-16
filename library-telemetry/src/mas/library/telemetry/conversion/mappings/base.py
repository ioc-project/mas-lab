#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Extensible event → span handler registry.

The native-event → OTel-span mapping is expressed as a set of **handlers**, each
a plain function ``handler(conv, event) -> None`` that inspects a native event
record and emits one or more spans through the converter's primitive API
(:class:`SpanEmitter` below).

Handlers are grouped into **category modules** (``structural.py``,
``execution.py``, ``memory.py``, ``context.py``, ``governance.py``,
``trajectory.py``) that mirror the ontology *blocks* in
:mod:`mas.library.telemetry.conversion.envelope`.  This is the direct analogue
of how ``library-kg`` splits its OTel→KG mappings by wire format — it keeps each
file small and readable, and makes the mapping easy to extend.

Extending the mapping
---------------------
To support a new event ``kind``::

    from mas.library.telemetry.conversion.mappings.base import register

    @register("my_custom_event")
    def _h_my_custom_event(conv, ev):
        conv.point_span("CallAnnotation", {
            "mas.boundary": "CallAnnotation",
            "mas.agent.id": conv.agent_id(ev),
            "mas.annotation.kind": "my_custom_event",
        }, ev.get("parent_call_id"), ts_ns=conv.ts_ns(ev))

Put the handler in the category module matching its ontology block, or register
it from anywhere (third-party packages included) before building a converter.
One handler may be registered for several kinds: ``@register("a", "b")``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Protocol, runtime_checkable

# A handler maps a native event record to spans via the converter primitives.
Handler = Callable[["SpanEmitter", Dict[str, Any]], None]

# kind → handler
_REGISTRY: Dict[str, Handler] = {}


def register(*kinds: str) -> Callable[[Handler], Handler]:
    """Decorator registering *fn* as the span handler for one or more event kinds."""
    if not kinds:
        raise ValueError("register() requires at least one event kind")

    def _decorator(fn: Handler) -> Handler:
        for kind in kinds:
            _REGISTRY[kind] = fn
        return fn

    return _decorator


def build_handler_table() -> Dict[str, Handler]:
    """Return a fresh copy of the full kind → handler mapping.

    Importing this module's siblings (the category modules) is what populates the
    registry; :func:`build_handler_table` triggers those imports so every
    built-in handler is present regardless of import order.
    """
    # Import for side effects: each category module calls @register at import.
    from mas.library.telemetry.conversion.mappings import (  # noqa: F401
        context,
        execution,
        governance,
        memory,
        structural,
        trajectory,
    )

    return dict(_REGISTRY)


def registered_kinds() -> "list[str]":
    """Return the sorted list of event kinds with a registered handler."""
    build_handler_table()
    return sorted(_REGISTRY)


@runtime_checkable
class SpanEmitter(Protocol):
    """Primitive span-emission surface that handlers call.

    Implemented by
    :class:`mas.library.telemetry.conversion.converter.MasOtelConverter`.
    Handlers depend only on this protocol, never on converter internals.
    """

    def open_span(
        self,
        call_id: str,
        name: str,
        attrs: Dict[str, Any],
        parent_call_id: str | None = ...,
        start_ns: int | None = ...,
    ) -> None:
        """Open an interval span and track it by ``call_id``."""

    def close_span(
        self,
        call_id: str | None,
        extra: Dict[str, Any] | None = ...,
        status: str = ...,
        end_ns: int | None = ...,
    ) -> None:
        """Close the interval span previously opened for ``call_id``."""

    def point_span(
        self,
        name: str,
        attrs: Dict[str, Any],
        parent_call_id: str | None = ...,
        ts_ns: int | None = ...,
        call_id: str | None = ...,
    ) -> None:
        """Emit a zero-width point-in-time span."""

    def emit_duplicate_start_annotation(self, ev: Dict[str, Any], kind: str) -> bool:
        """Emit a CallAnnotation for a duplicate ``*_start`` on an open call."""

    def ts_ns(self, ev: Dict[str, Any]) -> int | None:
        """Event timestamp (float seconds) → nanoseconds int, or ``None``."""

    def agent_id(self, ev: Dict[str, Any]) -> str:
        """Agent id for *ev*, defaulting to ``'unknown'``."""

    def require_call_id(self, ev: Dict[str, Any]) -> str:
        """Event ``call_id``, generating a fresh UUID when absent."""

    def span_key(self, ev: Dict[str, Any]) -> str:
        """Tracking key for interval spans (nested wrappers vs structural calls)."""

    def enc(self, value: Any, limit: int | None = None) -> str:
        """Encode an arbitrary value to a JSON string (uncapped unless ``limit`` is given)."""

    def is_open(self, call_id: str | None) -> bool:
        """Whether an interval span is currently open for ``call_id``."""

    def is_closed(self, call_id: str | None) -> bool:
        """Whether a span for ``call_id`` has already been closed."""

    def emit_annotation(
        self,
        ev: Dict[str, Any],
        boundary: str,
        attrs: Dict[str, Any],
        *,
        fields: tuple[str, ...] = ...,
    ) -> None:
        """One point span for one annotation record (payload and target as the native fold)."""

    def state_start_attrs(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """``ioa_observe.state.*`` input and output id from a START record."""

    def state_end_attrs(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """``ioa_observe.state.output.content`` from an END record."""

    def agent_start_state_attrs(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """An agent visit's input State, from ``execution_start``."""

    def agent_end_state_attrs(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """An agent visit's output State, from ``execution_end``."""


__all__ = [
    "Handler",
    "SpanEmitter",
    "register",
    "build_handler_table",
    "registered_kinds",
]

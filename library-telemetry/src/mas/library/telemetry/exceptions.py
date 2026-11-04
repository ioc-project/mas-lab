#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed exception hierarchy for the events → OTel → collector telemetry pipeline.

Mirrors the shape of ``mas.library.kg.exceptions`` for the inverse direction:
where ``library-kg`` converts *OTel spans → KG*, this library converts
*native events → OTel spans* and then verifies / serializes them.
"""

from __future__ import annotations

from typing import Any

from mas.library.telemetry._dist import extra_hint as _extra_hint


class TelemetryError(Exception):
    """Base for telemetry-library errors."""


# ---------------------------------------------------------------------------
# Conversion (native events → OTel spans)
# ---------------------------------------------------------------------------


class ConversionError(TelemetryError):
    """Base for native-events → OTel-span conversion errors."""


class OtelSdkUnavailableError(ConversionError):
    """The OpenTelemetry SDK is required for conversion but is not installed."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "opentelemetry-sdk is not installed. Install the conversion extra: "
            f"{_extra_hint('convert')}"
            + (f" ({detail})" if detail else "")
        )


class UnknownEventKindError(ConversionError):
    """A native event ``kind`` has no registered span handler."""

    def __init__(self, kind: str, call_id: str = "") -> None:
        self.kind = kind
        self.call_id = call_id
        super().__init__(
            f"[call={call_id or '?'}] Unknown event kind {kind!r} — no span handler "
            "registered. Add a handler in the appropriate "
            "conversion/mappings/*.py category module and decorate it with "
            "@register(<kind>)."
        )


class MissingRequiredFieldError(ConversionError):
    """A required field is absent or empty on a native event record."""

    def __init__(self, field: str, kind: str, detail: str = "") -> None:
        self.field = field
        self.kind = kind
        super().__init__(
            f"[kind={kind}] Required field {field!r} is missing or empty"
            + (f" ({detail})" if detail else "")
        )


# ---------------------------------------------------------------------------
# Verification (OTel spans → conformance report)
# ---------------------------------------------------------------------------


class VerificationError(TelemetryError):
    """Base for span verification errors."""


class SpecNotFoundError(VerificationError):
    """A ``.spanspec.yaml`` file could not be located."""

    def __init__(self, path: Any) -> None:
        self.path = path
        super().__init__(f"SpanSpec not found: {path}")


# ---------------------------------------------------------------------------
# Serialization (OTel spans → collector / OTLP)
# ---------------------------------------------------------------------------


class SerializationError(TelemetryError):
    """Base for OTLP / collector serialization errors."""


class CollectorPushError(SerializationError):
    """Pushing spans to an OTLP collector failed."""

    def __init__(self, endpoint: str, detail: str) -> None:
        self.endpoint = endpoint
        super().__init__(f"OTLP push to {endpoint!r} failed: {detail}")


class ClickHouseUnavailableError(SerializationError):
    """``clickhouse-connect`` is required for dump but is not installed."""

    def __init__(self) -> None:
        super().__init__(
            "clickhouse-connect is not installed. Install the clickhouse extra: "
            f"{_extra_hint('clickhouse')}"
        )


__all__ = [
    "TelemetryError",
    "ConversionError",
    "OtelSdkUnavailableError",
    "UnknownEventKindError",
    "MissingRequiredFieldError",
    "VerificationError",
    "SpecNotFoundError",
    "SerializationError",
    "CollectorPushError",
    "ClickHouseUnavailableError",
]

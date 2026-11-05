#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Thin OTel → KG wrapper around ``norm.normalize()``.

This module does **not** reimplement OTel dispatch or span handlers.
It only:

1. Detects ClickHouse-shaped rows vs OTel SDK span JSON.
2. Converts SDK spans to the ClickHouse-shaped dicts ``norm`` expects
   (``SpanName``, ``SpanAttributes``, ``SpanId``, ``ServiceName``,
   ``Timestamp``, ``Duration``, ``ParentSpanId`` — see
   ``norm.ioa_observe.otel_io.load_otel_export``).
3. Delegates to ``norm.normalizer.normalize``.

No inference, gap-fill, or heuristic recovery lives here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from mas.library.kg.exceptions import OtelFormatError

_CLICKHOUSE_KEYS = frozenset({"SpanName", "SpanAttributes", "SpanId"})
_SDK_SHAPE_KEYS = frozenset({"context", "attributes"})


def _wire_hex(value: Any, length: int) -> str:
    """Lowercase hex, no ``0x``, zero-padded — ClickHouse / OTLP ``SpanId``."""
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return ""
    return text.zfill(length)[-length:]


def is_clickhouse_span(span: dict[str, Any]) -> bool:
    """True when *span* already has ClickHouse OTel-export keys."""
    return bool(_CLICKHOUSE_KEYS.intersection(span))


def is_sdk_span(span: dict[str, Any]) -> bool:
    """True when *span* looks like an OTel SDK ``ReadableSpan.to_json()`` dict."""
    if is_clickhouse_span(span):
        return False
    if "name" not in span:
        return False
    return bool(_SDK_SHAPE_KEYS.intersection(span)) or "start_time" in span


def detect_span_shape(spans: Sequence[dict[str, Any]]) -> str:
    """Return ``clickhouse_row``, ``sdk``, or raise :class:`OtelFormatError`."""
    if not spans:
        return "clickhouse_row"
    sample = next((s for s in spans if isinstance(s, dict)), None)
    if sample is None:
        raise OtelFormatError("span list contains no dict records")
    if is_clickhouse_span(sample):
        return "clickhouse_row"
    if is_sdk_span(sample):
        return "sdk"
    raise OtelFormatError(f"unrecognised span keys {sorted(sample.keys())[:12]!r}")


def _timestamp_to_clickhouse(value: Any) -> str:
    """Map an SDK timestamp onto the ClickHouse ``Timestamp`` string shape.

    ``norm.ioa_observe.fields`` parses ``YYYY-MM-DD HH:MM:SS[.fraction]``.
    ISO-8601 ``Z`` / offset strings are rewritten; numeric epoch seconds or
    nanoseconds are converted. Values already in the ClickHouse shape pass
    through. Empty/missing becomes ``""``.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1e12:
            epoch = epoch / 1e9
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond:06d}000"
    text = str(value).strip()
    if not text:
        return ""
    if "T" not in text and " " in text:
        return text.replace("Z", "").replace("z", "")
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond:06d}000"


def _duration_ns(span: dict[str, Any]) -> int:
    """Return duration in nanoseconds from an explicit field or start/end times."""
    if span.get("Duration") not in (None, ""):
        try:
            return int(span["Duration"])
        except (TypeError, ValueError):
            pass
    start = span.get("start_time")
    end = span.get("end_time")
    if start in (None, "") or end in (None, ""):
        return 0

    def _to_epoch_ns(value: Any) -> int:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 1e12:
                return int(number)
            return int(number * 1e9)
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1e9)

    delta = _to_epoch_ns(end) - _to_epoch_ns(start)
    return max(delta, 0)


def _sdk_attributes(span: dict[str, Any]) -> dict[str, Any]:
    raw = span.get("attributes")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _sdk_context(span: dict[str, Any]) -> dict[str, Any]:
    raw = span.get("context")
    if isinstance(raw, dict):
        return raw
    return {}


def sdk_span_to_clickhouse(span: dict[str, Any]) -> dict[str, Any]:
    """Mechanically reshape one OTel SDK span dict to a ClickHouse row."""
    context = _sdk_context(span)
    attrs = _sdk_attributes(span)
    resource = span.get("resource")
    service_name = ""
    if isinstance(resource, dict):
        res_attrs = resource.get("attributes")
        if isinstance(res_attrs, dict):
            service_name = str(res_attrs.get("service.name") or "")
    if not service_name:
        service_name = str(attrs.get("service.name") or span.get("ServiceName") or "")
    # application_id is the MAS name; ServiceName is the fallback when empty.
    app_id = str(attrs.get("application_id") or "").strip()
    if app_id:
        service_name = app_id
    parent = (
        span.get("parent_id") or context.get("parent_span_id") or span.get("ParentSpanId") or ""
    )
    return {
        "SpanName": str(span.get("name") or span.get("SpanName") or ""),
        "SpanAttributes": attrs,
        "SpanId": _wire_hex(
            context.get("span_id") or span.get("span_id") or span.get("SpanId") or "",
            16,
        ),
        "TraceId": _wire_hex(
            context.get("trace_id") or span.get("trace_id") or span.get("TraceId") or "",
            32,
        ),
        "ParentSpanId": ("" if parent in (None, "None", "") else _wire_hex(parent, 16)),
        "ServiceName": service_name,
        "Timestamp": _timestamp_to_clickhouse(span.get("start_time") or span.get("Timestamp")),
        "Duration": _duration_ns(span),
    }


def to_clickhouse_spans(spans: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ClickHouse-shaped rows, converting SDK spans when needed."""
    records = [s for s in spans if isinstance(s, dict)]
    if not records:
        return []
    shape = detect_span_shape(records)
    if shape == "clickhouse_row":
        return list(records)
    return [sdk_span_to_clickhouse(span) for span in records]


def normalize_otel(
    spans: Sequence[dict[str, Any]],
    *,
    all_attributes: bool = False,
) -> tuple[list, list]:
    """Normalize OTel spans to ``(nodes, edges)`` via ``norm.normalize()``.

    Uses whatever ``norm`` / ``ioc-scope-ontology`` are installed, as
    published — this package does not patch, subclass, or otherwise work
    around either dependency's own behavior. If ``norm``'s handlers and the
    installed SCOPE models disagree about what fields a model accepts,
    that is a real inconsistency in that dependency pair and is reported
    upstream, not silently papered over here.

    Default ``all_attributes=False`` keeps SCOPE model fields only,
    matching the native path.
    """
    from norm.normalizer import normalize

    from mas.library.kg.core.ontology_align import align_graph
    from mas.library.kg.core.oxp_models import restrict_graph

    clickhouse = to_clickhouse_spans(spans)
    nodes, edges = normalize(clickhouse)
    aligned_nodes, aligned_edges = align_graph(nodes, edges)
    return restrict_graph(aligned_nodes, aligned_edges, all_attributes=all_attributes)

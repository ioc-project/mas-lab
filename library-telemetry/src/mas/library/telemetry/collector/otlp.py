#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Serialize OTel spans to an OTLP collector — the analogue of ``neo4j/push``.

Where ``library-kg`` serializes a KG document to Neo4j, this module serializes an
OTel span set to any OTLP HTTP/JSON collector endpoint (e.g.
``http://localhost:4318``).

Accepts two input shapes — detected automatically:

* **MAS native events** (``kind`` / ``timestamp`` / ``run_id`` keys):
  converted to OTel SDK spans first via
  :func:`mas.library.telemetry.conversion.replay.replay_events_file`
  (requires the ``convert`` extra / ``opentelemetry-sdk``).
* **OTel SDK spans** (``context.trace_id`` / ``start_time`` / ``end_time`` keys):
  already span-shaped; remapped directly to OTLP JSON.

The HTTP push itself uses only the standard library (``urllib``) — no OTel SDK
dependency for pushing pre-built SDK spans.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class OtlpSpan:
    """Minimal OTLP span representation (HTTP JSON schema)."""

    trace_id: str  # 32 hex chars
    span_id: str  # 16 hex chars
    name: str
    start_ns: int  # Unix nanoseconds
    end_ns: int  # Unix nanoseconds, >= start_ns
    kind: int = 1  # 0=UNSPECIFIED 1=INTERNAL 2=SERVER 3=CLIENT 4=PRODUCER 5=CONSUMER
    parent_span_id: Optional[str] = None  # 16 hex chars or None
    attributes: Dict[str, Any] = field(default_factory=dict)
    status_code: int = 0  # 0=UNSET 1=OK 2=ERROR

    def to_otlp_dict(self) -> dict:
        span: dict = {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "name": self.name,
            "kind": self.kind,
            "startTimeUnixNano": str(self.start_ns),
            "endTimeUnixNano": str(self.end_ns),
            "attributes": _attrs_to_otlp(self.attributes),
            "status": {"code": self.status_code},
        }
        if self.parent_span_id:
            span["parentSpanId"] = self.parent_span_id
        return span


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def load_events(path: str | Path) -> List[dict]:
    """Load all non-empty JSON lines from *path*."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def push_spans_to_collector(
    sdk_spans: List[dict],
    endpoint: str,
    *,
    service_name: str = "",
    app_name: str = "",
    dry_run: bool = False,
    batch_size: int = 200,
    shift_to_now: bool = False,
    new_session_id: bool = False,
) -> dict:
    """Push a list of OTel SDK span dicts to an OTLP HTTP collector.

    Returns ``{"spans", "batches", "status", "detail"}``.
    """
    spans = _sdk_spans_to_otlp(sdk_spans)
    if not spans:
        return {"spans": 0, "batches": 0, "status": "ok", "detail": "no spans generated"}
    resource_attrs = _resource_from_sdk(sdk_spans[0]) if sdk_spans else {}
    mas_name = _mas_name(
        app_name=app_name,
        service_name=service_name,
        resource_attrs=resource_attrs,
        spans=spans,
    )
    _stamp_mas_name(spans, resource_attrs, mas_name)
    if shift_to_now:
        _shift_otlp_spans_to_now(spans)
    if new_session_id:
        _rekey_otlp_session_id(spans, mas_name)
    return _push(
        spans, resource_attrs, endpoint, dry_run=dry_run, batch_size=batch_size
    )


def push_file(
    path: str | Path,
    endpoint: str,
    service_name: str = "",
    app_name: str = "",
    dry_run: bool = False,
    batch_size: int = 200,
    shift_to_now: bool = False,
    new_session_id: bool = False,
) -> dict:
    """Convert *path* and push spans to an OTLP HTTP collector at *endpoint*.

    Parameters
    ----------
    app_name:
        MAS name. Required unless the file already carries ``application_id``
        or native events include ``app_name``. Stamped as ``application_id``
        on every span. ``service.name`` is the same MAS: Observe SDK treats
        the MAS, not each agent, as the service that produces telemetry.

    Returns a summary dict::

        {"spans": int, "batches": int, "status": "ok" | "dry-run" | "error", "detail": str}
    """
    events = load_events(path)
    if not events:
        return {"spans": 0, "batches": 0, "status": "ok", "detail": "empty file"}

    first = events[0]

    if _is_sdk_span(first):
        spans = _sdk_spans_to_otlp(events)
        resource_attrs = _resource_from_sdk(first)
        if spans:
            mas_name = _mas_name(
                app_name=app_name,
                service_name=service_name,
                resource_attrs=resource_attrs,
                spans=spans,
            )
            _stamp_mas_name(spans, resource_attrs, mas_name)
            if shift_to_now:
                _shift_otlp_spans_to_now(spans)
            if new_session_id:
                _rekey_otlp_session_id(spans, mas_name)
    else:
        run_id = first.get("run_id", "unknown")
        sdk_spans = _replay_native_to_sdk_spans(
            path,
            service_name,
            app_name,
            shift_to_now=shift_to_now,
            new_session_id=new_session_id,
        )
        spans = _sdk_spans_to_otlp(sdk_spans)
        resource_attrs = _resource_from_sdk(sdk_spans[0]) if sdk_spans else {}
        mas_name = _mas_name(
            app_name=app_name,
            service_name=service_name,
            resource_attrs=resource_attrs,
            spans=spans,
        )
        _stamp_mas_name(spans, resource_attrs, mas_name)
        resource_attrs["mas.run.id"] = run_id
        resource_attrs["mas.source"] = "mas-library-telemetry"

    if not spans:
        return {
            "spans": 0,
            "batches": 0,
            "status": "ok",
            "detail": "no spans generated",
        }

    return _push(
        spans, resource_attrs, endpoint, dry_run=dry_run, batch_size=batch_size
    )


def convert_file_to_otlp_jsonl(
    path: str | Path,
    output: str | Path,
    service_name: str = "",
    app_name: str = "",
) -> dict:
    """Convert *path* to OTLP JSON spans and write to *output* (no push).

    For SDK-span input, each line of *output* is an OTLP ``ResourceSpans`` object.
    For native-event input, *output* is OTel SDK spans JSONL (one span per line),
    which :func:`push_file` accepts.

    Returns ``{"spans": int, "status": "ok" | "error", "detail": str}``.
    """
    head = load_events(path)[:1]
    if not head:
        return {"spans": 0, "status": "ok", "detail": "empty file"}
    first = head[0]

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_sdk_span(first):
        events = load_events(path)
        spans = _sdk_spans_to_otlp(events)
        resource_attrs = _resource_from_sdk(first)
        mas_name = _mas_name(
            app_name=app_name,
            service_name=service_name,
            resource_attrs=resource_attrs,
            spans=spans,
        )
        _stamp_mas_name(spans, resource_attrs, mas_name)
        payload = _build_otlp_payload(spans, resource_attrs)
        out_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return {"spans": len(spans), "status": "ok", "detail": str(out_path)}

    # Native events → OTel SDK spans JSONL (accepted by push_file).
    from mas.library.telemetry.conversion.replay import replay_events_file

    replay_events_file(
        path, out_path, service_name=service_name, app_name=app_name
    )
    span_count = sum(
        1 for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    return {"spans": span_count, "status": "ok", "detail": str(out_path)}


# ---------------------------------------------------------------------------
# Native events → OTel SDK spans (via the library converter)
# ---------------------------------------------------------------------------


def _replay_native_to_sdk_spans(
    path: str | Path,
    service_name: str,
    app_name: str,
    *,
    shift_to_now: bool = False,
    new_session_id: bool = False,
) -> List[dict]:
    from mas.library.telemetry.conversion.replay import replay_events_file

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        replay_events_file(
            path,
            tmp_path,
            service_name=service_name,
            app_name=app_name,
            shift_to_now=shift_to_now,
            new_session_id=new_session_id,
        )
        return load_events(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# OTel SDK span.to_json() → OTLP spans
# ---------------------------------------------------------------------------

_SDK_KIND_MAP = {
    "SpanKind.INTERNAL": 1,
    "SpanKind.SERVER": 2,
    "SpanKind.CLIENT": 3,
    "SpanKind.PRODUCER": 4,
    "SpanKind.CONSUMER": 5,
}
_SDK_STATUS_MAP = {"OK": 1, "ERROR": 2, "UNSET": 0}


def _is_sdk_span(record: dict) -> bool:
    ctx = record.get("context")
    return isinstance(ctx, dict) and "trace_id" in ctx


def _sdk_spans_to_otlp(sdk_spans: List[dict]) -> List[OtlpSpan]:
    """Convert Python OTel SDK ``span.to_json()`` records to :class:`OtlpSpan`."""
    spans: List[OtlpSpan] = []
    for raw in sdk_spans:
        ctx = raw.get("context", {})
        trace_id = _normalise_hex(ctx.get("trace_id", ""), 32)
        span_id = _normalise_hex(ctx.get("span_id", ""), 16)
        if not trace_id or not span_id:
            continue
        parent_id_raw = raw.get("parent_id")
        parent_span_id = _normalise_hex(parent_id_raw, 16) if parent_id_raw else None
        start_ns = _iso_to_ns(raw.get("start_time", ""))
        end_ns = _iso_to_ns(raw.get("end_time", ""))
        spans.append(
            OtlpSpan(
                trace_id=trace_id,
                span_id=span_id,
                name=raw.get("name", "span"),
                start_ns=start_ns,
                end_ns=max(end_ns, start_ns + 1),
                kind=_SDK_KIND_MAP.get(raw.get("kind", ""), 1),
                parent_span_id=parent_span_id,
                attributes=dict(raw.get("attributes") or {}),
                status_code=_SDK_STATUS_MAP.get(
                    (raw.get("status") or {}).get("status_code", "UNSET"), 0
                ),
            )
        )
    spans.sort(key=lambda item: (item.start_ns, item.span_id))
    return spans


def _resource_from_sdk(first_span: dict) -> dict:
    res = first_span.get("resource") or {}
    return dict(res.get("attributes") or {})


def _mas_name(
    *,
    app_name: str,
    service_name: str,
    resource_attrs: dict,
    spans: List[OtlpSpan],
) -> str:
    from mas.library.telemetry.conversion.topology import require_mas_name

    # service_name / resource service.name are OTel process labels, not MAS identity.
    del service_name, resource_attrs
    existing_app = next(
        (
            str(span.attributes.get("application_id") or "").strip()
            for span in spans
            if str(span.attributes.get("application_id") or "").strip()
        ),
        "",
    )
    return require_mas_name(app_name, existing_app)


def _stamp_mas_name(
    spans: List[OtlpSpan],
    resource_attrs: dict,
    mas_name: str,
) -> None:
    """Stamp the MAS name as ``application_id`` and resource ``service.name``.

    Observe SDK treats the MAS as the telemetry-producing service, so
    ``service.name`` is the MAS, not the agent.
    """
    previous = str(resource_attrs.get("service.name") or "")
    resource_attrs["service.name"] = mas_name
    for span in spans:
        span.attributes["application_id"] = mas_name
        for key in ("session.id", "mas.session.id"):
            raw = str(span.attributes.get(key) or "")
            prefix = f"{previous}_"
            if previous and raw.startswith(prefix):
                span.attributes[key] = f"{mas_name}_{raw[len(prefix):]}"


# ---------------------------------------------------------------------------
# OTLP HTTP push
# ---------------------------------------------------------------------------


def _push(
    spans: List[OtlpSpan],
    resource_attrs: dict,
    endpoint: str,
    *,
    dry_run: bool,
    batch_size: int,
) -> dict:
    url = endpoint.rstrip("/") + "/v1/traces"
    total_batches = 0
    errors: List[str] = []
    for i in range(0, len(spans), batch_size):
        chunk = spans[i : i + batch_size]
        payload = _build_otlp_payload(chunk, resource_attrs)
        total_batches += 1
        if not dry_run:
            err = _http_post_json(url, payload)
            if err:
                errors.append(err)
    status = "dry-run" if dry_run else ("error" if errors else "ok")
    detail = "; ".join(errors) if errors else f"{len(spans)} spans → {url}"
    session_id = next(
        (
            str(s.attributes.get("session.id"))
            for s in spans
            if s.attributes.get("session.id")
        ),
        "",
    )
    return {
        "spans": len(spans),
        "batches": total_batches,
        "status": status,
        "detail": detail,
        "session_id": session_id,
        "service_name": str(resource_attrs.get("service.name") or ""),
    }


def _build_otlp_payload(spans: List[OtlpSpan], resource_attrs: dict) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attrs_to_otlp(resource_attrs)},
                "scopeSpans": [
                    {
                        "scope": {"name": "mas-library-telemetry", "version": "1.0"},
                        "spans": [s.to_otlp_dict() for s in spans],
                    }
                ],
            }
        ]
    }


def _http_post_json(url: str, payload: dict) -> Optional[str]:
    """POST JSON to *url*. Returns None on success, an error string on failure."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 204):
                return f"HTTP {resp.status}"
        return None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")[:200]
        return f"HTTP {exc.code}: {body_text}"
    except Exception as exc:  # pragma: no cover - network dependent
        return str(exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_TIME_ATTRS = ("ioa_start_time", "session.started_at", "session.ended_at")


def _shift_otlp_spans_to_now(
    spans: List[OtlpSpan], *, now_ns: int | None = None
) -> None:
    """Translate span times so the earliest start is *now* (relative gaps kept)."""
    dated = [s for s in spans if s.start_ns]
    if not dated:
        return
    min_start = min(s.start_ns for s in dated)
    now_ns = now_ns if now_ns is not None else int(time.time() * 1_000_000_000)
    delta_ns = now_ns - min_start
    if delta_ns == 0:
        return
    delta_s = delta_ns / 1_000_000_000.0
    for span in spans:
        if span.start_ns:
            span.start_ns += delta_ns
        if span.end_ns:
            span.end_ns += delta_ns
        for key in _TIME_ATTRS:
            raw = span.attributes.get(key)
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                span.attributes[key] = float(raw) + delta_s
            elif isinstance(raw, str):
                try:
                    span.attributes[key] = str(float(raw) + delta_s)
                except ValueError:
                    pass


def _rekey_otlp_session_id(
    spans: List[OtlpSpan], application_id: str, new_uuid: str | None = None
) -> str:
    """Stamp a fresh ``{application_id}_{uuid}`` on every span's session.id."""
    from mas.library.telemetry.conversion.semconv import session_id_for

    full = session_id_for(application_id, new_uuid or str(uuid.uuid4()))
    for span in spans:
        span.attributes["session.id"] = full
        if "mas.session.id" in span.attributes:
            span.attributes["mas.session.id"] = full
    return full


def _iso_to_ns(iso: str) -> int:
    """ISO-8601 (``2024-01-01T00:00:00.000000Z``) → Unix nanoseconds."""
    iso = iso.rstrip("Z").replace(" ", "T")
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _normalise_hex(value: str, length: int) -> str:
    """Strip ``0x`` prefix, lowercase, zero-pad to *length* chars."""
    if not value:
        return ""
    h = value.lower()[2:] if value.lower().startswith("0x") else value.lower()
    return h.zfill(length)[:length]


def _attrs_to_otlp(attrs: dict) -> list:
    """Convert a plain dict to OTLP ``[{key, value: {xValue}}]`` format."""
    result = []
    for k, v in attrs.items():
        if isinstance(v, bool):
            val = {"boolValue": v}
        elif isinstance(v, int):
            val = {"intValue": str(v)}  # OTLP JSON uses string for int64
        elif isinstance(v, float):
            val = {"doubleValue": v}
        else:
            val = {"stringValue": str(v)}
        result.append({"key": str(k), "value": val})
    return result


__all__ = [
    "OtlpSpan",
    "load_events",
    "push_spans_to_collector",
    "push_file",
    "convert_file_to_otlp_jsonl",
]

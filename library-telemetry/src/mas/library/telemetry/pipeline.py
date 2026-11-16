#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canonical batch telemetry pipeline — the public entry point.

Mirrors ``library-kg``'s ``pipeline.py`` for the inverse direction:

* ``library-kg``:      OTel spans → events → KG document
* ``library-telemetry``: native events → OTel spans (→ verify → collector)

Start here.  Most callers only need :func:`build_span_set_from_events` /
:func:`convert_events_to_spans_file` and :func:`verify_spans_file`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# events.jsonl loading
# ---------------------------------------------------------------------------


def load_events_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    """Load native events from a JSONL trace file (skips blank / bad lines)."""
    p = Path(path)
    events: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def stream_events_jsonl(path: str | Path) -> Iterator[Dict[str, Any]]:
    """Stream native events from JSONL."""
    yield from load_events_jsonl(path)


# ---------------------------------------------------------------------------
# events → OTel spans
# ---------------------------------------------------------------------------


def convert_events_to_spans_file(
    events_path: str | Path,
    output_path: str | Path,
    *,
    service_name: str = "",
    app_name: str = "",
    export_layers: "Any | None" = None,
    converter_profile: str | None = "observe_sdk",
    realtime: bool = False,
    replay_speed: float = 0.0,
    extensions: bool | None = None,
    shift_to_now: bool = False,
    new_session_id: bool = False,
) -> int:
    """Convert a native ``events.jsonl`` file to an ``otel_sdk_spans.jsonl`` file.

    Thin wrapper over
    :func:`mas.library.telemetry.conversion.replay.replay_events_file`.
    Requires the ``convert`` extra (``opentelemetry-sdk``).

    Returns the number of events processed.
    """
    from mas.library.telemetry.conversion.replay import replay_events_file

    return replay_events_file(
        events_path,
        output_path,
        service_name=service_name,
        app_name=app_name,
        export_layers=export_layers,
        converter_profile=converter_profile,
        realtime=realtime,
        replay_speed=replay_speed,
        extensions=extensions,
        shift_to_now=shift_to_now,
        new_session_id=new_session_id,
    )


def build_span_set_from_events(
    events_path: str | Path,
    *,
    service_name: str = "",
    app_name: str = "",
    export_layers: "Any | None" = None,
):
    """Convert a native ``events.jsonl`` file to an in-memory :class:`OtelSpanSet`.

    Requires the ``convert`` extra (``opentelemetry-sdk``).
    """
    from mas.library.telemetry.artifact import OtelSpanSet

    return OtelSpanSet.from_events(
        events_path,
        service_name=service_name,
        app_name=app_name,
        export_layers=export_layers,
    )


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def verify_spans_file(
    spans_path: str | Path,
    *,
    spanspec_level: str = "L3",
    spanspec_path: str | None = None,
    spanspec_fail_on_error: bool = False,
    spanspec_strictness: str = "required",
) -> Dict[str, Any]:
    """Run structural + JSON-schema + SpanSpec verification on a spans file.

    Thin wrapper over
    :func:`mas.library.telemetry.verification.structural.verify_otel_file`.
    """
    from mas.library.telemetry.verification.structural import verify_otel_file

    return verify_otel_file(
        spans_path,
        spanspec_level=spanspec_level,
        spanspec_path=spanspec_path,
        spanspec_fail_on_error=spanspec_fail_on_error,
        spanspec_strictness=spanspec_strictness,
    )


# ---------------------------------------------------------------------------
# collector serialization
# ---------------------------------------------------------------------------


def push_spans_file(
    spans_path: str | Path,
    endpoint: str,
    *,
    service_name: str = "",
    app_name: str = "",
    dry_run: bool = False,
    shift_to_now: bool = False,
    new_session_id: bool = False,
) -> Dict[str, Any]:
    """Push a spans (or native events) file to an OTLP collector.

    Thin wrapper over :func:`mas.library.telemetry.collector.otlp.push_file`.
    """
    from mas.library.telemetry.collector.otlp import push_file

    return push_file(
        spans_path,
        endpoint,
        service_name=service_name,
        app_name=app_name,
        dry_run=dry_run,
        shift_to_now=shift_to_now,
        new_session_id=new_session_id,
    )


__all__ = [
    "load_events_jsonl",
    "stream_events_jsonl",
    "convert_events_to_spans_file",
    "build_span_set_from_events",
    "verify_spans_file",
    "push_spans_file",
]

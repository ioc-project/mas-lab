#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One stream of envelopes. Backend protocols are adapters that publish or consume frames.

A frame is always ``{topic, ts, ref, source, payload, replay, id}``. Topics are
the dispatch key for plugins (``command.*``, ``native.*``, ``artifact.*``, …).
"""

from __future__ import annotations

from typing import Any

from mas.lab.controller.ui_stream.refs import EventEnvelope, envelope, ref_from_mapping

_SOURCES = {
    "command",
    "result",
    "control",
    "native",
    "telemetry",
    "metric",
    "job",
    "catalog",
    "spec",
    "artifact",
    "io",
    "governance",
}


def parse_frame(data: dict[str, Any] | None) -> EventEnvelope:
    raw = dict(data or {})
    payload = raw.get("payload")
    topic = str(raw.get("topic") or "").strip()
    source = str(raw.get("source") or "").strip()
    if source not in _SOURCES:
        if topic.startswith("command."):
            source = "command"
        elif topic.startswith("result."):
            source = "result"
        elif topic.startswith("native.") or topic.startswith("telemetry."):
            source = "native"
        elif topic.startswith("metric."):
            source = "metric"
        elif topic.startswith("control."):
            source = "control"
        elif topic.startswith("job."):
            source = "job"
        elif topic.startswith("spec."):
            source = "spec"
        elif topic.startswith("io."):
            source = "io"
        elif topic.startswith("governance."):
            source = "governance"
        else:
            source = "artifact"
    if not topic:
        kind = ""
        if isinstance(payload, dict):
            kind = str(payload.get("kind") or payload.get("verb") or source)
        topic = f"{source}.{kind or 'frame'}"
    ref = ref_from_mapping(raw.get("ref") if isinstance(raw.get("ref"), dict) else {})
    if source not in _SOURCES:
        source = "artifact"
    return envelope(
        topic,
        ref,
        source,  # type: ignore[arg-type]
        payload,
        replay=bool(raw.get("replay")),
        event_id=str(raw.get("id") or "") or None,
        ts=str(raw.get("ts") or "") or None,
    )


def is_command_frame(frame: EventEnvelope, raw: dict[str, Any] | None = None) -> bool:
    if frame.source == "command" or frame.topic.startswith("command."):
        return True
    payload = frame.payload if isinstance(frame.payload, dict) else {}
    if payload.get("verb"):
        return True
    return bool(raw and raw.get("verb"))


def command_payload(frame: EventEnvelope, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(frame.payload) if isinstance(frame.payload, dict) else {}
    extra = dict(raw or {})
    for key in ("topic", "source", "ts", "replay", "id", "payload"):
        extra.pop(key, None)
    merged = {**extra, **payload}
    if not merged.get("verb") and frame.topic.startswith("command."):
        merged["verb"] = frame.topic.split(".", 1)[1]
    if not merged.get("ref"):
        merged["ref"] = frame.ref.as_dict()
    return merged

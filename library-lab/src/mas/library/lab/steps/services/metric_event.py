#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Publish ``kind: metric`` records onto EventStream and the pipeline channel."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _as_kpi_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def flatten_mce_document(doc: dict[str, Any] | None) -> dict[str, float]:
    """Flatten ``metrics.json`` session scores onto ``mce.<id>`` and ``<id>``."""
    out: dict[str, float] = {}
    if not isinstance(doc, dict):
        return out
    session = doc.get("session")
    if not isinstance(session, dict):
        return out
    for name, score in session.items():
        if not isinstance(score, dict):
            continue
        metric_id = str(name)
        raw_value = score.get("value")
        if raw_value is None or isinstance(raw_value, bool):
            continue
        numeric = _as_kpi_number(raw_value)
        if numeric is None:
            continue
        out[f"mce.{metric_id}"] = numeric
        out[metric_id] = numeric
    return out


def publish_metric_record(name: str, metrics: dict[str, Any], **extra: Any) -> None:
    """Emit a metric artefact. No-op if EventStream is unavailable."""
    if not metrics:
        return
    try:
        from mas.runtime.boundary.obs.event_stream import emit_to_channel, publish_event
    except Exception:
        return
    record: dict[str, Any] = {
        "kind": "metric",
        "name": name,
        "metrics": dict(metrics),
    }
    for key, value in extra.items():
        if value not in (None, ""):
            record[key] = value
    try:
        publish_event(record)
    except Exception:
        logger.debug("metric publish failed", exc_info=True)
    try:
        emit_to_channel(record)
    except Exception:
        logger.debug("metric channel publish skipped", exc_info=True)

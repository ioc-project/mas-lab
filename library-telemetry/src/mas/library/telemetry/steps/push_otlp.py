#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: serialize spans to an OTLP collector."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def run_push_otlp(
    spans_path: str | Path,
    *,
    endpoint: str | None = None,
    infra: str | Path | None = None,
    service_name: str = "",
    app_name: str = "",
    dry_run: bool = False,
    batch_size: int = 200,
    shift_to_now: bool = False,
    new_session_id: bool = False,
) -> Dict[str, Any]:
    """Push a spans (or native events) file to an OTLP HTTP collector.

    Auto-detects native ``events.jsonl`` vs OTel SDK spans input.  The collector
    endpoint is resolved from *endpoint*, else an *infra* manifest's
    ``OtelCollector`` target, else ``$OTEL_EXPORTER_OTLP_ENDPOINT``.
    """
    from mas.library.telemetry.collector.otlp import push_file
    from mas.library.telemetry.infra import OtelCollectorTarget, resolve_otel_collector

    try:
        target = resolve_otel_collector(infra, endpoint=endpoint)
    except ValueError:
        if not dry_run:
            raise
        target = OtelCollectorTarget(endpoint="http://localhost:4318")
    endpoint = target.endpoint
    service_name = service_name or target.service_name
    app_name = app_name or target.app_name

    spans_path = Path(spans_path).expanduser().resolve()
    result = push_file(
        spans_path,
        endpoint,
        service_name=service_name,
        app_name=app_name,
        dry_run=dry_run,
        batch_size=batch_size,
        shift_to_now=shift_to_now,
        new_session_id=new_session_id,
    )
    logger.info("push_otlp: %s", result.get("detail"))
    return result


__all__ = ["run_push_otlp"]

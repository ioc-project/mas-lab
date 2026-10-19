#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: native ``events.jsonl`` → :class:`OtelSpanSet`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from mas.library.telemetry.artifact import OtelSpanSet

logger = logging.getLogger(__name__)


def run_convert(
    events_path: str | Path,
    *,
    output_dir: Optional[str | Path] = None,
    output_filename: str = "otel_sdk_spans.jsonl",
    service_name: str = "",
    app_name: str = "",
    export_layers: Any | None = None,
    converter_profile: str | None = "observe_sdk",
    dry_run: bool = False,
    realtime: bool = False,
    replay_speed: float = 0.0,
    extensions: bool | None = None,
    shift_to_now: bool = False,
    new_session_id: bool = False,
    rewrite_tool_delegation: bool = True,
) -> OtelSpanSet:
    """Convert ``events.jsonl`` to an :class:`OtelSpanSet`.

    Args:
        events_path: Path to input ``events.jsonl``.
        output_dir: Directory to write the spans JSONL into.  When *None* the
            span set is returned in memory only.
        output_filename: Filename written under *output_dir*.
        service_name: Unused as MAS identity (kept for call-site compatibility).
        app_name: Declared MAS name. Required unless events already carry ``app_name``.
        export_layers: Layer toggles (dict or ``ExportLayers``).
        dry_run: If *True*, convert but do not write output.

    Returns:
        :class:`OtelSpanSet`.  If *output_dir* is given (and not *dry_run*), the
        spans are also saved to ``<output_dir>/<output_filename>``.
    """
    events_path = Path(events_path).expanduser().resolve()
    logger.info("convert: %s  service=%s", events_path, service_name)

    span_set = OtelSpanSet.from_events(
        events_path,
        service_name=service_name,
        app_name=app_name,
        export_layers=export_layers,
        converter_profile=converter_profile,
        realtime=realtime,
        replay_speed=replay_speed,
        extensions=extensions,
        shift_to_now=shift_to_now,
        new_session_id=new_session_id,
        rewrite_tool_delegation=rewrite_tool_delegation,
    )

    if output_dir and not dry_run:
        out = Path(output_dir).expanduser().resolve() / output_filename
        saved = span_set.save(out)
        logger.info("convert: wrote %d spans → %s", span_set.span_count, saved)

    return span_set


__all__ = ["run_convert"]

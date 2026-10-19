#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Offline replay: convert a native ``events.jsonl`` file to OTel SDK spans.

Stands up the shared :func:`create_otel_export` session, feeds events, then
:meth:`OtelExport.close` (same finish path as the live plugin).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from mas.library.telemetry.conversion.layers import ExportLayers
from mas.library.telemetry.conversion.session import create_otel_export

logger = logging.getLogger(__name__)


def _make_seeded_id_generator(seed: str):
    try:
        from opentelemetry.sdk.trace.id_generator import IdGenerator
    except ImportError:  # pragma: no cover
        return None

    class _SeededIdGenerator(IdGenerator):
        def __init__(self, s: str) -> None:
            self._seed = s
            self._n = 0

        def _next(self, nbytes: int) -> int:
            self._n += 1
            digest = hashlib.sha256(f"{self._seed}:{self._n}".encode()).digest()
            val = int.from_bytes(digest[:nbytes], "big")
            return val or 1

        def generate_span_id(self) -> int:
            return self._next(8)

        def generate_trace_id(self) -> int:
            return self._next(16)

    return _SeededIdGenerator(seed)


def _SeededIdGenerator(seed: str):  # noqa: N802
    gen = _make_seeded_id_generator(seed)
    if gen is None:  # pragma: no cover
        raise RuntimeError("opentelemetry-sdk id_generator unavailable")
    return gen


def _event_timestamp_s(event: Dict[str, Any]) -> float | None:
    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        return float(ts)
    return None


def _replay_delay_s(prev_ts: float | None, curr_ts: float | None, speed: float) -> float:
    """Wall delay between two event timestamps at *speed*.

    ``speed <= 0`` means instant (no delay). ``speed == 1`` replays the original
    inter-event gaps; ``speed == 2`` halves them.
    """
    if speed <= 0 or prev_ts is None or curr_ts is None:
        return 0.0
    gap = (curr_ts - prev_ts) / speed
    return gap if gap > 0 else 0.0


def replay_events_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    service_name: str = "",
    app_name: str = "",
    flush_timeout_ms: int = 5000,
    export_layers: "ExportLayers | Dict[str, Any] | None" = None,
    converter_profile: str | None = "observe_sdk",
    write_mapping: bool = False,
    emit_graph: bool | None = None,
    extensions: bool | None = None,
    realtime: bool = False,
    replay_speed: float = 0.0,
    shift_to_now: bool = False,
    new_session_id: bool = False,
    rewrite_tool_delegation: bool = True,
    agent_llm_models: Dict[str, str] | None = None,
) -> int:
    """Replay ``events.jsonl`` to OTel spans via :func:`create_otel_export`."""
    from mas.library.telemetry.conversion.topology import derive_app_name, require_mas_name

    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"events.jsonl not found: {input_path}")

    events = _read_events(input_path)
    effective_app_name = require_mas_name(app_name, derive_app_name(events))

    annotation_enabled: bool | None = None
    if isinstance(export_layers, dict):
        raw_layer_cfg = export_layers.get("export_layers", export_layers)
        if isinstance(raw_layer_cfg, dict) and "annotation" in raw_layer_cfg:
            annotation_enabled = bool(raw_layer_cfg.get("annotation"))

    timestamp_offset_s = 0.0
    if shift_to_now:
        first = next(
            (
                float(event["timestamp"])
                for event in events
                if isinstance(event.get("timestamp"), (int, float))
            ),
            None,
        )
        if first is not None:
            timestamp_offset_s = time.time() - first

    session_uuid = str(uuid.uuid4()) if new_session_id else next(
        (
            str(event.get("session_id") or "").strip()
            for event in events
            if str(event.get("session_id") or "").strip()
            and str(event.get("session_id") or "").strip()
            not in {"local", "unknown"}
        ),
        None,
    )

    # Default replay keeps a path+MAS-name seed so files stay stable. A new
    # session UUID must also reseed span IDs — Neo4j AgentCall/State hashes
    # include span_id, and reused IDs MERGE into older sessions.
    seed_src = f"{input_path.resolve()}:{effective_app_name}"
    if new_session_id and session_uuid:
        seed_src = f"{seed_src}:{session_uuid}"
    session_seed = hashlib.sha256(seed_src.encode()).hexdigest()

    export = create_otel_export(
        spans_path=output_path,
        service_name=effective_app_name,
        app_name=effective_app_name,
        export_layers=export_layers,
        converter_profile=converter_profile,
        realtime=realtime,
        extensions=extensions,
        id_generator=_SeededIdGenerator(session_seed),
        annotation_enabled=annotation_enabled,
        timestamp_offset_s=timestamp_offset_s,
        session_uuid=session_uuid,
        rewrite_tool_delegation=rewrite_tool_delegation,
        agent_llm_models=agent_llm_models,
    )
    # Seeded IDs make replay files stable; they must not leak into session.id
    # so live plugin export and replay stay semantically identical. Likewise
    # agent_llm_models is opt-in only (never auto-discovered from sibling
    # agents/*.yaml here) — the live plugin has no such manifest directory
    # to read, and silently backfilling it only on the replay path made the
    # two diverge on mas.llm.model whenever a native llm_call event doesn't
    # carry its own model name.

    failed_events = 0
    prev_ts: float | None = None
    speed = float(replay_speed or 0.0)
    for event in events:
        curr_ts = _event_timestamp_s(event)
        delay = _replay_delay_s(prev_ts, curr_ts, speed)
        if delay:
            time.sleep(delay)
        if curr_ts is not None:
            prev_ts = curr_ts
        try:
            export.process_event(event)
        except Exception:
            failed_events += 1
            logger.exception(
                "replay_events_file: failed to process event %r; skipping",
                event.get("event_id") or event.get("call_id") or event,
            )
    if failed_events:
        logger.warning(
            "replay_events_file: %d/%d event(s) failed to process and were skipped",
            failed_events,
            len(events),
        )

    export.close(emit_graph=emit_graph, flush_timeout_ms=flush_timeout_ms)

    if write_mapping:
        _write_mapping(
            output_path=Path(output_path),
            app_name=effective_app_name,
            service_name=effective_app_name,
            session_uuid=export.converter._session_uuid,
            run_id=export.converter._run_id,
            input_path=input_path,
        )
    return len(events)


def _read_events(path: Path) -> "list[Dict[str, Any]]":
    events: list[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("replay_events_file: skipping invalid JSON line")
    # Cached traces interleave concurrent agents by write order. Inspect
    # and handoff chaining need causal (timestamp) order so the second
    # moderator visit starts after the specialist ends.
    events.sort(
        key=lambda event: (
            float(event["timestamp"])
            if isinstance(event.get("timestamp"), (int, float))
            else float("inf"),
        )
    )
    return events


def _write_mapping(
    *,
    output_path: Path,
    app_name: str,
    service_name: str,
    session_uuid: str,
    run_id: str,
    input_path: Path,
) -> None:
    from datetime import datetime, timezone

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "converter-replay",
        "application_id": app_name,
        "service_name": service_name,
        "session_uuid": session_uuid,
        "run_id": run_id,
        "input_events": str(input_path),
        "output_spans": str(output_path),
    }
    sidecar = output_path.with_suffix(output_path.suffix + ".mapping.json")
    try:
        sidecar.write_text(
            json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8"
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("could not write mapping sidecar: %s", sidecar, exc_info=True)


__all__ = ["replay_events_file", "_replay_delay_s"]

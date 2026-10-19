#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Filter recorded native events for the demo UI bus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from mas.lab.controller.ui_stream.governance import compact_context_record
from mas.lab.controller.ui_stream.metrics import coerce_timestamp

KEEP_KINDS = frozenset(
    {
        # NOTE: "context_assembled"/"context_usage" are deliberately absent --
        # they carry prompt/message content and are pull-only, demand-driven
        # (see docs/ui/live.md's "Governance / context stream" and
        # ingest.py's NativeIngest._wants_context()). They only reach replay
        # via demo_records(..., extra_kinds=extra_native_kinds(["context"]))
        # when a mounted plugin actually subscribed to governance.context --
        # same gate the live path already enforces. Putting them back here
        # would leak context on every replay regardless of subscription.
        "mas_call_start",
        "mas_call_end",
        "execution_start",
        "execution_end",
        "llm_call_start",
        "llm_call_end",
        "tool_call_start",
        "tool_call_end",
        "user_response",
        "client_response",
        "hitl_gate",
        "hitl_resolve",
        "hitl_request",
        "metric",
        "governance_decision",
        "control.structure",
        "control.context",
        "scope.progress",
    }
)


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def demo_records(
    records: Iterable[dict[str, Any]],
    *,
    extra_kinds: Iterable[str] = (),
) -> Iterator[dict[str, Any]]:
    keep = KEEP_KINDS | frozenset(extra_kinds)
    for record in records:
        kind = str(record.get("kind") or "")
        if kind not in keep:
            continue
        if kind in {"context_assembled", "context_usage"}:
            yield compact_context_record(record)
        else:
            yield record


def metric_record_from_artefact(path: Path) -> dict[str, Any] | None:
    """Turn a cached ``metrics.json`` into a bus ``kind: metric`` record."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    try:
        from mas.library.lab.steps.services.metric_event import flatten_mce_document
    except Exception:
        return None
    metrics = flatten_mce_document(doc)
    if not metrics:
        return None
    record: dict[str, Any] = {"kind": "metric", "name": "eval_mce", "metrics": metrics}
    if doc.get("scenario"):
        record["scenario"] = doc["scenario"]
    return record


def metric_records_from_progress(path: Path) -> list[dict[str, Any]]:
    """Wrap ``progress.jsonl`` rows as ``kind: metric`` records."""
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for record in parse_jsonl(path.read_text(encoding="utf-8")):
        if not isinstance(record, dict):
            continue
        wrapped = {
            "kind": "metric",
            "name": str(record.get("name") or "eval_progress"),
            "metrics": record,
        }
        ts = record.get("snapshot_at") or record.get("timestamp") or record.get("ts")
        if ts:
            wrapped["timestamp"] = ts
        if record.get("scenario"):
            wrapped["scenario"] = record["scenario"]
        out.append(wrapped)
    return out


def custom_panel_records(path: Path | None) -> list[dict[str, Any]]:
    """Read a persisted ``custom.{owner}.jsonl`` sibling trace for replay.

    Each line is already a complete ``kind: custom.{owner}`` record (stamped
    with ``ts`` at write time) -- no transform needed, unlike the
    metric/progress readers above.
    """
    if path is None or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for record in parse_jsonl(path.read_text(encoding="utf-8")):
        if isinstance(record, dict) and str(record.get("kind") or "").startswith("custom."):
            out.append(record)
    return out


def _record_ts(record: dict[str, Any]) -> float:
    return coerce_timestamp(record.get("timestamp", record.get("ts", record.get("snapshot_at")))) or 0.0


def metric_anchor(record: dict[str, Any]) -> tuple[str, str]:
    """Live+cache join uses (kind, agent_id), never call_id or wall clock."""
    return (str(record.get("kind") or ""), str(record.get("agent_id") or ""))


def synthesize_metric_ticks(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay ticks by folding the same trace-stats counters used live."""
    try:
        from mas.library.lab.steps.extract.trace_stats import (
            empty_trace_stats,
            fold_trace_event,
            public_trace_stats,
        )
    except Exception:
        return []
    stats = empty_trace_stats()
    ticks: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    nth: dict[tuple[str, str], int] = {}
    emit_kinds = {"llm_call_end", "tool_call_end", "execution_end", "mas_call_end"}
    for seq, record in enumerate(records or []):
        if not isinstance(record, dict):
            continue
        kind = str(record.get("kind") or "")
        if kind == "metric":
            continue
        fold_trace_event(stats, record)
        pub = public_trace_stats(stats)
        key = metric_anchor(record)
        nth[key] = nth.get(key, 0) + 1
        if kind not in emit_kinds or not pub:
            continue
        if pub == last:
            continue
        last = dict(pub)
        tick: dict[str, Any] = {
            "kind": "metric",
            "name": "trace_stats",
            "metrics": pub,
            "seq": seq,
            "anchor_kind": kind,
            "agent_id": record.get("agent_id"),
            "nth": nth[key],
        }
        ts = record.get("timestamp", record.get("ts"))
        if ts is not None:
            tick["timestamp"] = ts
        if record.get("call_id"):
            tick["call_id"] = record["call_id"]
        if record.get("scenario"):
            tick["scenario"] = record["scenario"]
        ticks.append(tick)
    return ticks


def stamp_artefact_metrics(record: dict[str, Any] | None, events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Put final metrics.json at the end of replay, not at t=0."""
    if not record:
        return None
    if record.get("timestamp") not in (None, ""):
        return record
    last = 0.0
    for item in events or []:
        last = max(last, _record_ts(item))
    if last:
        record = {**record, "timestamp": last}
    return record


def merge_replay_records(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable time-order merge so progress ticks with native events instead of dumping at the end."""
    merged: list[dict[str, Any]] = []
    for group in groups:
        merged.extend(list(group or []))
    indexed = list(enumerate(merged))
    indexed.sort(key=lambda item: (_record_ts(item[1]), item[0]))
    return [item[1] for item in indexed]


def trace_metric_records(
    *,
    metrics: Path | None = None,
    progress: Path | None = None,
    events: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    extra: list[dict[str, Any]] = []
    native = list(events or [])
    authored = metric_records_from_progress(progress) if progress is not None else []
    if authored:
        extra.extend(authored)
    else:
        extra.extend(synthesize_metric_ticks(native))
    if metrics is not None:
        artefact = stamp_artefact_metrics(metric_record_from_artefact(metrics), native)
        if artefact is not None:
            extra.append(artefact)
    return extra

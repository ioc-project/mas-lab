#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live OTel plugin JSON vs replay pipeline JSON on a real native trace.

The trip-planner sample is a native ``events.jsonl`` captured from a run.
Replaying it through the telemetry pipeline must produce the same OTel JSON
as feeding those records through ``OtelObservabilityPlugin`` (the live
exporter), modulo trace/span ids and timestamps.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import requires_otel

_HERE = Path(__file__).resolve()
PACKAGE_ROOT = _HERE.parents[3]  # library-telemetry/
REPO_ROOT = _HERE.parents[4]  # repository root
TRIP_PLANNER_TRACE = REPO_ROOT / "library-samples" / "apps" / "trip-planner" / "traces" / "events.jsonl"
TRIP_PLANNER_EXAMPLE = PACKAGE_ROOT / "examples" / "trip-planner" / "events.jsonl"
QA_AGENT_EXAMPLE = PACKAGE_ROOT / "examples" / "qa-agent" / "events.jsonl"

_VOLATILE_ATTRS = {
    "session.id",
    "mas.call.id",
    "mas.session.id",
    "ioa_observe.agent.span_id",
    "ioa_observe.agent.trace_id",
    "ioa_observe.handoff.source.span_ids",
    "ioa_observe.handoff.source.trace_ids",
}
_VOLATILE_SPAN_KEYS = {
    "context",
    "parent_id",
    "parent_span_id",
    "start_time",
    "end_time",
    "start_time_unix_nano",
    "end_time_unix_nano",
    "resource",
    "instrumentation_scope",
    "instrumentation_info",
    "links",
}


def _load_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _canonical_span(span: dict[str, Any]) -> str:
    attrs = dict(span.get("attributes") or {})
    for key in list(attrs):
        k = str(key)
        if k in _VOLATILE_ATTRS or "span_id" in k or k.endswith("span.id"):
            attrs.pop(key, None)
    body = {
        "name": span.get("name"),
        "kind": span.get("kind"),
        "status": span.get("status"),
        "attributes": {str(k): attrs[k] for k in sorted(attrs, key=str)},
        "events": span.get("events") or [],
    }
    return json.dumps(body, sort_keys=True, default=str, ensure_ascii=True)


def _span_multiset(path: Path) -> Counter[str]:
    spans = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return Counter(_canonical_span(s) for s in spans)


def _write_live_plugin_spans(
    events: list[dict[str, Any]],
    out: Path,
    *,
    service_name: str,
    app_name: str,
) -> None:
    from mas.library.standard.lib.observability.native.transform import TransformContext
    from mas.library.telemetry.plugins.otel_plugin import create_otel_plugin

    plugin = create_otel_plugin(
        spans_path=out,
        context=TransformContext(agent_id="moderator", run_id=""),
        service_name=service_name,
        app_name=app_name,
    )
    assert plugin.converter is not None
    # A real live run feeds boundary crossings in true wall-clock (causal)
    # order. A captured trace's own write order can lag behind that for
    # concurrently-running agents (two threads appending to one file), so
    # replaying raw file order here would simulate a live run that never
    # happens — sort first, the same causal fix replay.py's _read_events
    # already applies before calling process_event.
    for rec in sorted(
        events,
        key=lambda ev: float(ev["timestamp"]) if isinstance(ev.get("timestamp"), (int, float)) else float("inf"),
    ):
        plugin.converter.process_event(rec)
    plugin.close()


def _write_replay_pipeline_spans(
    events_path: Path,
    out: Path,
    *,
    service_name: str,
    app_name: str,
) -> None:
    from mas.library.telemetry.pipeline import convert_events_to_spans_file

    convert_events_to_spans_file(
        events_path,
        out,
        service_name=service_name,
        app_name=app_name,
        converter_profile="observe_sdk",
    )


def _assert_same_otel_json(live_path: Path, replay_path: Path) -> None:
    from mas.library.telemetry.verification.compare import compare_otel_span_files

    report = compare_otel_span_files(
        live_path,
        replay_path,
        reference_label="live-otel-plugin",
        candidate_label="replay-pipeline",
    )
    failed = [c for c in report["checks"] if not c["passed"]]
    assert report["passed"], failed

    live = _span_multiset(live_path)
    replay = _span_multiset(replay_path)
    if live != replay:
        missing = live - replay
        extra = replay - live
        raise AssertionError(
            "canonical OTel JSON differs (ids/timestamps stripped):\n"
            f"  live={sum(live.values())} replay={sum(replay.values())}\n"
            f"  missing_from_replay={sum(missing.values())} extra_in_replay={sum(extra.values())}\n"
            f"  sample_missing={list(missing.elements())[:2]!r}\n"
            f"  sample_extra={list(extra.elements())[:2]!r}"
        )


@pytest.mark.parametrize(
    "events_path,app_name",
    [
        pytest.param(TRIP_PLANNER_TRACE, "trip-planner", id="generated-trip-planner"),
        pytest.param(TRIP_PLANNER_EXAMPLE, "trip-planner", id="example-trip-planner"),
        pytest.param(QA_AGENT_EXAMPLE, "qa-agent", id="example-qa-agent"),
    ],
)
@requires_otel
def test_live_otel_plugin_json_matches_replay_pipeline(tmp_path: Path, events_path: Path, app_name: str) -> None:
    if not events_path.is_file():
        pytest.skip(f"sample trace not present: {events_path}")
    events = _load_events(events_path)
    assert events, f"empty trace: {events_path}"

    service_name = "mas-runtime"
    live_path = tmp_path / "live" / "otel_sdk_spans.jsonl"
    replay_path = tmp_path / "replay" / "otel_sdk_spans.jsonl"
    live_path.parent.mkdir(parents=True)
    replay_path.parent.mkdir(parents=True)

    _write_live_plugin_spans(events, live_path, service_name=service_name, app_name=app_name)
    _write_replay_pipeline_spans(events_path, replay_path, service_name=service_name, app_name=app_name)

    assert live_path.is_file() and live_path.stat().st_size > 0
    assert replay_path.is_file() and replay_path.stat().st_size > 0
    _assert_same_otel_json(live_path, replay_path)

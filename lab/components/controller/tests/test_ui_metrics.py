#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.lab.controller.ui_stream.ingest import NativeIngest
from mas.lab.controller.ui_stream.bus import EventBus
from mas.lab.controller.ui_stream.metrics import MetricsStore, coerce_timestamp
from mas.lab.controller.ui_stream.refs import DataSourceRef


def test_duration_freezes_after_mas_call_end() -> None:
    store = MetricsStore()
    ref = DataSourceRef(session="sess-1")
    store.apply_native(ref, {"kind": "execution_start", "timestamp": 10.0})
    store.apply_native(
        ref,
        {
            "kind": "llm_call_end",
            "timestamp": 12.0,
            "tokens_used": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        },
    )
    store.apply_native(ref, {"kind": "mas_call_end", "timestamp": 13.0})
    assert store.snapshot(ref)["duration_s"] == 3.0
    store.apply_native(ref, {"kind": "tool_call_start", "timestamp": 40.0})
    snap = store.snapshot(ref)
    assert snap["duration_s"] == 3.0
    assert snap["n_tool_calls"] == 1
    assert snap["total_tokens"] == 10


def test_coerce_iso_and_millis_timestamps() -> None:
    from datetime import datetime, timezone

    expected = datetime(2026, 10, 7, 12, tzinfo=timezone.utc).timestamp()
    assert coerce_timestamp("2026-10-07T12:00:00Z") == expected
    assert coerce_timestamp(1_760_000_000_000) == 1_760_000_000.0
    assert coerce_timestamp("12.5") == 12.5


def test_iso_timestamps_drive_duration() -> None:
    store = MetricsStore()
    ref = DataSourceRef(session="sess-2")
    store.apply_native(ref, {"kind": "execution_start", "timestamp": "2026-10-07T12:00:00Z"})
    store.apply_native(ref, {"kind": "llm_call_end", "timestamp": "2026-10-07T12:00:04Z", "tokens_used": {"total_tokens": 9}})
    assert store.snapshot(ref)["duration_s"] == 4.0
    assert store.snapshot(ref)["total_tokens"] == 9


def test_live_duration_uses_wall_clock_when_event_span_is_tiny(monkeypatch) -> None:
    store = MetricsStore()
    ref = DataSourceRef(session="live-1")
    times = iter([1000.0, 1045.0])
    monkeypatch.setattr("mas.lab.controller.ui_stream.metrics.time.time", lambda: next(times))
    store.apply_native(ref, {"kind": "execution_start", "timestamp": 1.0}, wall=True)
    store.apply_native(
        ref,
        {"kind": "llm_call_end", "timestamp": 1.3, "tokens_used": {"total_tokens": 9}},
        wall=True,
    )
    snap = store.snapshot(ref)
    assert snap["duration_s"] == 45.0
    assert snap["elapsed_s"] == 45.0
    assert snap["total_tokens"] == 9


def test_ingest_publishes_metric_snapshot_on_each_llm_call() -> None:
    bus = EventBus()
    ingest = NativeIngest(bus, MetricsStore())
    ref = DataSourceRef(session="s1")
    ingest.ingest(ref, {"kind": "llm_call_end", "timestamp": 1.0, "tokens_used": {"total_tokens": 4}})
    ingest.ingest(ref, {"kind": "llm_call_end", "timestamp": 2.0, "tokens_used": {"total_tokens": 6}})
    snaps = [item for item in bus.snapshot(("metric.snapshot",), ref)]
    assert len(snaps) >= 2
    assert snaps[-1].payload["total_tokens"] == 10
    assert snaps[-1].payload["duration_s"] == 1.0

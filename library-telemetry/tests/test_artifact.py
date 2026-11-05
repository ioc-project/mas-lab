#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OtelSpanSet artifact tests."""

from __future__ import annotations

from mas.library.telemetry import OtelSpanSet

from tests.conftest import requires_otel


def test_from_spans_and_accessors():
    spans = [
        {
            "name": "TaskCall",
            "context": {"span_id": "0", "trace_id": "t"},
            "attributes": {},
        },
        {
            "name": "AgentCall",
            "context": {"span_id": "1", "trace_id": "t"},
            "parent_id": "0",
            "attributes": {},
        },
    ]
    ss = OtelSpanSet.from_spans(spans, run_id="r1")
    assert ss.span_count == 2
    assert ss.trace_ids() == ["t"]
    assert ss.span_names() == {"TaskCall": 1, "AgentCall": 1}
    assert ss.run_id() == "r1"
    assert bool(ss) is True
    assert not OtelSpanSet.empty()


def test_save_and_from_file_roundtrip(tmp_path):
    spans = [
        {
            "name": "TaskCall",
            "context": {"span_id": "0", "trace_id": "t"},
            "attributes": {},
        }
    ]
    p = OtelSpanSet.from_spans(spans).save(tmp_path / "s.jsonl")
    loaded = OtelSpanSet.from_file(p)
    assert loaded.span_count == 1
    assert loaded.span_names() == {"TaskCall": 1}


@requires_otel
def test_from_events(events_path):
    ss = OtelSpanSet.from_events(
        events_path,
        service_name="mas-runtime",
        app_name="app",
        converter_profile="raw",
    )
    assert ss.span_count > 0
    assert ss.metadata["app_name"] == "app"
    report = ss.validate(strictness="required")
    assert report.conformance("L3") in {"full", "passing"}
    assert ss.compare_to(ss)["passed"]  # self-parity
    struct = ss.verify_structural()
    assert struct["ok"], struct["errors"]

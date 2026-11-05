#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""End-to-end pipeline + steps tests."""

from __future__ import annotations

from tests.conftest import requires_otel


@requires_otel
def test_convert_verify_roundtrip(tmp_path, events_path):
    from mas.library.telemetry.pipeline import (
        convert_events_to_spans_file,
        verify_spans_file,
    )

    out = tmp_path / "spans.jsonl"
    n = convert_events_to_spans_file(events_path, out, service_name="mas-runtime", app_name="app")
    assert n == 11  # events in the fixture
    report = verify_spans_file(out, spanspec_level="L3")
    assert report["ok"], report["errors"]
    # Observe-sdk sibling roots: session.start/end, invoke_agent, *.graph.
    assert report["stats"]["root_spans"] >= 3


@requires_otel
def test_replay_parity_is_deterministic(tmp_path, events_path):
    from mas.library.telemetry.steps import run_compare_spans, run_convert

    a = run_convert(events_path, output_dir=tmp_path, output_filename="a.jsonl", app_name="app")
    run_convert(events_path, output_dir=tmp_path, output_filename="b.jsonl", app_name="app")
    report = run_compare_spans(tmp_path / "a.jsonl", tmp_path / "b.jsonl", strict=True)
    assert report["passed"]
    assert report["summary"]["failed"] == 0
    assert a.span_count > 0


@requires_otel
def test_push_dry_run_step(tmp_path, events_path):
    from mas.library.telemetry.steps import run_convert, run_push_otlp

    run_convert(events_path, output_dir=tmp_path, output_filename="s.jsonl", app_name="app")
    result = run_push_otlp(
        tmp_path / "s.jsonl",
        endpoint="http://localhost:4318",
        app_name="app",
        dry_run=True,
    )
    assert result["status"] == "dry-run"
    assert result["spans"] > 0

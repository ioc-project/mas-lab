#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Golden runs of the examples shown in README.md and docs/.

Each test mirrors a code block from the documentation so the docs can never
drift from working code.  The doc source is cited above each test.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import requires_otel

# ── README "Batch functions (start here)" + "Quick start" ────────────────────


@requires_otel
def test_readme_quickstart_from_events(tmp_path, events_path):
    # README §Quick start — "End-to-end: events.jsonl → verified OTel spans"
    from mas.library.telemetry import OtelSpanSet

    spans = OtelSpanSet.from_events(events_path, app_name="my-app")
    out = spans.save(tmp_path / "otel_sdk_spans.jsonl")
    assert out.exists()
    assert spans.span_count > 0
    names = {s.get("name") for s in spans.spans}
    assert any(str(n).endswith(".agent") for n in names)
    assert any(str(n).endswith(".graph") for n in names)


@requires_otel
def test_readme_batch_functions(tmp_path, events_path):
    # README §Quick start — "Batch functions (start here)"
    from mas.library.telemetry.pipeline import (
        convert_events_to_spans_file,
        push_spans_file,
        verify_spans_file,
    )

    out = tmp_path / "spans.jsonl"
    convert_events_to_spans_file(
        events_path, out, app_name="my-app", converter_profile="raw"
    )
    report = verify_spans_file(out, spanspec_level="L3")
    assert report["ok"]
    push = push_spans_file(
        out, "http://localhost:4318", app_name="my-app", dry_run=True
    )
    assert push["status"] == "dry-run"


@requires_otel
def test_readme_convert_only(tmp_path, events_path):
    # README §Quick start — "Convert only (offline replay)"
    from mas.library.telemetry.conversion import replay_events_file

    n = replay_events_file(
        events_path,
        tmp_path / "otel_sdk_spans.jsonl",
        service_name="mas-runtime",
        app_name="my-app",
    )
    assert n > 0


def test_readme_verify_spanspec_only():
    # README §Quick start — "Verify a spans file" (SpanValidator only, pure)
    from mas.library.telemetry import SpanValidator

    span = {
        "name": "AgentCall",
        "context": {"span_id": "s1", "trace_id": "t1"},
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": {
            "application_id": "app",
            "session.id": "s",
            "mas.agent.id": "planner",
            "mas.boundary": "AgentCall",
        },
    }
    report = SpanValidator().validate([span])
    assert report.conformance("L3") in {"full", "passing", "failing"}


@requires_otel
def test_readme_compare_parity(tmp_path, events_path):
    # README §Quick start — "Compare two span sets (parity)"
    from mas.library.telemetry import OtelSpanSet

    ref = OtelSpanSet.from_events(events_path, app_name="x")
    cand = OtelSpanSet.from_events(events_path, app_name="x")
    assert cand.compare_to(ref, strict=True)["passed"]


# ── docs/conversion.md — extending the mapping ───────────────────────────────


def test_docs_conversion_registered_kinds():
    # docs/conversion.md §Extending — introspecting registered handlers
    from mas.library.telemetry.conversion import registered_kinds

    assert len(registered_kinds()) >= 53


@requires_otel
def test_docs_conversion_graph_span(tmp_path):
    # docs/conversion.md — the <app>.graph topology span (SCOPE-required)
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "mas_call_start",
            "call_id": "run",
            "timestamp": 1.0,
            "agent_id": "orch",
            "app_name": "demo",
        },
        {
            "kind": "execution_start",
            "call_id": "a1",
            "parent_call_id": "run",
            "timestamp": 1.1,
            "agent_id": "planner",
        },
        {
            "kind": "routing",
            "timestamp": 1.15,
            "agent_id": "planner",
            "source_agent_id": "planner",
            "target_agent_id": "worker",
        },
        {
            "kind": "execution_end",
            "call_id": "a1",
            "timestamp": 1.2,
            "agent_id": "planner",
        },
        {
            "kind": "mas_call_end",
            "call_id": "run",
            "timestamp": 1.3,
            "agent_id": "orch",
        },
    ]
    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in events))
    op = tmp_path / "s.jsonl"
    replay_events_file(ep, op, service_name="svc")
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    g = next(s for s in spans if s["name"].endswith(".graph"))
    assert g["name"] == "demo.graph"
    assert json.loads(g["attributes"]["gen_ai.ioa.graph"])["nodes"]


# ── docs/otlp-collector.md ───────────────────────────────────────────────────


def test_docs_otlp_convert_file(tmp_path):
    # docs/otlp-collector.md — convert_file_to_otlp_jsonl (SDK-span input, pure)
    from mas.library.telemetry.collector.otlp import convert_file_to_otlp_jsonl

    sdk_span = {
        "name": "AgentCall",
        "context": {"span_id": "0x1", "trace_id": "0x2"},
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": {"mas.boundary": "AgentCall"},
    }
    src = tmp_path / "spans.jsonl"
    src.write_text(json.dumps(sdk_span) + "\n")
    out = tmp_path / "otlp.jsonl"
    result = convert_file_to_otlp_jsonl(src, out, app_name="my-app")
    assert result["status"] == "ok"
    payload = json.loads(out.read_text().splitlines()[0])
    assert "resourceSpans" in payload

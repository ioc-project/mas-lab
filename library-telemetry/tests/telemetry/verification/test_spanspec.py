#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SpanValidator tests — pure (no OTel SDK required)."""

from __future__ import annotations

from mas.library.telemetry.verification import SpanValidator


def _span(name, attrs, span_id="s1", trace_id="t1", parent_id=None):
    return {
        "name": name,
        "context": {"span_id": span_id, "trace_id": trace_id},
        "parent_id": parent_id,
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": attrs,
    }


def test_agentcall_missing_required_is_error():
    v = SpanValidator()
    span = _span("AgentCall", {"application_id": "app", "session.id": "s"})  # no mas.agent.id/boundary
    report = v.validate([span])
    l3_errors = [e for e in report.errors("L3") if e.rule == "missing_required_attr"]
    assert any(e.attribute == "mas.agent.id" for e in l3_errors)
    assert report.conformance("L3") == "failing"


def test_full_agentcall_passes_l3():
    v = SpanValidator()
    span = _span(
        "AgentCall",
        {
            "application_id": "app",
            "session.id": "s",
            "mas.agent.id": "planner",
            "mas.boundary": "AgentCall",
            "mas.input": "hi",
            "mas.status": "success",
            "mas.call.id": "a1",
        },
    )
    report = v.validate([span])
    assert not report.errors("L3")


def test_unknown_span_type_warns():
    v = SpanValidator()
    span = _span("WibbleCall", {"application_id": "app", "session.id": "s"})
    report = v.validate([span])
    assert "WibbleCall" in report.unknown_span_types
    assert any(w.rule == "unknown_span_type" for w in report.warnings())


def test_duplicate_call_id_error():
    v = SpanValidator()
    attrs = {
        "application_id": "app",
        "session.id": "s",
        "mas.agent.id": "p",
        "mas.boundary": "AgentCall",
        "mas.call.id": "dup",
    }
    spans = [
        _span("AgentCall", dict(attrs), span_id="s1"),
        _span("AgentCall", dict(attrs), span_id="s2"),
    ]
    report = v.validate(spans)
    assert any(e.rule == "duplicate_call_id" for e in report.errors())


def test_missing_parent_error():
    v = SpanValidator()
    span = _span(
        "AgentCall",
        {
            "application_id": "app",
            "session.id": "s",
            "mas.agent.id": "p",
            "mas.boundary": "AgentCall",
        },
        parent_id="0xdeadbeef",
    )
    report = v.validate([span])
    assert any(e.rule == "missing_parent_span" for e in report.errors())


def test_strictness_promotes_recommended_to_error():
    v = SpanValidator()
    span = _span(
        "AgentCall",
        {
            "application_id": "app",
            "session.id": "s",
            "mas.agent.id": "planner",
            "mas.boundary": "AgentCall",
        },
    )  # missing recommended mas.input / mas.status
    lenient = v.validate([span], strictness="required")
    strict = v.validate([span], strictness="recommended")
    assert not lenient.errors("L3") or all(e.rule != "missing_recommended_attr" for e in lenient.errors("L3"))
    assert any(e.rule == "missing_recommended_attr" for e in strict.errors("L3"))


def test_graph_span_mandatory():
    # The built-in spec requires a '<app>.graph' span per trace (error if absent).
    v = SpanValidator()
    without_graph = [
        _span(
            "AgentCall",
            {
                "application_id": "app",
                "session.id": "s",
                "mas.agent.id": "planner",
                "mas.boundary": "AgentCall",
            },
        )
    ]
    report = v.validate(without_graph)
    assert any(vi.rule == "graph_span_present" and vi.severity == "error" for vi in report.violations)

    with_graph = without_graph + [
        _span(
            "demo.graph",
            {
                "application_id": "app",
                "session.id": "s",
                "gen_ai.ioa.graph": "{}",
                "ioa_observe.span.kind": "graph",
            },
            span_id="g1",
        )
    ]
    report2 = v.validate(with_graph)
    assert not any(vi.rule == "graph_span_present" for vi in report2.violations)


def test_application_id_mandatory():
    # application_id is required globally at L2 (error) + in the JSON envelope.
    from mas.library.telemetry.verification import validate_span_json_schema

    v = SpanValidator()
    span = _span(
        "AgentCall",
        {"session.id": "s", "mas.agent.id": "p", "mas.boundary": "AgentCall"},
    )
    report = v.validate([span])
    assert any(vi.rule == "global_required_attr" and vi.attribute == "application_id" for vi in report.errors("L2"))
    # JSON schema envelope also rejects a span with no application_id (when jsonschema present).
    errs = validate_span_json_schema(span)
    if errs:  # jsonschema installed
        assert any("application_id" in e for e in errs)


def test_summary_shape():
    v = SpanValidator()
    report = v.validate([_span("AgentCall", {"mas.agent.id": "p", "mas.boundary": "AgentCall"})])
    s = report.summary()
    assert set(s) >= {"spec_file", "total_spans", "conformance", "counts", "violations"}
    assert set(s["conformance"]) == {"L1", "L2", "L3", "L4"}

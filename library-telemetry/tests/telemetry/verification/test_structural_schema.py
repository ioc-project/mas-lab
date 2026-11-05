#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Structural + JSON-schema verification tests — pure (no OTel SDK required)."""

from __future__ import annotations

import pytest
from mas.library.telemetry.verification import (
    validate_spans_json_schema,
    verify_otel_spans,
)

try:
    import jsonschema  # noqa: F401

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False

requires_jsonschema = pytest.mark.skipif(
    not _HAS_JSONSCHEMA,
    reason="jsonschema not installed (envelope validation is best-effort)",
)


def _span(name, sid, tid="t1", parent=None, extra=None):
    a = {"mas.boundary": name, "mas.agent.id": "planner", "application_id": "app"}
    if extra:
        a.update(extra)
    return {
        "name": name,
        "context": {"span_id": sid, "trace_id": tid},
        "parent_id": parent,
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": a,
    }


def test_verify_empty():
    r = verify_otel_spans([])
    assert not r["ok"]
    assert any("file_not_empty" in e for e in r["errors"])


def test_verify_ok():
    spans = [_span("TaskCall", "s0"), _span("AgentCall", "s1", parent="s0")]
    r = verify_otel_spans(spans)
    assert r["ok"], r["errors"]
    assert r["stats"]["root_spans"] == 1
    assert r["stats"]["mas_spans"] == 2


def test_verify_no_root_is_error():
    spans = [_span("AgentCall", "s1", parent="sX")]
    r = verify_otel_spans(spans)
    assert not r["ok"]
    assert any("root_span_present" in e for e in r["errors"])


def test_verify_multiple_traces_warns():
    spans = [_span("TaskCall", "s0", tid="t1"), _span("TaskCall", "s1", tid="t2")]
    r = verify_otel_spans(spans)
    assert any("single_trace" in w for w in r["warnings"])


def test_missing_boundary_warns():
    span = {
        "name": "X",
        "context": {"span_id": "s", "trace_id": "t"},
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": {},
    }
    r = verify_otel_spans([span])
    assert any("mas_boundary_present" in w for w in r["warnings"])


@requires_jsonschema
def test_json_schema_ok():
    spans = [_span("AgentCall", "s1")]
    r = validate_spans_json_schema(spans)
    assert r["ok"], r["span_errors"]


@requires_jsonschema
def test_json_schema_missing_required_field():
    bad = {"name": "AgentCall"}  # no context / timestamps / attributes
    r = validate_spans_json_schema([bad])
    assert not r["ok"]
    assert r["error_count"] > 0

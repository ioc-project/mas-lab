#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OTLP serialization tests — pure (no OTel SDK, no network)."""

from __future__ import annotations

import pytest

from mas.library.telemetry.collector.otlp import (
    OtlpSpan,
    _attrs_to_otlp,
    _iso_to_ns,
    _normalise_hex,
    _sdk_spans_to_otlp,
    push_spans_to_collector,
)


def _sdk_span(name, sid, tid, parent=None):
    return {
        "name": name,
        "context": {"span_id": sid, "trace_id": tid},
        "parent_id": parent,
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "kind": "SpanKind.INTERNAL",
        "status": {"status_code": "OK"},
        "attributes": {"mas.boundary": name, "mas.agent.id": "p"},
        "resource": {"attributes": {"service.name": "svc"}},
    }


def test_normalise_hex():
    assert _normalise_hex("0xABCD", 16) == "000000000000abcd"
    assert _normalise_hex("", 16) == ""


def test_iso_to_ns():
    assert _iso_to_ns("2024-01-01T00:00:00.000000Z") > 0
    assert _iso_to_ns("bogus") == 0


def test_attrs_to_otlp_types():
    out = {
        d["key"]: d["value"]
        for d in _attrs_to_otlp({"s": "x", "i": 3, "f": 1.5, "b": True})
    }
    assert out["s"] == {"stringValue": "x"}
    assert out["i"] == {"intValue": "3"}  # int64 as string
    assert out["f"] == {"doubleValue": 1.5}
    assert out["b"] == {"boolValue": True}


def test_sdk_to_otlp_roundtrip_fields():
    spans = _sdk_spans_to_otlp(
        [_sdk_span("AgentCall", "0xdead", "0xbeef", parent="0xcafe")]
    )
    assert len(spans) == 1
    s = spans[0]
    assert isinstance(s, OtlpSpan)
    assert s.name == "AgentCall"
    assert s.status_code == 1  # OK
    assert s.end_ns >= s.start_ns + 1
    d = s.to_otlp_dict()
    assert d["traceId"] == _normalise_hex("0xbeef", 32)
    assert "parentSpanId" in d


def test_push_dry_run_builds_batches():
    sdk = [_sdk_span("AgentCall", f"0x{i:04x}", "0xbeef") for i in range(5)]
    result = push_spans_to_collector(
        sdk, "http://localhost:4318", app_name="app", dry_run=True, batch_size=2
    )
    assert result["status"] == "dry-run"
    assert result["spans"] == 5
    assert result["batches"] == 3


def test_push_service_name_is_not_mas_identity():
    sdk = [_sdk_span("AgentCall", "0x1", "0xbeef")]
    with pytest.raises(ValueError, match="MAS app name is required"):
        push_spans_to_collector(
            sdk,
            "http://localhost:4318",
            service_name="mas-runtime",
            dry_run=True,
        )


def test_push_stamps_application_id_and_service_name():
    from mas.library.telemetry.collector.otlp import OtlpSpan, _stamp_mas_name

    span = OtlpSpan(
        trace_id="b" * 32,
        span_id="c" * 16,
        name="AgentCall",
        start_ns=1,
        end_ns=2,
        attributes={"mas.boundary": "AgentCall"},
    )
    resource = {"service.name": "travel-planner"}
    _stamp_mas_name([span], resource, "travel-planner")
    assert span.attributes["application_id"] == "travel-planner"
    assert resource["service.name"] == "travel-planner"


def test_push_app_name_overwrites_service_name():
    sdk = [_sdk_span("invoke_agent mas-runtime", "0x1", "0xbeef")]
    sdk[0]["resource"] = {"attributes": {"service.name": "mas-runtime"}}
    sdk[0]["attributes"]["application.id"] = "mas-runtime"
    sdk[0]["attributes"]["session.id"] = "mas-runtime_old-uuid"
    result = push_spans_to_collector(
        sdk,
        "http://localhost:4318",
        service_name="mas-runtime",
        app_name="sample-app",
        dry_run=True,
    )
    assert result["status"] == "dry-run"
    assert result["service_name"] == "sample-app"
    assert result["session_id"] == "sample-app_old-uuid"


def test_push_new_session_id_rewrites_prefixed_uuid():
    sdk = [_sdk_span("session.end", "0x1", "0xbeef")]
    sdk[0]["attributes"]["session.id"] = "app_old-uuid"
    sdk[0]["attributes"]["mas.session.id"] = "app_old-uuid"
    result = push_spans_to_collector(
        sdk,
        "http://localhost:4318",
        app_name="app",
        dry_run=True,
        new_session_id=True,
    )
    assert result["status"] == "dry-run"
    assert result["session_id"].startswith("app_")
    assert result["session_id"] != "app_old-uuid"

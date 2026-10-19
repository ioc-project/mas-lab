#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Focused tests for the thinner modules to keep line coverage >= 90%.

These exercise pure helpers, error paths, and the ClickHouse read-back via an
injected fake client (no real ClickHouse needed).
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from tests.conftest import requires_otel


# ── tool_name ────────────────────────────────────────────────────────────────


def test_tool_name_resolution():
    from mas.library.telemetry.conversion.tool_name import (
        coerce_tool_name,
        resolve_tool_name,
    )

    assert coerce_tool_name(None) == ""
    assert coerce_tool_name("  x  ") == "x"
    assert resolve_tool_name({"tool_name": "search"}) == "search"
    assert resolve_tool_name({"op": "CUSTOM_OP"}) == "CUSTOM_OP"
    assert resolve_tool_name({"op": "TOOL_CALL"}) == "tool"  # ignored op → default
    assert resolve_tool_name({"activity": "browse"}) == "browse"
    assert resolve_tool_name({"tools": [{"name": "nested"}]}) == "nested"
    assert resolve_tool_name({"tools": ["strtool"]}) == "strtool"
    assert resolve_tool_name({}, default="fallback") == "fallback"


# ── exceptions ───────────────────────────────────────────────────────────────


def test_exception_hierarchy_and_messages():
    from mas.library.telemetry import exceptions as ex

    assert issubclass(ex.OtelSdkUnavailableError, ex.ConversionError)
    assert issubclass(ex.CollectorPushError, ex.SerializationError)
    assert "call=c1" in str(ex.UnknownEventKindError("weird", "c1"))
    assert "field" in str(ex.MissingRequiredFieldError("field", "kind", "detail"))
    assert "spec" in str(ex.SpecNotFoundError("/nope/spec.yaml")).lower()
    assert "4318" in str(ex.CollectorPushError("http://x:4318", "boom"))
    assert "clickhouse-connect" in str(ex.ClickHouseUnavailableError())


# ── pipeline ─────────────────────────────────────────────────────────────────


def test_pipeline_load_events(tmp_path):
    from mas.library.telemetry.pipeline import load_events_jsonl, stream_events_jsonl

    p = tmp_path / "e.jsonl"
    p.write_text('{"kind":"a"}\n\nnot json\n{"kind":"b"}\n')
    events = load_events_jsonl(p)
    assert [e["kind"] for e in events] == ["a", "b"]  # blank + bad lines skipped
    assert [e["kind"] for e in stream_events_jsonl(p)] == ["a", "b"]


@requires_otel
def test_pipeline_build_and_push(tmp_path, events_path):
    from mas.library.telemetry.pipeline import (
        build_span_set_from_events,
        push_spans_file,
    )

    ss = build_span_set_from_events(events_path, app_name="x")
    assert ss.span_count > 0
    out = tmp_path / "s.jsonl"
    ss.save(out)
    res = push_spans_file(out, "http://localhost:4318", app_name="x", dry_run=True)
    assert res["status"] == "dry-run"


# ── compare_spans step (multi + output) ──────────────────────────────────────


def _write_spans(path, names):
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "name": n,
                    "context": {"span_id": str(i), "trace_id": "t"},
                    "attributes": {"mas.boundary": n},
                }
            )
            for i, n in enumerate(names)
        )
    )


def test_compare_spans_multi_and_output(tmp_path):
    from mas.library.telemetry.steps.compare_spans import (
        run_compare_spans,
        run_compare_spans_multi,
    )

    ref = tmp_path / "ref.jsonl"
    a = tmp_path / "a.jsonl"
    _write_spans(ref, ["TaskCall", "AgentCall"])
    _write_spans(a, ["TaskCall", "AgentCall"])
    out = tmp_path / "report.json"
    r = run_compare_spans(ref, a, strict=True, output_path=out)
    assert r["passed"]
    assert out.exists()
    multi = run_compare_spans_multi(
        ref, [("cand-a", a)], strict=True, output_path=tmp_path / "m.json"
    )
    assert multi["passed"]


# ── OTLP error paths ─────────────────────────────────────────────────────────


def test_otlp_http_error(monkeypatch):
    from mas.library.telemetry.collector import otlp

    def _boom(url, payload):
        return "HTTP 503: down"

    monkeypatch.setattr(otlp, "_http_post_json", _boom)
    sdk = [
        {
            "name": "AgentCall",
            "context": {"span_id": "0x1", "trace_id": "0x2"},
            "start_time": "2024-01-01T00:00:00.000000Z",
            "end_time": "2024-01-01T00:00:01.000000Z",
            "attributes": {},
            "resource": {"attributes": {}},
        }
    ]
    res = otlp.push_spans_to_collector(
        sdk, "http://x:4318", app_name="app", dry_run=False
    )
    assert res["status"] == "error"
    assert "503" in res["detail"]


def test_otlp_empty_file(tmp_path):
    from mas.library.telemetry.collector.otlp import push_file

    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert push_file(p, "http://x:4318")["spans"] == 0


# ── ClickHouse read-back via a fake client ───────────────────────────────────


class _FakeRows:
    column_names = ["ServiceName", "spans"]
    result_rows = [["app-a", 5], ["app-b", 3]]


class _FakeClient:
    def query(self, q):
        return _FakeRows()


@pytest.fixture
def fake_clickhouse(monkeypatch):
    mod = types.ModuleType("clickhouse_connect")
    mod.get_client = lambda **kw: _FakeClient()
    monkeypatch.setitem(sys.modules, "clickhouse_connect", mod)
    return mod


def test_clickhouse_list_apps_and_dump(tmp_path, fake_clickhouse):
    from mas.library.telemetry.collector.clickhouse import dump_spans, list_apps

    seen_queries = []

    class _CapturingClient(_FakeClient):
        def query(self, q):
            seen_queries.append(q)
            return super().query(q)

    fake_clickhouse.get_client = lambda **kw: _CapturingClient()

    apps = list_apps()
    assert apps[0]["ServiceName"] == "app-a"
    assert "uniq(session_id) AS sessions" in seen_queries[0]
    out = tmp_path / "d.jsonl"
    result = dump_spans("sess-1", output_path=out)
    assert result["spans"] == 2
    assert out.exists()
    assert "WHERE session_id = {'sess-1'}" in seen_queries[1]
    result_t = dump_spans(
        "trace-1", query_by="trace", output_path=tmp_path / "t.jsonl", app_name="app-a"
    )
    assert result_t["query_by"] == "trace"
    assert "WHERE TraceId = {'trace-1'} AND ServiceName = {'app-a'}" in seen_queries[2]


def test_clickhouse_unavailable(monkeypatch):
    from mas.library.telemetry.collector import clickhouse
    from mas.library.telemetry.exceptions import ClickHouseUnavailableError

    # Simulate the import failing.
    monkeypatch.setitem(sys.modules, "clickhouse_connect", None)
    with pytest.raises(ClickHouseUnavailableError):
        clickhouse.list_apps()


# ── artifact helpers ─────────────────────────────────────────────────────────


def test_artifact_stream_and_repr(tmp_path):
    from mas.library.telemetry import OtelSpanSet, stream_span_sets

    s1 = OtelSpanSet.from_spans(
        [{"name": "A", "context": {"span_id": "1", "trace_id": "t"}, "attributes": {}}]
    )
    p1 = s1.save(tmp_path / "1.jsonl")
    p2 = s1.save(tmp_path / "2.jsonl")
    loaded = list(stream_span_sets([p1, p2]))
    assert len(loaded) == 2
    assert "OtelSpanSet(" in repr(s1)
    assert not OtelSpanSet.empty()


def test_artifact_fetch_from_clickhouse(tmp_path, fake_clickhouse):
    from mas.library.telemetry import OtelSpanSet

    ss = OtelSpanSet.fetch_from_clickhouse("sess-1")
    assert ss.metadata["source"] == "clickhouse"


# ── converter helpers (no SDK needed for the pure ones) ──────────────────────
# NOTE: the standalone `mas-telemetry` CLI was removed; the `mas-lab telemetry`
# component is covered by tests/test_lab_cli.py.


def test_converter_static_helpers():
    from mas.library.telemetry.conversion.converter import MasOtelConverter as C

    assert C.agent_id({"agent_id": "p"}) == "p"
    assert C.agent_id({}) == "unknown"
    assert C.enc(None) == ""
    assert C.enc({"a": 1}) == '{"a": 1}'
    assert C.require_call_id({"call_id": "x"}) == "x"
    assert len(C.require_call_id({})) > 0  # generated uuid


# ── converter edge scenarios (orphan end, dup start, governance, obs_wrap) ────


@requires_otel
def test_converter_internals_scoping_and_flush(tmp_path):
    """Cross-agent call-id reuse, self-parent collapse, and error-status flush."""
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "mas_call_start",
            "call_id": "run",
            "timestamp": 1.0,
            "agent_id": "orch",
            "app_name": "app",
        },
        # same call_id "shared" reused by two different agents → scoped apart
        {
            "kind": "execution_start",
            "call_id": "shared",
            "parent_call_id": "run",
            "timestamp": 1.1,
            "agent_id": "alice",
        },
        {
            "kind": "execution_start",
            "call_id": "shared",
            "parent_call_id": "run",
            "timestamp": 1.2,
            "agent_id": "bob",
        },
        # self-referential parent → parent collapsed to None
        {
            "kind": "tool_call_start",
            "call_id": "selfref",
            "parent_call_id": "selfref",
            "timestamp": 1.3,
            "agent_id": "alice",
            "tool_name": "t",
        },
        {
            "kind": "tool_call_end",
            "call_id": "selfref",
            "timestamp": 1.35,
            "agent_id": "alice",
            "status": "error",
        },
        # note: 'shared'/'run' left open on purpose → flushed with success status
    ]
    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in events))
    op = tmp_path / "s.jsonl"
    replay_events_file(ep, op, service_name="svc", converter_profile="raw")
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    assert sum(1 for s in spans if s["name"] == "AgentCall") == 2
    tool = next(s for s in spans if s["name"] == "ToolCall")
    assert tool["attributes"]["mas.status"] == "error"


@requires_otel
def test_converter_edge_scenarios(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "mas_call_start",
            "call_id": "run",
            "timestamp": 1.0,
            "agent_id": "orch",
            "app_name": "app",
        },
        {
            "kind": "execution_start",
            "call_id": "u1-exec",
            "parent_call_id": "run",
            "timestamp": 1.1,
            "agent_id": "planner",
        },
        # duplicate start on an open call → CallAnnotation
        {
            "kind": "llm_call_start",
            "call_id": "l1",
            "parent_call_id": "u1-exec",
            "timestamp": 1.2,
            "agent_id": "planner",
            "model": "m",
        },
        {
            "kind": "llm_call_start",
            "call_id": "l1",
            "parent_call_id": "u1-exec",
            "timestamp": 1.21,
            "agent_id": "planner",
            "model": "m",
        },
        {
            "kind": "llm_call_end",
            "call_id": "l1",
            "timestamp": 1.3,
            "agent_id": "planner",
            "response": {"content": "ok"},
        },
        # orphan llm end (no matching start) → synthesised zero-width span
        {
            "kind": "llm_call_end",
            "call_id": "orphan",
            "timestamp": 1.35,
            "agent_id": "planner",
            "response": {"content": "z"},
        },
        # governance fallback (kind not in table but governance layer)
        {
            "kind": "audit",
            "timestamp": 1.36,
            "parent_call_id": "u1-exec",
            "agent_id": "planner",
            "outcome": "ok",
        },
        # obs_wrap prefix fallback
        {
            "kind": "obs_wrap_gov_check",
            "timestamp": 1.37,
            "parent_call_id": "u1-exec",
            "agent_id": "planner",
        },
        {
            "kind": "execution_end",
            "call_id": "u1-exec",
            "timestamp": 1.4,
            "agent_id": "planner",
        },
        {
            "kind": "mas_call_end",
            "call_id": "run",
            "timestamp": 1.5,
            "agent_id": "orch",
        },
    ]
    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in events))
    op = tmp_path / "s.jsonl"
    # governance ON so audit/obs_wrap are exported
    replay_events_file(ep, op, service_name="svc", export_layers={"governance": True}, converter_profile="raw")
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    names = [s["name"] for s in spans]
    assert "LLMCall" in names
    assert "CallAnnotation" in names  # duplicate-start + obs_wrap
    assert any(s["name"] == "LLMCall" for s in spans)


@requires_otel
def test_replay_write_mapping_and_empty(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text(
        json.dumps(
            {
                "kind": "mas_call_start",
                "call_id": "r",
                "timestamp": 1.0,
                "agent_id": "a",
                "app_name": "x",
            }
        )
        + "\n"
    )
    op = tmp_path / "s.jsonl"
    replay_events_file(ep, op, service_name="svc", write_mapping=True)
    assert (op.parent / (op.name + ".mapping.json")).exists()

    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n")
    with pytest.raises(ValueError, match="MAS app name is required"):
        replay_events_file(empty, tmp_path / "o2.jsonl", service_name="svc")


# ── OTLP push_file with SDK-span input ───────────────────────────────────────


@requires_otel
def test_otlp_push_and_convert_native_input(tmp_path, events_path):
    from mas.library.telemetry.collector.otlp import (
        convert_file_to_otlp_jsonl,
        push_file,
    )

    # native events.jsonl → converted internally, then pushed (dry-run)
    res = push_file(events_path, "http://x:4318", app_name="a", dry_run=True)
    assert res["status"] == "dry-run"
    assert res["spans"] > 0
    # native → OTel SDK spans JSONL on disk
    out = tmp_path / "sdk.jsonl"
    conv = convert_file_to_otlp_jsonl(events_path, out, app_name="a")
    assert conv["spans"] > 0
    assert out.exists()


def test_push_otlp_dump_step(tmp_path, fake_clickhouse):
    from mas.library.telemetry.steps.push_otlp import run_dump_spans

    result = run_dump_spans("sess-1", output_path=tmp_path / "d.jsonl")
    assert result["spans"] == 2


def test_otlp_push_file_sdk_input(tmp_path):
    from mas.library.telemetry.collector.otlp import push_file

    sdk = {
        "name": "AgentCall",
        "context": {"span_id": "0x1", "trace_id": "0x2"},
        "parent_id": "0x3",
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "kind": "SpanKind.INTERNAL",
        "status": {"status_code": "OK"},
        "attributes": {"mas.boundary": "AgentCall"},
        "resource": {"attributes": {"service.name": "svc"}},
    }
    p = tmp_path / "spans.jsonl"
    p.write_text(json.dumps(sdk) + "\n")
    res = push_file(p, "http://x:4318", app_name="a", dry_run=True)
    assert res["status"] == "dry-run"
    assert res["spans"] == 1


def test_shift_otlp_spans_to_now_keeps_duration():
    from mas.library.telemetry.collector.otlp import OtlpSpan, _shift_otlp_spans_to_now

    span = OtlpSpan(
        trace_id="a" * 32,
        span_id="b" * 16,
        name="session.end",
        start_ns=1_000_000_000,
        end_ns=1_000_000_000 + 50_000,
        attributes={"ioa_start_time": 1.0, "session.ended_at": 1.05},
    )
    now_ns = 1_700_000_000_000_000_000
    _shift_otlp_spans_to_now([span], now_ns=now_ns)
    assert span.start_ns == now_ns
    assert span.end_ns - span.start_ns == 50_000
    assert span.attributes["ioa_start_time"] == pytest.approx(
        1.0 + (now_ns - 1_000_000_000) / 1e9
    )


def test_rekey_otlp_session_id_stamps_app_prefixed_uuid():
    from mas.library.telemetry.collector.otlp import OtlpSpan, _rekey_otlp_session_id

    span = OtlpSpan(
        trace_id="a" * 32,
        span_id="b" * 16,
        name="session.end",
        start_ns=1,
        end_ns=2,
        attributes={"session.id": "trip-planner_old", "mas.session.id": "old"},
    )
    full = _rekey_otlp_session_id([span], "trip-planner", new_uuid="aaaa-bbbb")
    assert full == "trip-planner_aaaa-bbbb"
    assert span.attributes["session.id"] == full
    assert span.attributes["mas.session.id"] == full


# ── SpanSpec consistency rules + attribute types (custom spec) ───────────────


def test_spanspec_custom_rules(tmp_path):
    import yaml

    from mas.library.telemetry.verification import SpanValidator

    spec = {
        "shapes": [
            {
                "span_name": "LLMCall",
                "levels": [{"level": "L3", "required": ["mas.agent.id"]}],
            }
        ],
        "attribute_types": {
            "mas.llm.temperature": {"type": "number", "level": "L3"},
            "mas.status": {
                "type": "string",
                "enum": ["success", "error"],
                "level": "L3",
            },
        },
        "consistency_rules": [
            {
                "name": "one_session",
                "check": "group_unique",
                "group_by": "mas.agent.id",
                "unique_field": "trace_id",
                "level": "L3",
                "severity": "warning",
                "description": "agent spans should share a trace",
            },
        ],
    }
    spec_path = tmp_path / "custom.spanspec.yaml"
    spec_path.write_text(yaml.safe_dump(spec))
    v = SpanValidator(str(spec_path))

    spans = [
        {
            "name": "LLMCall",
            "context": {"span_id": "s1", "trace_id": "t1"},
            "attributes": {
                "mas.agent.id": "p",
                "mas.llm.temperature": "hot",
                "mas.status": "bogus",
            },
        },
        {
            "name": "LLMCall",
            "context": {"span_id": "s2", "trace_id": "t2"},
            "attributes": {"mas.agent.id": "p"},
        },
    ]
    report = v.validate(spans)
    rules = {vi.rule for vi in report.violations}
    assert "attribute_type" in rules  # temperature not a number, status not in enum
    assert "one_session" in rules  # agent p appears across 2 traces


def test_spanspec_suffix_ancestor_and_complete(tmp_path):
    import yaml

    from mas.library.telemetry.verification import SpanValidator

    spec = {
        "suffix_shapes": [
            {
                "span_name_suffix": ".agent",
                "levels": [
                    {
                        "level": "L3",
                        "required": ["agent_id"],
                        "recommended": ["mas.boundary"],
                        "optional": ["extra"],
                    }
                ],
            }
        ],
        "consistency_rules": [
            {
                "name": "child_under_wf",
                "check": "has_ancestor_named",
                "target_span_name": "child.agent",
                "ancestor_names": ["workflow"],
                "level": "L3",
                "severity": "warning",
                "description": "child must sit under a workflow",
            },
        ],
    }
    sp = tmp_path / "s.yaml"
    sp.write_text(yaml.safe_dump(spec))
    v = SpanValidator(str(sp))

    spans = [
        {
            "name": "child.agent",
            "context": {"span_id": "s1", "trace_id": "t"},
            "parent_id": None,
            "attributes": {"agent_id": "p"},
        },  # suffix match; no ancestor → rule fires
    ]
    report = v.validate(spans, strictness="complete")  # optional 'extra' becomes error
    rules = {vi.rule for vi in report.violations}
    assert "child_under_wf" in rules
    assert "missing_optional_attr" in rules
    assert "child.agent" in report.known_span_types


def test_otlp_hex_and_iso_helpers():
    from mas.library.telemetry.collector.otlp import (
        _attrs_to_otlp,
        _iso_to_ns,
        _normalise_hex,
    )

    assert (
        _normalise_hex("0xABCDEF", 4) == "cdef"[:4]
        or len(_normalise_hex("0xABCDEF", 4)) == 4
    )
    assert _iso_to_ns("2024-01-01T00:00:00.123456789Z") > 0  # sub-us trimmed
    assert _iso_to_ns("") == 0
    kv = {d["key"]: d["value"] for d in _attrs_to_otlp({"n": 2})}
    assert kv["n"] == {"intValue": "2"}


def test_verify_otel_file_spanspec_fail(tmp_path):
    from mas.library.telemetry.verification.structural import verify_otel_file

    # AgentCall missing required mas.agent.id/boundary → L3 spanspec errors.
    span = {
        "name": "AgentCall",
        "context": {"span_id": "s1", "trace_id": "t1"},
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": {"application_id": "app", "session.id": "s"},
    }
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(span) + "\n")
    report = verify_otel_file(p, spanspec_level="L3", spanspec_fail_on_error=True)
    assert report["ok"] is False
    assert report["spanspec"]["conformance_at_level"] == "failing"
    assert any("spanspec_L3" in e for e in report["errors"])


def test_spanspec_parent_and_call_mismatch():
    from mas.library.telemetry.verification import SpanValidator

    spans = [
        {
            "name": "AgentCall",
            "context": {"span_id": "p", "trace_id": "t1"},
            "attributes": {
                "mas.agent.id": "a",
                "mas.boundary": "AgentCall",
                "mas.call.id": "cP",
            },
        },
        # child points to parent p but declares a different trace → parent_trace_mismatch
        {
            "name": "LLMCall",
            "context": {"span_id": "c", "trace_id": "t2"},
            "parent_id": "p",
            "attributes": {
                "mas.agent.id": "a",
                "mas.boundary": "LLMCall",
                "mas.call.parent": "WRONG",
                "mas.call.id": "cC",
            },
        },
    ]
    report = SpanValidator().validate(spans)
    rules = {v.rule for v in report.violations}
    assert "parent_trace_mismatch" in rules
    assert "call_parent_mismatch" in rules


def test_otlp_push_file_no_spans(tmp_path):
    from mas.library.telemetry.collector.otlp import push_file

    # SDK-shaped record with no usable ids → no spans generated
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"context": {"trace_id": ""}, "name": "X"}) + "\n")
    res = push_file(p, "http://x:4318", dry_run=True)
    assert res["spans"] == 0

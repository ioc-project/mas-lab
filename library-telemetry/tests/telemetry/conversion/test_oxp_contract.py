#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Converter output must satisfy SCOPE norm's required-attribute contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.replay import replay_events_file

pytest.importorskip("opentelemetry.sdk")

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "events.jsonl"


def _attrs(span: dict) -> dict:
    raw = span.get("attributes") or {}
    if isinstance(raw, dict) and "mas.boundary" not in raw and "application_id" not in raw:
        # SDK to_json sometimes nests as {key: {value}}
        out = {}
        for k, v in raw.items():
            if isinstance(v, dict) and "value" in v:
                inner = v["value"]
                if isinstance(inner, dict):
                    out[k] = next(iter(inner.values()), inner)
                else:
                    out[k] = inner
            else:
                out[k] = v
        return out
    return raw


def _name(span: dict) -> str:
    return str(span.get("name") or "")


def _load_spans(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_observe_sdk_profile_has_oxp_required_attributes(tmp_path):
    if not FIXTURE.exists():
        pytest.skip("events fixture missing")
    out = tmp_path / "otel_sdk_spans.jsonl"
    replay_events_file(
        FIXTURE,
        out,
        service_name="trip-planner",
        app_name="trip-planner",
        converter_profile="observe_sdk",
    )
    spans = _load_spans(out)
    assert spans

    agents = [s for s in spans if _name(s).endswith(".agent")]
    chats = [s for s in spans if _name(s).endswith(".chat")]
    tools = [s for s in spans if _name(s).endswith(".tool")]
    graphs = [s for s in spans if _name(s).endswith(".graph")]
    starts = [s for s in spans if _name(s) == semconv.SPAN_NAME_SESSION_START]
    ends = [s for s in spans if _name(s) == semconv.SPAN_NAME_SESSION_END]

    for span in spans:
        attrs = _attrs(span)
        sid = str(attrs.get(semconv.SESSION_ID) or "")
        app = str(attrs.get(semconv.APPLICATION_ID) or "")
        if sid and app:
            assert sid.startswith(f"{app}_"), (sid, app)

    assert agents, "observe_sdk profile must emit *.agent spans"
    assert not any(_name(s) == "root" for s in spans)
    assert not any(_name(s) == "openclaw.request" for s in spans)
    assert any(_name(s).startswith("invoke_agent ") for s in spans)
    for span in agents:
        attrs = _attrs(span)
        assert attrs.get(semconv.AGENT_ID), span
        assert attrs.get(semconv.IOA_ENTITY_NAME), span
        assert attrs.get(semconv.EXECUTION_SUCCESS) is not None, span

    for span in chats:
        attrs = _attrs(span)
        assert attrs.get(semconv.GEN_AI_PROVIDER) or attrs.get(semconv.GEN_AI_REQUEST_MODEL), span
        assert attrs.get(semconv.IOA_AGENT_SPAN_ID), span
        assert attrs.get(semconv.GEN_AI_OPERATION_NAME) == "chat", span

    for span in tools:
        attrs = _attrs(span)
        assert attrs.get(semconv.IOA_ENTITY_NAME), span
        assert attrs.get(semconv.IOA_AGENT_SPAN_ID), span

    assert graphs, "non-realtime replay must emit *.graph"
    for span in graphs:
        attrs = _attrs(span)
        assert attrs.get(semconv.APPLICATION_ID), span
        assert attrs.get(semconv.GEN_AI_IOA_GRAPH), span
        assert attrs.get(semconv.IOA_ENTITY_NAME), span
        payload = json.loads(attrs[semconv.GEN_AI_IOA_GRAPH])
        assert isinstance(payload.get("nodes"), dict), payload.get("nodes")
        assert span.get("parent_id") in (None, "")

    assert len(starts) == 1, "SCOPE session lifecycle requires session.start"
    assert len(ends) == 1, "SCOPE session lifecycle requires session.end"
    assert _attrs(starts[0]).get("session.started_at")
    assert _attrs(ends[0]).get("session.ended_at")
    assert _attrs(starts[0]).get(semconv.SESSION_ID) == _attrs(ends[0]).get(
        semconv.SESSION_ID
    )
    traces = {
        (s.get("context") or {}).get("trace_id")
        for s in (*starts, *ends, *graphs, *agents)
        if (s.get("context") or {}).get("trace_id")
    }
    assert len(traces) == 1, traces


def test_new_session_id_replaces_event_session_id(tmp_path):
    if not FIXTURE.exists():
        pytest.skip("events fixture missing")
    out = tmp_path / "otel_sdk_spans.jsonl"
    replay_events_file(
        FIXTURE,
        out,
        service_name="trip-planner",
        app_name="trip-planner",
        converter_profile="observe_sdk",
        new_session_id=True,
    )
    spans = _load_spans(out)
    sids = {
        str(_attrs(s).get(semconv.SESSION_ID) or "")
        for s in spans
        if _attrs(s).get(semconv.SESSION_ID)
    }
    assert len(sids) == 1
    sid = next(iter(sids))
    assert sid.startswith("trip-planner_")
    assert "sess-1" not in sid


def test_new_session_id_reseeds_span_ids(tmp_path):
    if not FIXTURE.exists():
        pytest.skip("events fixture missing")

    def _span_ids(path):
        return {
            str((span.get("context") or {}).get("span_id") or "")
            for span in _load_spans(path)
            if (span.get("context") or {}).get("span_id")
        }

    stable_a = tmp_path / "stable_a.jsonl"
    stable_b = tmp_path / "stable_b.jsonl"
    isolated = tmp_path / "isolated.jsonl"
    kwargs = dict(
        service_name="trip-planner",
        app_name="trip-planner",
        converter_profile="observe_sdk",
    )
    replay_events_file(FIXTURE, stable_a, **kwargs)
    replay_events_file(FIXTURE, stable_b, **kwargs)
    replay_events_file(FIXTURE, isolated, new_session_id=True, **kwargs)
    assert _span_ids(stable_a) == _span_ids(stable_b)
    assert _span_ids(stable_a)
    assert _span_ids(isolated) != _span_ids(stable_a)


def test_observe_sdk_default_matches_noa_ingest_categories(tmp_path):
    """Default observe_sdk export is the noa-trip-planner ingest subset.

    No governance / context / processing / CallAnnotation spans. Extra
    attributes (``mas.*``, ``session.name``, ``tool_name``, …) stay on the
    wire — ingest silently ignores them. Required ingest keys stay on.
    """
    sample = (
        Path(__file__).resolve().parents[4]
        / "library-samples"
        / "apps"
        / "trip-planner"
        / "traces"
        / "events.jsonl"
    )
    if not sample.exists():
        pytest.skip(f"missing tutorial sample {sample}")
    out = tmp_path / "otel_sdk_spans.jsonl"
    replay_events_file(
        sample,
        out,
        service_name="trip-planner",
        app_name="trip-planner",
        converter_profile="observe_sdk",
    )
    spans = _load_spans(out)
    names = [_name(s) for s in spans]
    suffixes = {n.rsplit(".", 1)[-1] if "." in n else n for n in names}
    assert "governance" not in suffixes
    assert "context" not in suffixes
    assert "processing" not in suffixes
    assert "session.start" in names
    assert "session.end" in names
    assert any(n.endswith(".graph") for n in names)
    assert any(n.startswith("invoke_agent ") for n in names)
    assert any(n.endswith(".agent") for n in names)
    assert any(n.endswith(".chat") for n in names)
    assert any(n.endswith(".tool") for n in names)
    assert not any(n.startswith("delegate_to_") for n in names)
    for span in spans:
        attrs = _attrs(span)
        # application_id is the primary MAS name; service.name is only the
        # fallback when it is empty. Stamp it on every span so the fallback
        # never has to leak ``mas-runtime``.
        assert attrs.get(semconv.APPLICATION_ID), span
        assert attrs.get(semconv.SESSION_ID), span
    schedule = next(s for s in spans if _name(s) == "schedule_agent.agent")
    assert _attrs(schedule).get("ioa_observe.agent.previous") == "moderator"


def test_realtime_replaces_graph_span(tmp_path):
    from mas.library.telemetry.conversion.converter import MasOtelConverter
    from mas.library.telemetry.conversion.exporter import JSONLineFileSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    out = tmp_path / "rt.jsonl"
    exporter = JSONLineFileSpanExporter(out)
    provider = TracerProvider(resource=Resource.create({"service.name": "trip-planner"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    conv = MasOtelConverter(
        provider.get_tracer("t"),
        app_name="trip-planner",
        converter_profile="observe_sdk",
        realtime=True,
    )
    conv.process_event(
        {
            "kind": "execution_start",
            "call_id": "a1",
            "agent_id": "planner",
            "timestamp": 1.0,
        }
    )
    conv.process_event(
        {
            "kind": "execution_end",
            "call_id": "a1",
            "agent_id": "planner",
            "timestamp": 2.0,
        }
    )
    conv.emit_graph_span({"nodes": {"planner": {}}, "edges": []}, app_name="trip-planner")
    conv.flush_open_spans()
    provider.force_flush()
    names = [_name(s) for s in _load_spans(out)]
    assert "topology.node.started" in names
    assert "topology.node.completed" in names
    assert not any(n.endswith(".graph") for n in names)


def test_realtime_tool_signals_carry_entity_name_and_agent_span_id(tmp_path):
    """norm's realtime handlers (handlers/tool.py, handlers/chat.py) drop a
    signal with no ioa_observe.entity.name, and can't link it to its
    AgentCall without ioa_observe.agent.span_id. The *_start event's own
    tool_name/model must survive to the matching *_end signal too, since
    that event never repeats it."""
    from mas.library.telemetry.conversion.converter import MasOtelConverter
    from mas.library.telemetry.conversion.exporter import JSONLineFileSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    out = tmp_path / "rt.jsonl"
    exporter = JSONLineFileSpanExporter(out)
    provider = TracerProvider(resource=Resource.create({"service.name": "trip-planner"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    conv = MasOtelConverter(
        provider.get_tracer("t"),
        app_name="trip-planner",
        converter_profile="observe_sdk",
        realtime=True,
    )
    conv.process_event({"kind": "execution_start", "call_id": "a1", "agent_id": "planner", "timestamp": 1.0})
    conv.process_event(
        {
            "kind": "tool_call_start",
            "call_id": "t1",
            "parent_call_id": "a1",
            "agent_id": "planner",
            "tool_name": "lookup_schedule",
            "timestamp": 1.1,
        }
    )
    conv.process_event({"kind": "tool_call_end", "call_id": "t1", "agent_id": "planner", "timestamp": 1.2})
    conv.process_event({"kind": "execution_end", "call_id": "a1", "agent_id": "planner", "timestamp": 2.0})
    conv.flush_open_spans()
    provider.force_flush()
    signals = {_name(s): _attrs(s) for s in _load_spans(out) if _name(s).startswith("tool.")}
    assert set(signals) == {"tool.started", "tool.completed"}
    for attrs in signals.values():
        assert attrs.get(semconv.IOA_ENTITY_NAME) == "lookup_schedule"
        assert attrs.get(semconv.IOA_AGENT_SPAN_ID)


def test_realtime_lifecycle_pair_shares_one_span_id(tmp_path):
    """A realtime "started"/"completed" (or topology node started/completed)
    pair is two independently start()/end()'d OTel spans, each normally
    getting its own random span id from the SDK's id generator -- with no
    shared id, norm's build_kg has no key to link them as one logical call
    and creates two disconnected nodes instead of one. The active
    id_generator's reuse_span_id() hook (session.py's
    _SharedTraceIdGenerator) must make the "completed" half reuse the
    "started" half's own id."""
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"kind": "mas_call_start", "call_id": "run", "timestamp": 0.0, "agent_id": "orchestrator"},
                {"kind": "execution_start", "call_id": "a1", "parent_call_id": "run", "timestamp": 0.1, "agent_id": "planner"},
                {"kind": "tool_call_start", "call_id": "t1", "parent_call_id": "a1", "agent_id": "planner", "tool_name": "lookup_schedule", "timestamp": 0.2},
                {"kind": "tool_call_end", "call_id": "t1", "agent_id": "planner", "timestamp": 0.3},
                {"kind": "execution_end", "call_id": "a1", "agent_id": "planner", "timestamp": 0.4},
                {"kind": "mas_call_end", "call_id": "run", "timestamp": 0.5, "agent_id": "orchestrator"},
            ]
        )
    )
    out = tmp_path / "rt.jsonl"
    replay_events_file(
        events, out, service_name="svc", app_name="planner", converter_profile="observe_sdk", realtime=True
    )
    spans = _load_spans(out)
    tool_signals = [s for s in spans if _name(s).startswith("tool.")]
    assert {_name(s) for s in tool_signals} == {"tool.started", "tool.completed"}
    span_ids = {s["context"]["span_id"] for s in tool_signals}
    assert len(span_ids) == 1, f"started/completed must share one span id, got {span_ids}"
    topology_signals = [s for s in spans if _name(s).startswith("topology.node.")]
    assert {_name(s) for s in topology_signals} == {
        "topology.node.started",
        "topology.node.completed",
    }
    assert len({s["context"]["span_id"] for s in topology_signals}) == 1

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


def test_observe_sdk_profile_has_scope_required_attributes(tmp_path):
    assert FIXTURE.exists(), FIXTURE
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
    assert _attrs(starts[0]).get(semconv.SESSION_ID) == _attrs(ends[0]).get(semconv.SESSION_ID)
    traces = {
        (s.get("context") or {}).get("trace_id")
        for s in (*starts, *ends, *graphs, *agents)
        if (s.get("context") or {}).get("trace_id")
    }
    assert len(traces) == 1, traces


def test_new_session_id_replaces_event_session_id(tmp_path):
    assert FIXTURE.exists(), FIXTURE
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
    sids = {str(_attrs(s).get(semconv.SESSION_ID) or "") for s in spans if _attrs(s).get(semconv.SESSION_ID)}
    assert len(sids) == 1
    sid = next(iter(sids))
    assert sid.startswith("trip-planner_")
    assert "sess-1" not in sid


def test_new_session_id_reseeds_span_ids(tmp_path):
    assert FIXTURE.exists(), FIXTURE

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


def test_observe_sdk_exports_processing_call_without_extensions(tmp_path):
    """ProcessingCall is a SCOPE core class: observe_sdk emits it by default."""
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "kind": "processing_call_start",
                "call_id": "proc-1",
                "parent_call_id": "agent-1",
                "timestamp": 1.0,
                "agent_id": "planner",
                "processing_name": "context assembly",
                "processing_type": "context_assembly",
            }
        )
        + "\n"
        + json.dumps(
            {
                "kind": "processing_call_end",
                "call_id": "proc-1",
                "timestamp": 1.1,
                "agent_id": "planner",
                "processing_name": "context assembly",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(events, out, app_name="qa-agent", converter_profile="observe_sdk")
    names = [_name(s) for s in _load_spans(out)]
    assert any(n.endswith(".processing") for n in names), names
    proc = next(s for s in _load_spans(out) if _name(s).endswith(".processing"))
    attrs = _attrs(proc)
    assert attrs.get("mas.boundary") == "ProcessingCall"
    assert attrs.get("mas.processing.name") == "context assembly"
    assert attrs.get("mas.processing.messages")


def test_observe_sdk_default_matches_noa_ingest_categories(tmp_path):
    """Default observe_sdk export is the SCOPE ingest subset.

    No governance / context / CallAnnotation spans. ProcessingCall is a
    SCOPE core class, so ``*.processing`` is on. Extra attributes
    (``mas.*``, ``session.name``, ``tool_name``, …) stay on the wire —
    ingest silently ignores them. Required ingest keys stay on.
    """
    sample = (
        Path(__file__).resolve().parents[4] / "library-samples" / "apps" / "trip-planner" / "traces" / "events.jsonl"
    )
    assert sample.exists(), sample
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
    assert "processing" in suffixes
    assert "session.start" in names
    assert "session.end" in names
    assert any(n.endswith(".graph") for n in names)
    assert any(n.startswith("invoke_agent ") for n in names)
    assert any(n.endswith(".agent") for n in names)
    assert any(n.endswith(".chat") for n in names)
    assert any(n.endswith(".tool") for n in names)
    # delegate_to_* is an ordinary tool span of the caller, not a visit split.
    assert "delegate_to_schedule_agent.tool" in names
    for span in spans:
        attrs = _attrs(span)
        # application_id is the primary MAS name; service.name is only the
        # fallback when it is empty. Stamp it on every span so the fallback
        # never has to leak ``mas-runtime``.
        assert attrs.get(semconv.APPLICATION_ID), span
        assert attrs.get(semconv.SESSION_ID), span
    schedule = next(s for s in spans if _name(s) == "schedule_agent.agent")
    assert "ioa_observe.agent.previous" not in _attrs(schedule)


def _realtime_replay(tmp_path: Path, events: list[dict]) -> tuple[list[dict], list[dict]]:
    """(spans, runtime-event LogRecords) of a realtime replay of ``events``."""
    from mas.library.telemetry.conversion.session import runtime_events_path

    src = tmp_path / "events.jsonl"
    src.write_text("\n".join(json.dumps(e) for e in events))
    out = tmp_path / "rt.jsonl"
    replay_events_file(src, out, service_name="svc", app_name="planner", converter_profile="observe_sdk", realtime=True)
    records = [json.loads(line) for line in runtime_events_path(out).read_text().splitlines() if line.strip()]
    return _load_spans(out), records


_REALTIME_RUN = [
    {"kind": "mas_call_start", "call_id": "run", "timestamp": 0.0, "agent_id": "orchestrator", "session_id": "s1"},
    {"kind": "execution_start", "call_id": "a1", "parent_call_id": "run", "timestamp": 0.1, "agent_id": "planner"},
    {
        "kind": "llm_call_start",
        "call_id": "l1",
        "parent_call_id": "a1",
        "agent_id": "planner",
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "plan"}],
        "timestamp": 0.15,
    },
    {"kind": "llm_call_end", "call_id": "l1", "agent_id": "planner", "output": "call it", "timestamp": 0.18},
    {
        "kind": "tool_call_start",
        "call_id": "t1",
        "parent_call_id": "a1",
        "agent_id": "planner",
        "tool_name": "lookup_schedule",
        "arguments": {"day": "mon"},
        "timestamp": 0.2,
    },
    {"kind": "tool_call_end", "call_id": "t1", "agent_id": "planner", "output": "9am", "timestamp": 0.3},
    {"kind": "execution_end", "call_id": "a1", "agent_id": "planner", "timestamp": 0.4},
    {"kind": "mas_call_end", "call_id": "run", "timestamp": 0.5, "agent_id": "orchestrator"},
]


def test_realtime_replaces_graph_span_with_runtime_event_log_records(tmp_path):
    spans, records = _realtime_replay(tmp_path, _REALTIME_RUN)
    assert not any(_name(s).endswith(".graph") for s in spans)
    # Runtime events are LogRecords named like the SDK's own, not spans.
    assert not any(_name(s).startswith(("topology.", "tool.", "llm.")) for s in spans)
    assert [r["event_name"] for r in records] == [
        "topology.session.started",
        "topology.node.started",
        "llm.started",
        "llm.completed",
        "tool.started",
        "tool.completed",
        "topology.node.completed",
        "topology.session.completed",
    ]
    for record in records:
        assert record["body"] == record["event_name"] == record["attributes"]["event.name"]


def test_realtime_events_carry_the_sdk_keys(tmp_path):
    spans, records = _realtime_replay(tmp_path, _REALTIME_RUN)
    by_name = {r["event_name"]: r["attributes"] for r in records}
    session = {name: by_name.pop(name) for name in ("topology.session.started", "topology.session.completed")}
    agent_span = next(s for s in spans if _name(s) == "planner.agent")["context"]["span_id"][2:]
    tool_span = next(s for s in spans if _name(s) == "lookup_schedule.tool")["context"]["span_id"][2:]
    chat_span = next(s for s in spans if _name(s) == "planner.chat")["context"]["span_id"][2:]
    for attrs in by_name.values():
        assert attrs["agent.name"] == "planner"
        assert attrs["application_id"] == "planner"
        assert attrs["session.id"] == "planner_s1"
        assert attrs[semconv.IOA_AGENT_SPAN_ID] == agent_span
        assert len(attrs["trace.id"]) == 32 and len(attrs["event.id"]) == 64
        assert attrs["event.time"].startswith("1970-01-01T00:00:00")
    assert by_name["topology.node.started"]["span.id"] == agent_span
    tool = by_name["tool.completed"]
    assert (tool["tool.name"], tool["tool.type"], tool["tool.output"], tool["tool.status"]) == (
        "lookup_schedule",
        "function",
        "9am",
        "success",
    )
    assert by_name["tool.started"]["tool.input"] == '{"day":"mon"}'
    assert by_name["tool.started"]["span.id"] == by_name["tool.completed"]["span.id"] == tool_span
    llm = by_name["llm.started"]
    assert (llm["llm.name"], llm["gen_ai.provider.name"], llm["llm.call.id"]) == ("gpt-4o", "openai", chat_span)
    assert by_name["llm.completed"]["llm.output"] == "call it"
    # The MAS call's records: its own span, no agent span (it is no agent's).
    invoke = next(s for s in spans if _name(s).startswith("invoke_agent"))["context"]["span_id"][2:]
    for attrs in session.values():
        assert attrs["span.id"] == invoke and semconv.IOA_AGENT_SPAN_ID not in attrs
    # A started record carries the attributes its span was started with.
    assert by_name["topology.node.started"]["mas.call.id"]
    assert by_name["llm.started"]["gen_ai.request.model"] == "gpt-4o"


def test_realtime_records_are_valid_sdk_runtime_events(tmp_path):
    """Each record passes the ioa-observe SDK's own contract and numbers the session's snapshots in order."""
    from ioa_observe.sdk.tracing.runtime_events import identify_runtime_event, validate_runtime_event_attributes

    _spans, records = _realtime_replay(tmp_path, _REALTIME_RUN)
    assert records
    for record in records:
        attrs = record["attributes"]
        validate_runtime_event_attributes(attrs["event.name"], attrs)
        assert identify_runtime_event(attrs) == attrs  # event.id is the SDK's
        assert record["severity_text"] == "INFO"
    versions = [r["attributes"]["snapshot.version"] for r in records]
    assert versions == list(range(1, len(records) + 1))


@pytest.mark.scope_next
def test_norm_realtime_path_ingests_the_runtime_events_unchanged(tmp_path):
    """SCOPE norm reads each LogRecord as it stands (``otel_io_realtime``)."""
    from norm.ioa_observe.otel_io_realtime import realtime_record_to_span

    _spans, records = _realtime_replay(tmp_path, _REALTIME_RUN)
    as_spans = [realtime_record_to_span(r) for r in records]
    tool = [s for s in as_spans if s["SpanName"].startswith("tool.")]
    assert {s["SpanId"] for s in tool} == {tool[0]["SpanId"]}  # started + completed fold into one call
    assert all(s["SpanAttributes"]["ioa_observe.entity.name"] == "lookup_schedule" for s in tool)
    assert all(
        s["SpanAttributes"]["agent_id"] == "planner"
        for s in as_spans
        if not s["SpanName"].startswith("topology.session.")
    )

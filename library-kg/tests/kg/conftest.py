import json

import pytest


@pytest.fixture
def minimal_openclaw_spans():
    """Two spans forming a minimal valid OpenClaw trace (request + agent turn)."""
    return [
        {
            "Timestamp": "1704067200000000000",
            "TraceId": "aaaa",
            "SpanId": "s1",
            "ParentSpanId": "",
            "SpanName": "openclaw.request",
            "SpanAttributes": {
                "mas.call.id": "call-root",
                "mas.agent.id": "gateway",
                "mas.session.id": "sess-1",
            },
            "Duration": 5000000,
        },
        {
            "Timestamp": "1704067200100000000",
            "TraceId": "aaaa",
            "SpanId": "s2",
            "ParentSpanId": "s1",
            "SpanName": "openclaw.agent.turn",
            "SpanAttributes": {
                "mas.call.id": "call-agent",
                "mas.agent.id": "agent-1",
                "mas.session.id": "sess-1",
            },
            "Duration": 3000000,
        },
    ]


@pytest.fixture
def minimal_mas_sdk_spans():
    """Two spans forming a minimal valid MAS SDK trace."""
    return [
        {
            "name": "mas.agent",
            "context": {"trace_id": "bbbb", "span_id": "s1"},
            "parent_id": None,
            "attributes": {
                "mas.boundary": "AgentCall",
                "mas.call.id": "call-root",
                "mas.agent.id": "agent-1",
                "mas.session.id": "sess-1",
                "mas.run.id": "run-1",
            },
            "start_time": "2024-01-01T00:00:00Z",
            "end_time": "2024-01-01T00:01:00Z",
        },
        {
            "name": "mas.tool",
            "context": {"trace_id": "bbbb", "span_id": "s2"},
            "parent_id": "s1",
            "attributes": {
                "mas.boundary": "ToolCall",
                "mas.call.id": "call-tool",
                "mas.agent.id": "agent-1",
                "mas.session.id": "sess-1",
                "mas.run.id": "run-1",
                "mas.tool.name": "search",
            },
            "start_time": "2024-01-01T00:00:10Z",
            "end_time": "2024-01-01T00:00:20Z",
        },
    ]


@pytest.fixture
def trip_planner_events():
    """A representative native events session for the trip-planner app."""
    run_id = "trip-planner_123e4567-e89b-12d3-a456-426614174000"
    return [
        {
            "kind": "execution_start",
            "timestamp": 1704067200.0,
            "run_id": run_id,
            "call_id": "planner-root",
            "agent_id": "planner",
            "trace_id": "trace-trip",
            "span_id": "span-root-1",
            "input": "Plan a 3-day trip to Paris",
        },
        {
            "kind": "execution_end",
            "timestamp": 1704067202.0,
            "run_id": run_id,
            "call_id": "planner-root",
            "agent_id": "planner",
            "trace_id": "trace-trip",
            "span_id": "span-root-1",
            "output": "Draft itinerary with activities and restaurants",
            "status": "success",
        },
        {
            "kind": "execution_start",
            "timestamp": 1704067203.0,
            "run_id": run_id,
            "call_id": "planner-refine",
            "agent_id": "planner",
            "trace_id": "trace-trip",
            "span_id": "span-root-2",
            "input": "Refine itinerary with budget constraints",
        },
        {
            "kind": "execution_end",
            "timestamp": 1704067205.0,
            "run_id": run_id,
            "call_id": "planner-refine",
            "agent_id": "planner",
            "trace_id": "trace-trip",
            "span_id": "span-root-2",
            "output": "Budget-aware final plan",
            "status": "success",
        },
    ]


@pytest.fixture
def trip_planner_events_jsonl(tmp_path, trip_planner_events):
    """Write the trip-planner fixture as events.jsonl and return the path."""
    path = tmp_path / "trip-planner-events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in trip_planner_events), encoding="utf-8")
    return path

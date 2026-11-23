#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unit + fixture tests for observability.native.completeness.
check_native_trace_completeness -- the native-events counterpart of
observability.completeness.check_otel_trace_completeness."""

from __future__ import annotations

import json
from pathlib import Path

from mas.library.kg.observability.native.completeness import check_native_trace_completeness

_FIXTURE = Path(__file__).resolve().parents[2] / "examples" / "trip-planner" / "events.jsonl"


def test_interval_event_covered_by_call_id():
    events = [
        {
            "kind": "execution_start",
            "run_id": "r1",
            "agent_id": "a",
            "call_id": "c1",
            "timestamp": 1.0,
        },
        {
            "kind": "execution_end",
            "run_id": "r1",
            "agent_id": "a",
            "call_id": "c1",
            "timestamp": 2.0,
        },
    ]
    nodes = [{"node_type": "AgentCall", "id": "x", "callId": "c1"}]
    ok, violations = check_native_trace_completeness(events, nodes, "r1")
    assert ok
    assert violations == []


def test_suppressed_kind_passes_without_a_node():
    # KIND_TO_CLASS["prompt_build_start"] is None -- deliberately dropped.
    events = [{"kind": "prompt_build_start", "run_id": "r1", "agent_id": "a", "timestamp": 1.0}]
    ok, violations = check_native_trace_completeness(events, [], "r1")
    assert ok
    assert violations == []


def test_annotation_kind_covered_by_deterministic_id():
    from mas.library.kg.core.graph_builder import _annotation_id

    event = {"kind": "routing", "run_id": "r1", "agent_id": "a", "timestamp": 3.0}
    ann_id = f"ann-{_annotation_id(event, 'r1')}"
    nodes = [{"node_type": "CallAnnotation", "id": ann_id}]
    ok, violations = check_native_trace_completeness([event], nodes, "r1")
    assert ok
    assert violations == []


def test_unknown_kind_is_flagged():
    events = [{"kind": "totally_unknown_kind", "run_id": "r1", "agent_id": "a", "timestamp": 1.23}]
    ok, violations = check_native_trace_completeness(events, [], "r1")
    assert not ok
    assert violations == [{"kind": "totally_unknown_kind", "call_id": None, "timestamp": 1.23}]


def test_mcp_message_is_covered_without_nodes() -> None:
    events = [
        {
            "kind": "transport.message.sent",
            "protocol": "mcp",
            "run_id": "r1",
            "agent_id": "worker",
            "timestamp": 1.0,
            "attributes": {"source": "worker", "target": "echo", "tool": "echo"},
        }
    ]
    ok, violations = check_native_trace_completeness(events, [], "r1")
    assert ok
    assert violations == []


def test_empty_mcp_discovery_is_a_gap() -> None:
    """Mutation: MCP discovery with no agent and no tools is treated as covered."""
    events = [
        {
            "kind": "transport.discovery",
            "protocol": "mcp",
            "run_id": "r1",
            "agent_id": "",
            "timestamp": 1.0,
            "attributes": {"discovery_kind": "tools"},
        }
    ]
    ok, violations = check_native_trace_completeness(events, [], "r1")
    assert not ok
    assert violations[0]["kind"] == "transport.discovery"


def test_empty_non_mcp_message_is_a_gap() -> None:
    """Mutation: a message with no names is treated as covered, hiding a missing A2A identity."""
    events = [
        {
            "kind": "transport.message.sent",
            "protocol": "a2a",
            "run_id": "r1",
            "agent_id": "",
            "timestamp": 1.0,
            "attributes": {},
        }
    ]
    ok, violations = check_native_trace_completeness(events, [], "r1")
    assert not ok
    assert violations[0]["kind"] == "transport.message.sent"


def test_real_trip_planner_fixture_is_complete_across_all_layers():
    """Golden check: every event in a real native-events fixture must be
    accounted for once all optional layers (governance/infrastructure/
    provenance/annotation) are enabled -- confirming the check doesn't false-positive
    on legitimately opt-in-gated event kinds when those layers are on."""
    from mas.library.kg.pipeline import build_kg_document

    events = [json.loads(line) for line in _FIXTURE.read_text().splitlines() if line.strip()]
    run_id = events[0].get("run_id", "trip-planner")
    doc = build_kg_document(
        events,
        run_id=run_id,
        categories="all",
        all_attributes=True,
    )
    ok, violations = check_native_trace_completeness(events, doc["nodes"], run_id)
    assert ok, f"unaccounted events: {violations}"

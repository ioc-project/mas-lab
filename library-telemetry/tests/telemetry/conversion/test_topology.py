#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Topology extraction, app-name derivation, and .graph span tests."""

from __future__ import annotations

import json

import pytest

from mas.library.telemetry.conversion.topology import (
    build_topology,
    declared_mas_name,
    derive_app_name,
    determinism_score,
    graph_span_attributes,
    require_mas_name,
    topology_dynamism,
)
from tests.conftest import requires_otel

_EVENTS = [
    {
        "kind": "mas_call_start",
        "call_id": "run",
        "timestamp": 1000.0,
        "agent_id": "orchestrator",
        "app_name": "travel-planner",
    },
    {
        "kind": "execution_start",
        "call_id": "a1",
        "timestamp": 1000.1,
        "agent_id": "moderator",
    },
    {
        "kind": "routing",
        "timestamp": 1000.15,
        "agent_id": "moderator",
        "source_agent_id": "moderator",
        "target_agent_id": "itinerary_agent",
    },
    {
        "kind": "agent_communication_start",
        "timestamp": 1000.2,
        "agent_id": "moderator",
        "source_agent_id": "moderator",
        "target_agent_id": "budget_agent",
    },
    {
        "kind": "execution_start",
        "call_id": "a2",
        "timestamp": 1000.25,
        "agent_id": "itinerary_agent",
    },
]


# ── topology (pure) ──────────────────────────────────────────────────────────


def test_topology_nodes_and_edges():
    topo = build_topology(_EVENTS)
    # budget_agent appears only as a communication target — still a node.
    assert isinstance(topo["nodes"], dict)
    assert list(topo["nodes"]) == [
        "budget_agent",
        "itinerary_agent",
        "moderator",
        "orchestrator",
    ]
    assert all(n["id"] == key for key, n in topo["nodes"].items())
    edges = {(e["source"], e["target"]): e for e in topo["edges"]}
    assert ("moderator", "itinerary_agent") in edges
    assert ("moderator", "budget_agent") in edges
    assert edges[("moderator", "itinerary_agent")]["kind"] == "routing"
    assert edges[("moderator", "budget_agent")]["kind"] == "communication"


def test_topology_handoff_edge_from_exact_parent_call_id():
    topo = build_topology(
        [
            {
                "kind": "execution_start",
                "call_id": "moderator-u1-exec",
                "agent_id": "moderator",
            },
            {
                "kind": "tool_call_start",
                "call_id": "6ec2bfaa-805b-447c-b6d1-7d01fdb23f81",
                "agent_id": "moderator",
            },
            {
                "kind": "execution_start",
                "call_id": "schedule_agent-6ec2bfaa-exec",
                "agent_id": "schedule_agent",
                "parent_call_id": "6ec2bfaa-805b-447c-b6d1-7d01fdb23f81",
            },
        ]
    )
    assert set(topo["nodes"]) == {"moderator", "schedule_agent"}
    edges = {(e["source"], e["target"]) for e in topo["edges"]}
    assert ("moderator", "schedule_agent") in edges


def test_topology_does_not_infer_handoff_from_call_id_substring():
    topo = build_topology(
        [
            {
                "kind": "tool_call_start",
                "call_id": "moderator-tool",
                "agent_id": "moderator",
            },
            {
                "kind": "execution_start",
                "call_id": "schedule_agent-moderator-tool-exec",
                "agent_id": "schedule_agent",
                "parent_call_id": "moderator-tool",
            },
        ]
    )
    assert ("moderator", "schedule_agent") in {
        (e["source"], e["target"]) for e in topo["edges"]
    }
    topo_no_parent = build_topology(
        [
            {
                "kind": "tool_call_start",
                "call_id": "abc-def",
                "agent_id": "moderator",
            },
            {
                "kind": "execution_start",
                "call_id": "schedule_agent-abc-exec",
                "agent_id": "schedule_agent",
                "parent_call_id": "abc",
            },
        ]
    )
    assert ("moderator", "schedule_agent") not in {
        (e["source"], e["target"]) for e in topo_no_parent["edges"]
    }


def test_topology_is_deterministic():
    assert build_topology(_EVENTS) == build_topology(list(reversed(_EVENTS)))


def test_dynamism_and_determinism():
    topo = build_topology(_EVENTS)
    assert topology_dynamism(topo) > 0.0  # routing edge is conditional
    assert 0.0 <= determinism_score(topo) <= 1.0

    static = {
        "nodes": [{"id": "a"}],
        "edges": [{"source": "a", "target": "b", "conditional": False}],
    }
    assert topology_dynamism(static) == 0.0
    assert determinism_score(static) == 1.0


def test_graph_span_attributes_shape():
    attrs = graph_span_attributes(build_topology(_EVENTS))
    assert attrs["ioa_observe.span.kind"] == "graph"
    assert "gen_ai.ioa.graph" in attrs
    payload = json.loads(attrs["gen_ai.ioa.graph"])
    assert isinstance(payload["nodes"], dict)
    assert payload["nodes"]
    assert attrs["gen_ai.ioa.graph.protocol"] == "MAS"


def test_graph_span_attributes_rejects_list_nodes():
    with pytest.raises(TypeError, match="dict keyed by agent id"):
        graph_span_attributes(
            {"nodes": [{"id": "planner", "name": "planner"}], "edges": []}
        )


def test_derive_app_name():
    assert derive_app_name(_EVENTS, fallback="svc") == "travel-planner"
    assert derive_app_name([{"kind": "x", "mas_id": "trip-planner"}], fallback="svc") == "trip-planner"
    assert derive_app_name([{"kind": "x"}], fallback="svc") == "svc"
    # system_specification name
    assert (
        derive_app_name([{"kind": "system_specification", "name": "spec-app"}], "svc")
        == "spec-app"
    )


def test_require_mas_name_first_nonempty():
    assert require_mas_name("", "travel-planner", "other") == "travel-planner"


def test_require_mas_name_raises_when_missing():
    with pytest.raises(ValueError, match="MAS app name is required"):
        require_mas_name("", "  ", None)


@requires_otel
def test_create_otel_export_service_name_is_not_identity(tmp_path):
    from mas.library.telemetry.conversion.session import create_otel_export

    with pytest.raises(ValueError, match="MAS app name is required"):
        create_otel_export(
            spans_path=tmp_path / "s.jsonl",
            service_name="mas-runtime",
        )


@requires_otel
def test_replay_service_name_is_not_identity(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text(
        json.dumps(
            {
                "kind": "mas_call_start",
                "call_id": "run",
                "timestamp": 1.0,
                "agent_id": "planner",
            }
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="MAS app name is required"):
        replay_events_file(ep, tmp_path / "o.jsonl", service_name="mas-runtime")


def test_topology_fallback_node_from_app_name():
    topo = build_topology(
        [
            {
                "kind": "mas_call_start",
                "call_id": "run",
                "agent_id": "mas",
                "app_name": "qa-agent",
            },
            {
                "kind": "execution_start",
                "call_id": "a1",
                "agent_id": "agent",
                "app_name": "qa-agent",
            },
        ]
    )
    assert set(topo["nodes"]) == {"qa-agent"}


def test_declared_mas_name_rejects_filesystem_path():
    assert declared_mas_name("trip-planner") == "trip-planner"
    with pytest.raises(ValueError, match="filesystem path"):
        declared_mas_name("/opt/mas-lab/library-samples/apps/trip-planner/agents")
    with pytest.raises(ValueError, match="filesystem path"):
        require_mas_name("/opt/mas-lab/library-samples/apps/trip-planner/agents.yaml")


# ── graph span through replay (golden, needs OTel SDK) ───────────────────────


@requires_otel
def test_replay_emits_graph_span(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    op = tmp_path / "s.jsonl"
    replay_events_file(ep, op, service_name="mas-runtime", converter_profile="raw")  # app_name derived
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]

    graph = [s for s in spans if s["name"].endswith(".graph")]
    assert len(graph) == 1
    g = graph[0]
    assert g["name"] == "travel-planner.graph"
    assert g["attributes"]["ioa_observe.span.kind"] == "graph"
    assert g["attributes"]["mas.boundary"] == "Graph"
    topo = json.loads(g["attributes"]["gen_ai.ioa.graph"])
    nodes = topo["nodes"]
    assert isinstance(nodes, dict)
    node_ids = set(nodes)
    assert node_ids == {
        "orchestrator",
        "moderator",
        "itinerary_agent",
        "budget_agent",
    }
    # app-name bug fixed: every span carries the real app id, not "mas-runtime"
    assert all(s["attributes"].get("application_id") == "travel-planner" for s in spans)
    assert all(
        (s.get("resource") or {}).get("attributes", {}).get("service.name")
        == "travel-planner"
        for s in spans
    )
    # graph span shares the run trace (child of the synthetic root).
    root = next(s for s in spans if s["name"] == "root")
    assert g["context"]["trace_id"] == root["context"]["trace_id"]
    assert g["parent_id"] == root["context"]["span_id"]


@requires_otel
def test_explicit_app_name_overrides(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    op = tmp_path / "s.jsonl"
    replay_events_file(ep, op, service_name="svc", app_name="explicit-override")
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    # application_id is the primary MAS name (SCOPE/norm); service.name is
    # only the fallback when that attribute is empty. Both must be the
    # app name, not leftover ``service_name`` / ``mas-runtime``.
    assert all(
        s["attributes"].get("application_id") == "explicit-override" for s in spans
    )
    session_start = next(s for s in spans if s["name"] == "session.start")
    assert session_start["attributes"].get("application.id") == "explicit-override"
    assert all(
        (s.get("resource") or {}).get("attributes", {}).get("service.name")
        == "explicit-override"
        for s in spans
    )


@requires_otel
def test_push_native_app_name_overwrites_service_name(tmp_path):
    from mas.library.telemetry.collector.otlp import push_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    result = push_file(
        ep,
        "http://localhost:4318",
        service_name="mas-runtime",
        app_name="sample-app",
        dry_run=True,
    )
    assert result["status"] == "dry-run"
    assert result["service_name"] == "sample-app"


@requires_otel
def test_push_native_derives_app_name_when_unset(tmp_path):
    from mas.library.telemetry.collector.otlp import push_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    result = push_file(
        ep,
        "http://localhost:4318",
        service_name="mas-runtime",
        dry_run=True,
    )
    assert result["status"] == "dry-run"
    assert result["service_name"] == "travel-planner"


@requires_otel
def test_emit_graph_false_suppresses(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    op = tmp_path / "s.jsonl"
    replay_events_file(ep, op, service_name="svc", emit_graph=False)
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    assert not any(s["name"].endswith(".graph") for s in spans)


@requires_otel
def test_path_app_name_is_rejected(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    op = tmp_path / "s.jsonl"
    with pytest.raises(ValueError, match="filesystem path"):
        replay_events_file(
            ep,
            op,
            app_name="/opt/mas-lab/library-samples/apps/trip-planner/agents",
            converter_profile="observe_sdk",
        )


@requires_otel
def test_session_end_emitted_on_mas_call_end(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = list(_EVENTS) + [
        {
            "kind": "mas_call_end",
            "call_id": "run",
            "timestamp": 1000.9,
            "agent_id": "orchestrator",
            "status": "success",
        }
    ]
    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in events))
    op = tmp_path / "s.jsonl"
    replay_events_file(
        ep, op, app_name="travel-planner", converter_profile="observe_sdk"
    )
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    names = [s["name"] for s in spans]
    assert names.count("session.start") == 1
    assert names.count("session.end") == 1
    assert any(n.endswith(".graph") for n in names)
    # graph before session.end on the known-end path
    assert names.index(next(n for n in names if n.endswith(".graph"))) < names.index(
        "session.end"
    )


@requires_otel
def test_session_end_fallback_without_mas_call_end(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in _EVENTS))
    op = tmp_path / "s.jsonl"
    replay_events_file(
        ep, op, app_name="travel-planner", converter_profile="observe_sdk"
    )
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    names = [s["name"] for s in spans]
    assert names.count("session.end") == 1


@requires_otel
def test_finish_does_not_duplicate_session_end(tmp_path):
    from mas.library.telemetry.conversion.session import create_otel_export

    export = create_otel_export(
        spans_path=tmp_path / "s.jsonl",
        app_name="travel-planner",
        converter_profile="observe_sdk",
    )
    for event in _EVENTS:
        export.converter.process_event(event)
    export.converter.process_event(
        {
            "kind": "mas_call_end",
            "call_id": "run",
            "timestamp": 1000.9,
            "agent_id": "orchestrator",
        }
    )
    export.finish()
    export.finish()
    export.close()
    spans = [
        json.loads(line)
        for line in (tmp_path / "s.jsonl").read_text().splitlines()
        if line.strip()
    ]
    names = [s["name"] for s in spans]
    assert names.count("session.end") == 1

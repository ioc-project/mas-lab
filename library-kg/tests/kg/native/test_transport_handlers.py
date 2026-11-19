#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native transport handlers: own-fields, order-independent, idempotent."""

from __future__ import annotations

import json
from pathlib import Path

from mas.library.kg.pipeline import build_kg_document

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transport"


def _graph(events: list[dict], *, categories: str | None = None):
    doc = build_kg_document(events, run_id="r1", categories=categories)
    nodes = sorted(
        ({k: v for k, v in n.items() if k != "masUri"} for n in doc["nodes"]),
        key=lambda n: (n.get("node_type") or "", n.get("id") or ""),
    )
    edges = sorted(
        doc["edges"],
        key=lambda e: (e.get("edge_type") or "", e.get("from_id") or "", e.get("to_id") or ""),
    )
    return nodes, edges


def _message(kind: str, *, source: str, target: str, ts: float = 1.0) -> dict:
    return {
        "kind": kind,
        "timestamp": ts,
        "run_id": "r1",
        "agent_id": source,
        "mas_id": "demo",
        "protocol": "example",
        "side": "client",
        "correlation_key": "k",
        "source": "hook",
        "attributes": {"source": source, "target": target},
    }


def test_message_declares_two_agents() -> None:
    nodes, edges = _graph(
        [_message("transport.message.sent", source="planner", target="researcher")]
    )
    agent_ids = {n["id"] for n in nodes if n.get("node_type") == "Agent"}
    assert agent_ids == {"planner", "researcher"}
    mas_edges = [e for e in edges if e.get("edge_type") == "belongsToMAS"]
    assert len(mas_edges) == 2
    assert not any(e.get("edge_type") == "callsAgent" for e in edges)


def test_scope_kinds_fold_by_default() -> None:
    nodes, _edges = _graph(
        [_message("transport.message.sent", source="planner", target="researcher")],
        categories="",
    )
    agent_ids = {n["id"] for n in nodes if n.get("node_type") == "Agent"}
    assert agent_ids == {"planner", "researcher"}


def test_order_and_duplicate() -> None:
    sent = _message("transport.message.sent", source="planner", target="researcher", ts=1.0)
    received = _message("transport.message.received", source="planner", target="researcher", ts=2.0)
    received["side"] = "server"
    a_nodes, a_edges = _graph([sent, received])
    b_nodes, b_edges = _graph([received, sent])
    c_nodes, c_edges = _graph([sent, received, sent, received])
    assert a_nodes == b_nodes == c_nodes
    assert a_edges == b_edges == c_edges


def test_discovery_tools() -> None:
    event = {
        "kind": "transport.discovery",
        "timestamp": 1.0,
        "run_id": "r1",
        "agent_id": "worker",
        "mas_id": "demo",
        "protocol": "example",
        "side": "client",
        "correlation_key": "tools:worker",
        "source": "hook",
        "attributes": {
            "discovery_kind": "tools",
            "tools": {"tools": [{"name": "echo", "description": "ping"}]},
            "source": "worker",
        },
    }
    nodes, edges = _graph([event])
    tools = [n for n in nodes if n.get("node_type") == "Tool"]
    assert any(n.get("name") == "echo" for n in tools)
    # The listing agent is not declared by this event (as in norm), so its edge is dropped...
    assert not [n for n in nodes if n.get("node_type") == "Agent"]
    assert not any(e.get("edge_type") == "usesTool" for e in edges)
    # ...and it stays once another event declares the agent.
    card = {**event, "attributes": {"discovery_kind": "agent_card", "card": {"name": "worker"}}}
    _, edges = _graph([event, card])
    assert any(e.get("edge_type") == "usesTool" for e in edges)


def test_session_internal_error_drop() -> None:
    events = [
        {
            "kind": kind,
            "timestamp": 1.0,
            "run_id": "r1",
            "agent_id": "x",
            "protocol": "example",
            "side": "client",
            "correlation_key": "k",
            "source": "hook",
            "attributes": {},
        }
        for kind in ("transport.session", "transport.internal", "transport.error")
    ]
    nodes, edges = _graph(events)
    assert [n for n in nodes if n.get("node_type") == "Agent" and n.get("id") == "x"] == []


def test_golden_files_match() -> None:
    paths = sorted(FIXTURES.glob("*.json")) if FIXTURES.exists() else []
    if not paths:
        return
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        nodes, edges = _graph(payload["events"])
        assert nodes == payload["nodes"]
        assert edges == payload["edges"]


def _discovery(**attrs) -> dict:
    return {
        "kind": "transport.discovery",
        "timestamp": 1.0,
        "run_id": "r1",
        "agent_id": "listener",
        "mas_id": "demo",
        "protocol": "example",
        "side": "client",
        "correlation_key": "d",
        "source": "hook",
        "attributes": attrs,
    }


def _agents(nodes: list[dict]) -> set[str]:
    return {n["id"] for n in nodes if n.get("node_type") == "Agent"}


def test_card_without_a_name_declares_nothing() -> None:
    """Mutation: fall back to the event's agent id when the card has no name."""
    nodes, _ = _graph([_discovery(discovery_kind="agent_card", card={"description": "no name"})])
    assert _agents(nodes) == set()
    nodes, _ = _graph([_discovery(discovery_kind="agent_card", card="{not json")])
    assert _agents(nodes) == set()


def test_discovery_without_a_kind_declares_nothing() -> None:
    """Mutation: treat an unlabelled discovery as a tool list."""
    nodes, _ = _graph([_discovery(tools={"tools": [{"name": "echo"}]})])
    assert _agents(nodes) == set()
    assert not [n for n in nodes if n.get("node_type") == "Tool"]


def test_non_text_agent_names_are_not_agents() -> None:
    """Mutation: str() every source and target, so the number 5 becomes Agent '5'."""
    nodes, _ = _graph([_message("transport.message.sent", source="planner", target=5)])  # type: ignore[arg-type]
    assert _agents(nodes) == {"planner"}


def test_mcp_message_declares_no_agents() -> None:
    """Mutation: MCP tools/call is treated like A2A, so source/target become Agents."""
    event = _message("transport.message.sent", source="planner", target="echo")
    event["protocol"] = "mcp"
    nodes, _ = _graph([event])
    assert _agents(nodes) == set()

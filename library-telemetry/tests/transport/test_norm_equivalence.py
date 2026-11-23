#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The native KG path and the OTel path through SCOPE's norm declare the same agents, tools and usesTool pairs.

Needs the order-agnostic norm (realtime handlers) and an ontology that has the new classes: put both on
PYTHONPATH. Without them this test is skipped, so a missing norm is visible as a skip, not a pass.
"""

from __future__ import annotations

import pytest

pytest.importorskip("norm.ioa_observe.otel_io_realtime")

from library_ioa.plugins.a2a.aliases import ALIASES as A2A_ALIASES  # noqa: E402
from library_ioa.plugins.mcp.aliases import ALIASES as MCP_ALIASES  # noqa: E402
from mas.library.kg.pipeline import build_kg_document  # noqa: E402
from mas.library.telemetry.transport.projector import project  # noqa: E402
from mas.runtime.schema.transport import (  # noqa: E402
    DISCOVERY,
    MESSAGE_SENT,
    SOURCE_HOOK,
    TransportEvent,
)
from norm.ioa_observe.otel_io_realtime import RealtimeKG  # noqa: E402


def _event(kind: str, protocol: str, **attrs) -> TransportEvent:
    return TransportEvent(
        kind=kind,
        protocol=protocol,
        side="client",
        correlation_key="k",
        source=SOURCE_HOOK,
        timestamp=1.0,
        mas_id="demo",
        agent_id=str(attrs.get("source") or ""),
        attributes=attrs,
    )


def _facts_native(events: list[TransportEvent]) -> tuple[set, set, set]:
    doc = build_kg_document([e.to_record() for e in events], run_id="r1")
    nodes = doc["nodes"]
    agents = {n["id"] for n in nodes if n.get("node_type") == "Agent"}
    tools = {n["name"] for n in nodes if n.get("node_type") == "Tool"}
    names = {n["id"]: n.get("name") for n in nodes}
    uses = {(e["from_id"], names.get(e["to_id"])) for e in doc["edges"] if e.get("edge_type") == "usesTool"}
    return agents, tools, uses


def _facts_norm(events: list[TransportEvent]) -> tuple[set, set, set]:
    tables = {"a2a": A2A_ALIASES, "mcp": MCP_ALIASES}
    records = []
    for event in events:
        for alias in project([event], mode="contract", aliases=tables[event.protocol], emit_aliases=True).aliases:
            records.append(
                {
                    "body": alias.name,
                    "attributes": {"event.name": alias.name, "application_id": "demo", **alias.attributes},
                }
            )
    nodes, edges = RealtimeKG().add_all(records).snapshot()
    agents = {n["id"] for n in nodes if n["node_type"] == "Agent"}
    tools = {n["name"] for n in nodes if n["node_type"] == "Tool"}
    uses = {(e["source_id"], e["target_id"]) for e in edges if e["edge_type"] == "usesTool"}
    return agents, tools, uses


def test_message_and_card_declare_the_same_agents() -> None:
    events = [
        _event(MESSAGE_SENT, "a2a", source="planner", target="researcher", message_id="m1"),
        _event(DISCOVERY, "a2a", discovery_kind="agent_card", card={"name": "researcher", "description": "d"}),
    ]
    native_agents, _, _ = _facts_native(events)
    norm_agents, _, _ = _facts_norm(events)
    assert native_agents == norm_agents == {"planner", "researcher"}


def test_tools_discovery_declares_the_same_tools_and_links() -> None:
    events = [
        # another event declares the listing agent; both paths drop the edge of an undeclared one
        _event(DISCOVERY, "a2a", discovery_kind="agent_card", card={"name": "listener"}),
        _event(
            DISCOVERY,
            "mcp",
            discovery_kind="tools",
            source="listener",
            tools={"tools": [{"name": "echo", "description": "e"}, {"name": "ping"}]},
        ),
    ]
    _, native_tools, native_uses = _facts_native(events)
    _, norm_tools, norm_uses = _facts_norm(events)
    assert native_tools == norm_tools == {"echo", "ping"}
    assert native_uses == norm_uses == {("listener", "echo"), ("listener", "ping")}

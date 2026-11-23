#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native handlers for ``transport.*`` events.

Each event maps from its own fields. Message events declare the two Agents
named ``attributes.source`` / ``attributes.target`` (SCOPE has no Agent–Agent
edge). Discovery of an agent card declares the Agent it names; discovery of tools
declares each Tool and a ``usesTool`` edge from the listing agent. The listing
agent is not declared by this event, as in norm: the edge stays only when
another event declares that agent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Mapping

from mas.library.kg.observability.native.handlers.agent import mas_name

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def _attrs(event: Mapping[str, Any]) -> Dict[str, Any]:
    raw = event.get("attributes") or {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _declare_agent(
    builder: "NativeGraphBuilder", agent_id: str, mas: str, *, description: str = ""
) -> str:
    if not agent_id:
        return ""
    extra: Dict[str, Any] = {}
    if description:
        extra["description"] = description
    nid = builder.catalog("Agent", agent_id, node_id=agent_id, **extra)
    if mas:
        builder.add_edge("belongsToMAS", nid, builder.catalog("MAS", mas))
    return nid


def _tool_entries(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return []
    if not isinstance(content, dict):
        return []
    entries: List[Dict[str, Any]] = []
    for key in ("tools", "functions"):
        for item in content.get(key) or []:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            entries.append(function if isinstance(function, dict) else item)
    return entries


def handle_transport(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    kind = str(event.get("kind") or "")
    attrs = _attrs(event)
    mas = mas_name(event, builder)
    if kind.startswith("transport.message"):
        # MCP tools/call is recorded natively; SCOPE maps mcp.message.* to nothing
        # (the kernel tool call is the fact), so this event must not declare Agents.
        if str(event.get("protocol") or "") == "mcp":
            return []
        for key in ("source", "target"):
            name = attrs.get(key)
            if isinstance(name, str):  # an id is text; a number or object here is not an agent name
                _declare_agent(builder, name, mas)
        return []
    if kind != "transport.discovery":
        return []
    discovery = str(attrs.get("discovery_kind") or "")
    if discovery == "agent_card":
        card = attrs.get("card") or {}
        if isinstance(card, str):
            try:
                card = json.loads(card)
            except (TypeError, ValueError):
                card = {}
        name = card.get("name") if isinstance(card, dict) else None
        if isinstance(name, str) and name:  # no name on the card: nothing is declared, as norm does
            _declare_agent(builder, name, mas, description=str(card.get("description") or ""))
        return []
    if discovery != "tools":
        return []
    # As norm does: only the Tools are declared. The edge names the listing
    # agent, and it survives only when another event declares that agent
    # (the graph drops an edge whose source is not a node, in norm too).
    agent_id = str(event.get("agent_id") or attrs.get("source") or "")
    for entry in _tool_entries(attrs.get("tools")):
        name = str(entry.get("name") or "")
        if not name:
            continue
        extra = {}
        if entry.get("description"):
            extra["description"] = str(entry["description"])
        tool = builder.catalog("Tool", name, **extra)
        if agent_id:
            builder.add_edge("usesTool", agent_id, tool)
    return []


__all__ = ["handle_transport"]

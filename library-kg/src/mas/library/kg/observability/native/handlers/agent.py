#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``execution_start`` / ``execution_end`` event kinds.

Produces an AgentCall node. One record says what it says: the visit's first State on the start,
its final State on the end, its agent and its MAS. A delegated visit is an ordinary
AgentCall; its ``parent_call_id`` names the tool call that started it and is not read
for containment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from mas.library.kg.core.state_transition import ROLE_VISIT_END, ROLE_VISIT_START

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def mas_name(event: Dict[str, Any], builder: "NativeGraphBuilder") -> str:
    """The MAS an event names: ``mas_id``, else ``app_name``, else the configured one."""
    return str(
        event.get("mas_id") or event.get("app_name") or builder.app_name_override or ""
    ).strip()


def handle_agent_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    kind = event.get("kind", "")
    node = builder.call_partial(event, "AgentCall")
    if node is None:
        return []

    agent_id = str(event.get("agent_id") or "")
    node["agentName"] = agent_id
    if kind == "execution_start":
        node["agentType"] = event.get("agent_type", "")
        node["inputContent"] = event.get("input") or event.get("payload") or ""
        if event.get("agent_sequence"):
            node["agentSequence"] = int(event["agent_sequence"])
    elif kind == "execution_end":
        node["outputRaw"] = event.get("output") or event.get("payload") or ""
    role = ROLE_VISIT_START if kind == "execution_start" else ROLE_VISIT_END
    builder.emit_call(event, node, role=role)

    if agent_id:
        agent = builder.catalog("Agent", agent_id, node_id=agent_id)
        builder.executes(node, "executesAgent", agent)
        mas = mas_name(event, builder)
        if mas:
            builder.add_edge("belongsToMAS", agent, builder.catalog("MAS", mas))
    return [node]

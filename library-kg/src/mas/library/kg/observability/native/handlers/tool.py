#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handlers for the ``tool_call_*`` / ``network_call_*`` (ToolCall) and
``skill_execution_*`` (SkillCall) event kinds."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def _delegate_target(event: Dict[str, Any]) -> str:
    from mas.runtime.boundary.delegation.handoff import handoff_target

    return handoff_target(str(event.get("tool_name") or ""), event=event)


def handle_tool_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    kind = event.get("kind", "")
    tool_name = str(event.get("tool_name") or "")
    target_agent = _delegate_target(event)
    call_id = str(event.get("call_id") or "")
    rewrite = getattr(builder, "rewrite_tool_delegation", True)
    rewritten_ids = getattr(builder, "_rewritten_delegate_ids", set())
    if rewrite and (target_agent or call_id in rewritten_ids):
        if kind.endswith("_start") and target_agent:
            rewritten_ids.add(call_id)
            close_visit = getattr(builder, "close_agent_visit_for_delegate", None)
            if callable(close_visit):
                close_visit(str(event.get("agent_id") or ""), event)
            getattr(builder, "_delegated_agent_ids", set()).add(target_agent)
            if hasattr(builder, "agent_ids"):
                builder.agent_ids.add(target_agent)
            from_id = str(event.get("parent_call_id") or event.get("agent_id") or "")
            builder.edges.append(
                {
                    "edge_type": "callsAgent",
                    "from_id": from_id,
                    "from_type": "call",
                    "to_id": target_agent,
                    "to_type": "agent",
                    "tool_name": tool_name,
                }
            )
        return []

    node = builder.upsert_call(event, "ToolCall")
    if node is None:
        return []

    if kind.endswith("_start"):
        node.setdefault("toolName", event.get("tool_name") or "")
        node.setdefault("toolCallId", event.get("tool_call_id") or "")
        node.setdefault(
            "toolArguments",
            json.dumps(
                event.get("tool_arguments")
                or event.get("arguments")
                or event.get("parameters")
                or {}
            ),
        )

        # rewrite_tool_delegation=False: keep the ToolCall and still stamp
        # one callsAgent edge from this tool onto the target agent.
        if target_agent:
            builder.edges.append(
                {
                    "edge_type": "callsAgent",
                    "from_id": node["callId"],
                    "from_type": "call",
                    "to_id": target_agent,
                    "to_type": "agent",
                    "tool_name": tool_name,
                }
            )
    else:
        node["toolOutput"] = event.get("output") or event.get("result") or ""

    return [node]


def handle_skill_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    node = builder.upsert_call(event, "SkillCall")
    if node is None:
        return []
    kind = event.get("kind", "")

    if kind.endswith("_start"):
        node.setdefault(
            "skillName",
            event.get("skill_name") or event.get("processing_name") or node.get("kindBase", ""),
        )
        node.setdefault("skillInput", event.get("input") or event.get("payload") or "")
        if event.get("skill_version"):
            node.setdefault("skillVersion", event["skill_version"])
    else:
        node["skillOutput"] = event.get("output") or event.get("result") or ""
        node["status"] = event.get("status", node.get("status", "success"))

    return [node]

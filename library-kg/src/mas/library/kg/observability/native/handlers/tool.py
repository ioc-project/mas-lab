#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handlers for the ``tool_call_*`` / ``network_call_*`` (ToolCall) and
``skill_execution_*`` (SkillCall) event kinds. A SkillCall executes the ``Skill`` its
record names (``skill_name``).

A ``delegate_to_*`` tool call is an ordinary ToolCall of the caller; the callee's visit
is an ordinary AgentCall.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def handle_tool_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    kind = event.get("kind", "")
    node = builder.call_partial(event, "ToolCall")
    if node is None:
        return []
    tool_name = str(event.get("tool_name") or "")
    node["toolName"] = tool_name

    if kind.endswith("_start"):
        node["toolCallId"] = event.get("tool_call_id") or ""
        node["toolArguments"] = json.dumps(
            event.get("tool_arguments") or event.get("arguments") or event.get("parameters") or {}
        )
    else:
        output = event.get("output")
        if output in (None, ""):
            output = event.get("result")
        if output is None:
            output = ""
        # A structured result is text in the graph, as JSON like the arguments (and as the
        # OTel converter writes it), not a Python repr.
        node["toolOutput"] = output if isinstance(output, str) else json.dumps(output, default=str)

    builder.emit_call(event, node)
    if tool_name:
        tool = builder.catalog("Tool", tool_name)
        builder.executes(node, "executesTool", tool)
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            builder.add_edge("usesTool", builder.catalog("Agent", agent_id, node_id=agent_id), tool)
    return [node]


def handle_skill_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    node = builder.call_partial(event, "SkillCall")
    if node is None:
        return []
    name = str(event.get("skill_name") or event.get("processing_name") or "")
    if name:
        node["name"] = name  # named by the Skill it runs
    builder.emit_call(event, node)
    if name:
        skill = builder.catalog("Skill", name)
        builder.executes(node, "executesSkill", skill)
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            builder.add_edge(
                "usesSkill", builder.catalog("Agent", agent_id, node_id=agent_id), skill
            )
    return [node]

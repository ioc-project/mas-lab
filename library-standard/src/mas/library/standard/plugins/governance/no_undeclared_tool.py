#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Block tool calls the model was not given in this LLM call's ``tools`` list."""

from __future__ import annotations

from mas.runtime.boundary.delegation.policy import delegate_tool_name, delegation_targets
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.engine.tools import tool_entry_name
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "gov_no_undeclared_tool"


def undeclared_tool_observation(tool: str, available: list[str]) -> str:
    """Text the model sees when it names a tool that was not in ``tools``."""
    names = ", ".join(available) if available else "(none)"
    return f"Tool {tool!r} was not in the tools list offered to you. Do not call it. Available tools: {names}."


def _names_from_spec(spec: dict | None) -> list[str] | None:
    tools = (spec or {}).get("tools") if isinstance(spec, dict) else None
    if not isinstance(tools, list):
        return None
    names = [n for item in tools if (n := tool_entry_name(item))]
    return names or None


def allowed_tool_names(intent: EgressIntentView, *, config: KernelConfig) -> list[str] | None:
    """Names this TOOL_CALL may use.

    Prefer the ``tools`` array actually sent on the LLM call that produced
    this invocation (even if the agent spec lists more). Fall back to
    ``spec.tools`` when the engine did not record an offer. ``None`` means
    unknown — do not block.
    """
    if intent.offered_tools is not None:
        return list(intent.offered_tools)
    spec = getattr(config, "agent_spec", None)
    names = _names_from_spec(spec)
    if names is None or not isinstance(spec, dict):
        return names
    # delegate_to_<peer> tools come from the topology, not from spec.tools.
    agent_id = str(getattr(config, "agent_id", "") or "").strip() or None
    peers = delegation_targets({"spec": spec}, agent_id=agent_id) if agent_id else []
    return names + [delegate_tool_name(p) for p in peers if delegate_tool_name(p) not in names]


class NoUndeclaredToolPlugin:
    """Egress BLOCK when the pending tool was not exposed to the model.

    Designed for a governance *chain*: if this call is not a TOOL_CALL, or
    the name is in the offered list, the plugin PASSES so the next plugin
    runs. A BLOCK exits the chain with that error.
    """

    plugin_id = "gov_no_undeclared_tool@v1"

    def __init__(self, **_raw: object) -> None:
        pass

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        if intent.op != "TOOL_CALL" or not (intent.tool_name or "").strip():
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                "not a tool call",
            )
        allowed = allowed_tool_names(intent, config=config)
        if allowed is None:
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                "no offered-tool list was recorded for this call",
            )
        name = intent.tool_name.strip()
        if name in allowed:
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                f"tool {name!r} is in the offered tools list",
            )
        reason = undeclared_tool_observation(name, allowed)
        return GovDecision.BLOCK, PLUGIN_ID, reason

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Delegation policy — capability + topology → ``delegate_to_*`` tool surface.

Peer delegation tools are exposed when BOTH conditions hold:
1. The agent capability contract allows typed delegation (behavior.delegation_style)
2. The workflow topology declares delegates_to targets for that agent

This separates:
- Capability contract (WHAT an agent can do) from
- Topology declaration (WHO an agent delegates to)

Examples:
    >>> manifest = {
    ...     "spec": {
    ...         "behavior": {"delegation_style": "typed"},
    ...         "workflow": {
    ...             "nodes": [
    ...                 {"id": "moderator", "delegates_to": ["worker1", "worker2"]}
    ...             ]
    ...         }
    ...     }
    ... }
    >>> uses_llm_peer_delegation(manifest)
    True
    >>> delegation_targets(manifest, agent_id="moderator")
    ['worker1', 'worker2']
"""

from __future__ import annotations

from typing import Any

DELEGATE_TOOL_PREFIX = "delegate_to_"
SEND_TOOL_NAME = "send"
INTENT_HEADER = "mas.intent"
SEND_INTENTS = frozenset({"task", "inform", "ask", "steer"})
SEND_SEMANTICS = {
    "concept": "agent_comm",
    "op": "send",
    "subject_arg": "target",
    "intent_arg": "intent",
}
_DELEGATE_PARAMS = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "description": "Task for the delegate agent."},
        "context_id": {
            "type": "string",
            "description": (
                "Optional: continue a specific prior working-memory context with this "
                "peer instead of the current session's default one — e.g. to run two "
                "independent conversations with the same specialist in one session. "
                "Omit to use the session's default context for this peer."
            ),
        },
    },
    "required": ["task"],
}


def _spec(manifest: dict | None) -> dict[str, Any]:
    return (manifest or {}).get("spec") or {}


def _workflow(manifest: dict | None) -> dict[str, Any]:
    wf = _spec(manifest).get("workflow")
    return wf if isinstance(wf, dict) else {}


def delegation_style(manifest: dict | None) -> str:
    """Extract delegation_style from manifest behavior settings.
    
    Returns:
        The delegation style ('typed', 'none', etc.). Defaults to 'typed'.
        Current OSS supports only 'typed' (delegate_to_<id> tools).
    """
    spec = _spec(manifest)
    behavior = spec.get("behavior")
    if isinstance(behavior, dict):
        raw = behavior.get("delegation_style")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return "typed"


def uses_llm_peer_delegation(manifest: dict | None) -> bool:
    """Check if the manifest enables LLM-driven peer delegation.
    
    Delegation is enabled when behavior.delegation_style is 'typed'.
    Delegation enablement is capability-driven (behavior.delegation_style).
    
    Returns:
        True if typed delegation tools should be exposed, False otherwise.
    """
    # Current OSS supports typed delegate_to_<id> tools only.
    return delegation_style(manifest) == "typed"


def entry_agent_id(manifest: dict | None) -> str | None:
    wf = _workflow(manifest)
    if entry := wf.get("entry"):
        return str(entry)
    name = ((manifest or {}).get("metadata") or {}).get("name")
    return str(name) if name else None


def delegation_targets(manifest: dict | None, *, agent_id: str | None = None) -> list[str]:
    """Get delegation peer targets for a specific agent from workflow topology.
    
    Args:
        manifest: Agent or MAS manifest dict.
        agent_id: Optional agent ID. If None, returns all delegation targets
                  across the entire workflow.
    
    Returns:
        List of agent IDs this agent can delegate to, based on:
        1. behavior.delegation_style == 'typed' (capability contract)
        2. workflow.nodes[].delegates_to (topology declaration)
        
        Returns empty list if delegation is disabled or agent has no peers.
    """
    if not manifest or not uses_llm_peer_delegation(manifest):
        return []
    nodes = [n for n in (_workflow(manifest).get("nodes") or []) if isinstance(n, dict)]
    if not nodes:
        return []
    aid = agent_id or entry_agent_id(manifest)
    if aid:
        for node in nodes:
            if str(node.get("id") or "") == aid:
                return [str(t) for t in (node.get("delegates_to") or []) if t]
        return []
    seen: list[str] = []
    for node in nodes:
        for target in node.get("delegates_to") or []:
            s = str(target)
            if s and s not in seen:
                seen.append(s)
    return seen


def node_dispatch(manifest: dict | None, *, agent_id: str | None = None) -> str:
    """Return ``workflow.nodes[].dispatch`` for one agent (empty if unset)."""
    nodes = [n for n in (_workflow(manifest).get("nodes") or []) if isinstance(n, dict)]
    aid = agent_id or entry_agent_id(manifest)
    if not aid:
        return ""
    for node in nodes:
        if str(node.get("id") or "") == aid:
            return str(node.get("dispatch") or "").strip().lower()
    return ""


def uses_parallel_dispatch(manifest: dict | None, *, agent_id: str | None = None) -> bool:
    """True when the node fans out to all ``delegates_to`` peers in one act."""
    return node_dispatch(manifest, agent_id=agent_id) in {"parallel", "all"}


def delegate_tool_name(agent_id: str) -> str:
    return f"{DELEGATE_TOOL_PREFIX}{agent_id}"


def parse_delegate_tool_name(tool_name: str) -> str | None:
    if not tool_name.startswith(DELEGATE_TOOL_PREFIX):
        return None
    target = tool_name[len(DELEGATE_TOOL_PREFIX) :].strip()
    return target or None


def openai_delegation_tools(
    manifest: dict | None,
    *,
    agent_id: str | None = None,
    peer_descriptions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    descriptions: dict[str, str] = {}
    if peer_descriptions:
        descriptions.update({str(k): str(v) for k, v in peer_descriptions.items() if v})
    return [
        {
            "type": "function",
            "function": {
                "name": delegate_tool_name(peer),
                "description": descriptions.get(peer)
                or f"Delegate a sub-task to agent {peer}.",
                "parameters": _DELEGATE_PARAMS,
            },
        }
        for peer in delegation_targets(manifest, agent_id=agent_id)
    ]

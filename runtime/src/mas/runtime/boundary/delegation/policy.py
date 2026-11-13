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


def advertise_delegate_tools(manifest: dict | None) -> bool:
    """False when the model must not see ``delegate_to_*`` tools.

    ``behavior.delegation_style: none`` is the one switch. Topology still
    lists peers and the send path still runs.
    """
    return uses_llm_peer_delegation(manifest)


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


def _name(value: object) -> str:
    return str(value or "").strip()


def _names(values: object, *, exclude: frozenset[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        peer = _name(value)
        if not peer or peer in exclude or peer in seen:
            continue
        seen.add(peer)
        out.append(peer)
    return out


def _node_identity(node: dict[str, Any]) -> str:
    return _name(node.get("agent")) or _name(node.get("id"))


def _node_delegates(nodes: list[dict[str, Any]], node_id: str) -> list[str] | None:
    for node in nodes:
        if node_id not in (_node_identity(node), _name(node.get("id"))):
            continue
        return [_name(t) for t in (node.get("delegates_to") or []) if _name(t)]
    return None


def delegation_targets(manifest: dict | None, *, agent_id: str | None = None) -> list[str]:
    """Leftover ``delegate_to_*`` names this agent offers, and the next hop a pattern picks.

    That is this agent's ``delegates_to``. ``workflow.entry`` only names who
    receives the user message; it does not change this list. An empty list,
    a missing node, or a missing agent id means none of those tools (and a
    pattern that sends itself has no next hop). Send still delivers if called.

    ``behavior.delegation_style`` does not change this list. It only decides
    whether the model is shown the tools (see ``advertise_delegate_tools``).
    """
    if not manifest:
        return []
    nodes = [n for n in (_workflow(manifest).get("nodes") or []) if isinstance(n, dict)]
    self_id = _name(agent_id) or _name(((manifest or {}).get("metadata") or {}).get("name"))
    if not self_id:
        return []
    own = _node_delegates(nodes, self_id)
    if own is None:
        return []
    return _names(own, exclude=frozenset({self_id}))


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
    if not advertise_delegate_tools(manifest):
        return []
    descriptions: dict[str, str] = {}
    if peer_descriptions:
        descriptions.update({str(k): str(v) for k, v in peer_descriptions.items() if v})
    return [
        {
            "type": "function",
            "function": {
                "name": delegate_tool_name(peer),
                "description": descriptions.get(peer) or f"Delegate a sub-task to agent {peer}.",
                "parameters": _DELEGATE_PARAMS,
            },
        }
        for peer in delegation_targets(manifest, agent_id=agent_id)
    ]

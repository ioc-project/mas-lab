#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Who rides the Flavour's ``agent_comm`` chain, and the name each agent has on it."""

from __future__ import annotations

from typing import Any

from mas.ctl.compose.models import ResolvedInfra
from mas.runtime.boundary.delegation.policy import delegation_targets


def declared_entry_id(mas_config: dict[str, Any] | None) -> str:
    """The entry agent the MAS names: ``spec.entry_agent`` or ``workflow.entry``.

    Omitted, Control ``$entry`` matches nobody. :func:`entry_agent_id` still falls
    back to the first agency row when a session has to pick someone to run.
    """
    spec = _spec(mas_config)
    if isinstance(spec.get("entry_agent"), str) and spec["entry_agent"].strip():
        return spec["entry_agent"].strip()
    workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}
    entry = workflow.get("entry")
    return str(entry).strip() if entry else ""


def entry_agent_id(mas_config: dict[str, Any] | None) -> str:
    """Resolve the MAS entry agent id from mas config."""
    declared = declared_entry_id(mas_config)
    if declared:
        return declared
    spec = _spec(mas_config)
    agency = spec.get("agency") if isinstance(spec.get("agency"), dict) else {}
    agents = agency.get("agents") or spec.get("agents") or []
    if agents and isinstance(agents[0], dict):
        return str(agents[0].get("id") or agents[0].get("name") or "agent")
    return "agent"


def agency_ids(mas_config: dict[str, Any] | None) -> list[str]:
    """The agent ids of a MAS config's agency, in order."""
    spec = _spec(mas_config)
    agency = spec.get("agency") if isinstance(spec.get("agency"), dict) else {}
    out: list[str] = []
    for row in agency.get("agents") or []:
        agent_id = str((row.get("id") or row.get("name")) if isinstance(row, dict) else row or "").strip()
        if agent_id:
            out.append(agent_id)
    return out


def peers_of(mas_config: dict[str, Any] | None, agent_ids: list[str]) -> dict[str, set[str]]:
    """The agents each of ``agent_ids`` delegates to; an agent that delegates to no one is left out."""
    out: dict[str, set[str]] = {}
    for agent_id in agent_ids:
        targets = set(delegation_targets(mas_config, agent_id=agent_id))
        if targets:
            out[agent_id] = targets
    return out


def chain_attachments(infra: ResolvedInfra | None) -> tuple[set[str], set[str]]:
    """Agents served here on a Flavour chain (``listen``) and agents dialled over one (``connect``)."""
    served: set[str] = set()
    used: set[str] = set()
    for agent_id, usage in ((infra.agent_usage or {}) if infra is not None else {}).items():
        if "listen" in usage:
            served.add(agent_id)
        if "connect" in usage:
            used.add(agent_id)
    return served, used


def has_chain(infra: ResolvedInfra | None) -> bool:
    """True when the Flavour puts some agent on a chain."""
    return infra is not None and bool(infra.agent_chains)


def chain_for(infra: ResolvedInfra | None, agent_id: str) -> list[dict[str, Any]]:
    """The chain ``agent_id`` is served and dialled on, or ``[]`` when it is on none."""
    if infra is None:
        return []
    return list((infra.agent_chains or {}).get(agent_id) or [])


def chain_name(chain: list[dict[str, Any]]) -> str:
    """The rows of a chain, bottom first, as one name for handles, logs and errors.

    A chain whose params differ per agent (``{agent}``, ``bind.agents.<id>.params``) is named per agent.
    """
    if chain and chain[0].get("chain"):
        return str(chain[0]["chain"])
    return "+".join(str(layer.get("row") or layer.get("type")) for layer in chain)


def agent_chains(
    agent_id: str, *, peers: dict[str, set[str]], infra: ResolvedInfra | None
) -> list[list[dict[str, Any]]]:
    """The distinct chains ``agent_id`` joins: its own when it is served here, and each target's chain it sends on."""
    if not has_chain(infra):
        return []
    served, used = chain_attachments(infra)
    chains: dict[str, list[dict[str, Any]]] = {}
    if agent_id in served:
        own = chain_for(infra, agent_id)
        chains[chain_name(own)] = own
    for target in sorted((peers.get(agent_id) or set()) & used):
        chain = chain_for(infra, target)
        if chain:
            chains.setdefault(chain_name(chain), chain)
    return [chain for chain in chains.values() if chain]


def chain_identities(
    agent_ids: list[str], *, peers: dict[str, set[str]], infra: ResolvedInfra | None
) -> dict[str, str]:
    """Each linked agent's name on the bottom layer of the chains it joins (comma-joined when several)."""
    from mas.library.standard.lib.stack.handle import agent_name, layer_classes

    out: dict[str, str] = {}
    for agent_id in agent_ids:
        names: list[str] = []
        for chain in agent_chains(agent_id, peers=peers, infra=infra):
            bottom = chain[0]
            name = agent_name(layer_classes([bottom])[0], bottom.get("params") or {}, agent_id)
            if name not in names:
                names.append(name)
        if names:
            out[agent_id] = ", ".join(names)
    return out


def _spec(mas_config: dict[str, Any] | None) -> dict[str, Any]:
    root = mas_config or {}
    spec = root.get("spec") if isinstance(root.get("spec"), dict) else root
    return spec if isinstance(spec, dict) else {}

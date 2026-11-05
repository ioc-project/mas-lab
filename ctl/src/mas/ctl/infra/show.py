#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Build a bounded view of resolved infra chains for ``mas-ctl infra show``."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mas.ctl.compose.models import ResolvedInfra
from mas.runtime.engine.infra_pipeline import (
    middleware_call_kinds,
    middleware_id,
    middleware_per,
    stamp_middleware_step,
)

_MAX_DESTINATIONS = 32
_MAX_STEPS = 16
_MAX_AGENTS = 32
_MAX_CHARS = 120


def _clip(value: Any, *, limit: int = _MAX_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _redact_url(value: Any, *, limit: int = _MAX_CHARS) -> str:
    """Scheme, host, port, and path only — no userinfo and no query."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme and not parts.netloc:
        return _clip(text, limit=limit)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return _clip(urlunsplit((parts.scheme, host, parts.path, "", "")), limit=limit)


def _describe_step(step: dict[str, Any]) -> dict[str, Any]:
    stamped = stamp_middleware_step(step)
    kinds = list(middleware_call_kinds(stamped))
    return {
        "middleware": middleware_id(stamped),
        "for": kinds,
        "per": middleware_per(stamped),
    }


def _describe_steps(pipeline: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    steps = [step for step in (pipeline or []) if isinstance(step, dict)]
    return [_describe_step(step) for step in steps[:_MAX_STEPS]]


def infra_show_payload(
    infra: ResolvedInfra,
    *,
    agents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stable JSON document for ``mas-ctl infra show --json``."""
    common = list(infra.llm_proxy.get("pipeline") or [])
    leaves = dict(infra.llm_proxies or {})
    llm: list[dict[str, Any]] = []
    for name, leaf in list(leaves.items())[:_MAX_DESTINATIONS]:
        steps = common + list((leaf or {}).get("pipeline") or [])
        llm.append(
            {
                "name": _clip(name, limit=64),
                "api_base": _redact_url((leaf or {}).get("api_base") or ""),
                "steps": _describe_steps(steps),
            }
        )
    if not llm and infra.llm_proxy.get("api_base"):
        llm.append(
            {
                "name": _clip(infra.llm_proxy.get("name") or "default", limit=64),
                "api_base": _redact_url(infra.llm_proxy.get("api_base") or ""),
                "steps": _describe_steps(common),
            }
        )

    tool_common = list(infra.tool_pipeline or [])
    tool: list[dict[str, Any]] = []
    servers = dict(infra.tool_server_registry or {})
    for name, server in list(servers.items())[:_MAX_DESTINATIONS]:
        steps = tool_common + list((server or {}).get("pipeline") or [])
        tool.append(
            {
                "name": _clip(name, limit=64),
                "url": _redact_url((server or {}).get("url") or (server or {}).get("base_url") or ""),
                "steps": _describe_steps(steps),
            }
        )
    if not tool and tool_common:
        tool.append({"name": "tool", "url": "", "steps": _describe_steps(tool_common)})

    return {
        "llm": llm,
        "tool": tool,
        "agents": list(agents or [])[:_MAX_AGENTS],
    }


def document_agent_ids(document: dict[str, Any] | None) -> list[str]:
    """The agent ids of a MAS document (its agency), or the one id of an Agent document."""
    if not isinstance(document, dict):
        return []
    spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
    if str(document.get("kind") or "") == "MAS":
        agency = spec.get("agency") if isinstance(spec.get("agency"), dict) else {}
        ids: list[str] = []
        for row in agency.get("agents") or []:
            agent_id = str((row.get("id") or row.get("name")) if isinstance(row, dict) else row or "").strip()
            if agent_id:
                ids.append(agent_id)
        return ids
    name = str((document.get("metadata") or {}).get("name") or "").strip()
    return [name or "agent"] if (name or spec) else []


def agents_from_manifest(
    document: dict[str, Any] | None,
    *,
    infra: ResolvedInfra | None = None,
) -> list[dict[str, Any]]:
    """Bounded comm rows from a MAS or Agent YAML document.

    The comm column is how the agent is reached: the layers of its Flavour
    chain (``bind.agents.<id>.agent_comm``, else ``bind.agent_comm``), bottom
    first, for an agent on a chain; the Application ``protocol`` for an agent
    reached at its own url; else the library default ``agent_comm`` plugin.
    ``identity`` is the agent's name on the bottom layer of each chain it joins.
    """
    from mas.ctl.infra.identities import agent_chains, chain_for, chain_identities, peers_of
    from mas.runtime.registry import get_registry

    agent_ids = document_agent_ids(document)[:_MAX_AGENTS]
    if not agent_ids:
        return []
    peers = peers_of(document, agent_ids) if str(document.get("kind") or "") == "MAS" else {}
    apps = dict(infra.applications or {}) if infra is not None else {}
    names = chain_identities(agent_ids, peers=peers, infra=infra)
    default = get_registry().default_for("agent_comm")
    out: list[dict[str, Any]] = []
    for agent_id in agent_ids:
        row = apps.get(agent_id)
        joined = agent_chains(agent_id, peers=peers, infra=infra)
        own = chain_for(infra, agent_id)
        if own:
            comm = [str(layer.get("type") or "") for layer in own]
        elif joined:
            comm = [str(layer.get("type") or "") for layer in joined[0]]
        elif isinstance(row, dict) and str(row.get("protocol") or row.get("kind") or "").strip():
            comm = [str(row.get("protocol") or row.get("kind")).strip()]
        else:
            comm = [default] if default else []
        out.append(
            {
                "id": _clip(agent_id, limit=64),
                "comm": [_clip(layer, limit=64) for layer in comm],
                "identity": _clip(names.get(agent_id, ""), limit=96) if names.get(agent_id) else "",
            }
        )
    return out


def render_infra_show(payload: dict[str, Any]) -> str:
    """Human text: one destination per block, steps as `id for=… per=…`."""
    lines: list[str] = []
    llm = payload.get("llm") or []
    if llm:
        lines.append("llm")
        for leaf in llm:
            lines.append(f"  {leaf.get('name') or '?'}  {leaf.get('api_base') or ''}".rstrip())
            lines.extend(_step_lines(leaf.get("steps") or []))
    tool = payload.get("tool") or []
    if tool:
        lines.append("tool")
        for server in tool:
            label = f"  {server.get('name') or '?'}"
            url = server.get("url") or ""
            lines.append(f"{label}  {url}".rstrip() if url else label)
            lines.extend(_step_lines(server.get("steps") or []))
    agents = payload.get("agents") or []
    if agents:
        lines.append("agents")
        for agent in agents:
            parts = [", ".join(agent.get("comm") or []) or "(none)"]
            identity = str(agent.get("identity") or "").strip()
            if identity:
                parts.append(identity)
            lines.append(f"  {agent.get('id') or '?'}  {'  '.join(parts)}")
    if not lines:
        lines.append("(no llm, tool, or agent destinations)")
    return "\n".join(lines) + "\n"


def _step_lines(steps: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for step in steps:
        kinds = ",".join(step.get("for") or []) or "*"
        out.append(f"    {step.get('middleware') or '?'}  for={kinds}  per={step.get('per') or '?'}")
    return out

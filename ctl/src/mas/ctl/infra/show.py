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


def agents_from_manifest(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Bounded comm-stack rows from a MAS or Agent YAML document.

    MAS files declare the stack once as ``spec.agent_comm`` (``type``,
    ``protocol``, or ``kind``). That binding applies to every agent row.
    """
    if not isinstance(document, dict):
        return []
    kind = str(document.get("kind") or "")
    spec = document.get("spec") or {}
    mas_comm = _comm_layers(spec.get("agent_comm"))
    if kind == "MAS":
        agency = spec.get("agency") or {}
        rows = agency.get("agents") or []
        out: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                row_comm = _comm_layers(
                    row.get("communication") or row.get("agent_comm") or row.get("comm")
                )
                out.append(
                    {
                        "id": _clip(str(row.get("id") or row.get("name") or ""), limit=64),
                        "comm": row_comm or mas_comm,
                    }
                )
            elif row:
                out.append({"id": _clip(str(row), limit=64), "comm": mas_comm})
            if len(out) >= _MAX_AGENTS:
                break
        return out
    name = str((document.get("metadata") or {}).get("name") or "")
    if name or spec:
        return [
            {
                "id": _clip(name or "agent", limit=64),
                "comm": _comm_layers(
                    spec.get("communication") or spec.get("agent_comm") or spec.get("comm")
                )
                or mas_comm,
            }
        ]
    return []


def _comm_layers(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        name = raw.strip()
        return [_clip(name, limit=64)] if name else []
    if isinstance(raw, dict):
        variant = str(
            raw.get("type") or raw.get("protocol") or raw.get("kind") or raw.get("plugin") or raw.get("id") or ""
        ).strip()
        return [_clip(variant, limit=64)] if variant else []
    if isinstance(raw, list):
        layers: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                name = str(
                    item.get("type")
                    or item.get("protocol")
                    or item.get("kind")
                    or item.get("plugin")
                    or item.get("id")
                    or item.get("name")
                    or ""
                ).strip()
            else:
                name = str(item).strip()
            if name:
                layers.append(_clip(name, limit=64))
        return layers[:_MAX_STEPS]
    return []


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
            comm = ", ".join(agent.get("comm") or []) or "(none)"
            lines.append(f"  {agent.get('id') or '?'}  {comm}")
    if not lines:
        lines.append("(no llm, tool, or agent destinations)")
    return "\n".join(lines) + "\n"


def _step_lines(steps: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for step in steps:
        kinds = ",".join(step.get("for") or []) or "*"
        out.append(f"    {step.get('middleware') or '?'}  for={kinds}  per={step.get('per') or '?'}")
    return out

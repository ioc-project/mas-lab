#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bounded view of infra chains for ``mas-ctl infra show`` / ``infra resolved``."""

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


def _effective_steps(pipeline: list[dict[str, Any]] | None, kind: str) -> list[dict[str, Any]]:
    """Listed steps plus the documented default retry when that kind has none."""
    from mas.runtime.layers.defaults import merge_default_retry

    return merge_default_retry(list(pipeline or []), kind)


def _session_steps(
    pipeline: list[dict[str, Any]] | None,
    kind: str,
    *,
    spec: dict[str, Any] | None,
    retry_overlay: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The wrap chat and run-mas install: default retry pipeline, leftover spec.control."""
    from mas.runtime.layers.control import resolve_call_pipeline

    return resolve_call_pipeline(spec, list(pipeline or []), kind=kind, retry_overlay=retry_overlay)


def _describe_step(step: dict[str, Any]) -> dict[str, Any]:
    from mas.runtime.layers.compose import layer_id, layer_priority, step_enabled, step_id
    from mas.runtime.layers.defaults import drop_default_stamp

    raw = dict(step)
    inserted = drop_default_stamp(raw)
    stamped = stamp_middleware_step(raw)
    kinds = list(middleware_call_kinds(stamped))
    name = layer_id(stamped) or middleware_id(stamped)
    params = _public_params(stamped.get("params") if isinstance(stamped.get("params"), dict) else {})
    out: dict[str, Any] = {
        "id": step_id(stamped),
        "layer": name,
        "middleware": name,
        "for": kinds,
        "per": middleware_per(stamped),
        "priority": layer_priority(stamped),
    }
    if not step_enabled(stamped):
        out["enabled"] = False
    if params:
        out["params"] = params
    if inserted:
        out["default"] = True
    return out


def _public_params(params: dict[str, Any]) -> dict[str, Any]:
    """Scalar pipeline params. Keys that name a secret are omitted."""
    from mas.ctl.infra.bind import secret_keys

    out: dict[str, Any] = {}
    for key, value in params.items():
        token = str(key)
        if secret_keys({token: value}):
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            out[token] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            out[token] = value
        elif isinstance(value, float):
            out[token] = value
        elif isinstance(value, str):
            out[token] = _clip(value)
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) or item is None for item in value
        ):
            out[token] = value
    return out


def _describe_steps(pipeline: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    steps = [step for step in (pipeline or []) if isinstance(step, dict)]
    return [_describe_step(step) for step in steps[:_MAX_STEPS]]


def catalog_show_payload(
    infra: ResolvedInfra,
    *,
    connections: bool = True,
    pipeline: bool = True,
) -> dict[str, Any]:
    """One listed ref. Destinations if the ref has them; else listed steps or kernel fields."""
    sections: list[str] = []
    if infra.llm_proxies or infra.llm_proxy.get("api_base"):
        sections.append("llm")
    if infra.tool_server_registry:
        sections.append("tool")
    if sections:
        return infra_show_payload(
            infra,
            agents=[],
            sections=tuple(sections),
            connections=connections,
            pipeline=pipeline,
        )
    out: dict[str, Any] = {}
    if pipeline:
        described = _describe_steps(_listed_catalog_steps(infra))
        if described:
            out["pipeline"] = described
    engine = _runtime_engine_view(infra)
    if engine and connections and pipeline:
        out["runtime"] = engine
    return out


def _runtime_engine_view(infra: ResolvedInfra) -> dict[str, Any]:
    """Kernel fields a RuntimeEngine ref actually applies. No invented call pipeline."""
    raw = infra.runtime_engine or {}
    out: dict[str, Any] = {}
    if "engine_queue_depth" in raw:
        out["queue_depth"] = raw["engine_queue_depth"]
    for key in ("max_auto_steps", "timeout", "stream", "parallel", "cache"):
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    return out


def _listed_catalog_steps(infra: ResolvedInfra) -> list[dict[str, Any]]:
    """Pipeline as listed on the ref — not wrap-time default retry."""
    steps: list[dict[str, Any]] = []
    for src in (infra.llm_proxy.get("pipeline"), infra.tool_pipeline):
        for step in src or []:
            if isinstance(step, dict):
                steps.append(step)
        if steps:
            return steps[:_MAX_STEPS]
    return []


def infra_show_payload(
    infra: ResolvedInfra,
    *,
    agents: list[dict[str, Any]] | None = None,
    sections: tuple[str, ...] | list[str] | None = None,
    connections: bool = True,
    pipeline: bool = True,
    session: bool = False,
    spec: dict[str, Any] | None = None,
    retry_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable JSON document for ``mas-ctl infra show`` / ``infra resolved``."""
    want = {str(item).strip().lower() for item in (sections or ()) if str(item).strip()}
    if not want:
        want = {"llm", "tool", "agents"}
    common = list(infra.llm_proxy.get("pipeline") or [])
    leaves = dict(infra.llm_proxies or {})
    wrap = {"session": session, "spec": spec, "retry_overlay": retry_overlay}
    llm: list[dict[str, Any]] = []
    if "llm" in want:
        for name, leaf in list(leaves.items())[:_MAX_DESTINATIONS]:
            if not str(name).strip():
                continue
            llm.append(
                _destination_view(name, leaf, common, kind="llm", connections=connections, pipeline=pipeline, **wrap)
            )
        if not llm and infra.llm_proxy.get("api_base"):
            name = str(infra.llm_proxy.get("name") or "").strip() or _redact_url(infra.llm_proxy.get("api_base") or "")
            if name:
                llm.append(
                    _destination_view(
                        name,
                        infra.llm_proxy,
                        common,
                        kind="llm",
                        connections=connections,
                        pipeline=pipeline,
                        **wrap,
                    )
                )

    tool: list[dict[str, Any]] = []
    if "tool" in want:
        tool_common = list(infra.tool_pipeline or [])
        servers = dict(infra.tool_server_registry or {})
        for name, server in list(servers.items())[:_MAX_DESTINATIONS]:
            if not str(name).strip():
                continue
            tool.append(
                _destination_view(
                    name,
                    server,
                    tool_common,
                    kind="tool",
                    connections=connections,
                    pipeline=pipeline,
                    **wrap,
                )
            )

    out: dict[str, Any] = {}
    if "llm" in want:
        out["llm"] = llm
    if "tool" in want:
        out["tool"] = tool
    if "agents" in want:
        out["agents"] = list(agents or [])[:_MAX_AGENTS]
    return out


def _destination_view(
    name: Any,
    leaf: dict[str, Any] | None,
    common: list[dict[str, Any]],
    *,
    kind: str,
    connections: bool,
    pipeline: bool,
    session: bool = False,
    spec: dict[str, Any] | None = None,
    retry_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(leaf or {})
    row: dict[str, Any] = {"name": _clip(name, limit=64)}
    if connections:
        if kind == "llm":
            row["api_base"] = _redact_url(payload.get("api_base") or "")
        else:
            row["url"] = _redact_url(payload.get("url") or payload.get("base_url") or "")
    if pipeline:
        listed = common + list(payload.get("pipeline") or [])
        steps = (
            _session_steps(listed, kind, spec=spec, retry_overlay=retry_overlay)
            if session
            else _effective_steps(listed, kind)
        )
        row["steps"] = _describe_steps(steps)
    return row


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
    return [name] if name else []


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
            dest = _dest_line(leaf, "api_base")
            if dest:
                lines.append(dest)
            lines.extend(_step_lines(leaf.get("steps") or []))
    tool = payload.get("tool") or []
    if tool:
        lines.append("tool")
        for server in tool:
            dest = _dest_line(server, "url")
            if dest:
                lines.append(dest)
            lines.extend(_step_lines(server.get("steps") or []))
    listed = payload.get("pipeline") or []
    if listed:
        lines.append("pipeline")
        lines.extend(_step_lines(listed))
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    if runtime:
        lines.append("runtime")
        for key, value in runtime.items():
            lines.append(f"  {key}  {value}")
    agents = payload.get("agents") or []
    if agents:
        lines.append("agents")
        for agent in agents:
            agent_id = str(agent.get("id") or "").strip()
            if not agent_id:
                continue
            parts = [", ".join(agent.get("comm") or []) or "(none)"]
            identity = str(agent.get("identity") or "").strip()
            if identity:
                parts.append(identity)
            lines.append(f"  {agent_id}  {'  '.join(parts)}")
    if not lines:
        lines.append("(no llm, tool, or agent destinations)")
    return "\n".join(lines) + "\n"


def _dest_line(row: dict[str, Any], url_key: str) -> str:
    name = str(row.get("name") or "").strip()
    if not name:
        return ""
    label = f"  {name}"
    if url_key not in row:
        return label
    url = row.get(url_key) or ""
    return f"{label}  {url}".rstrip() if url else label


def _step_lines(steps: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for step in steps:
        name = step.get("middleware") or step.get("layer")
        if not name:
            continue
        kinds = ",".join(step.get("for") or [])
        line = f"    {name}"
        if kinds:
            line += f"  for={kinds}"
        per = step.get("per")
        if per:
            line += f"  per={per}"
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        extra = _param_text(params)
        if extra:
            line += f"  {extra}"
        if step.get("default"):
            line += "  (default)"
        if step.get("enabled") is False:
            line += "  (disabled)"
        if step.get("id"):
            line += f"  id={step['id']}"
        out.append(line)
    return out


def _param_text(params: dict[str, Any]) -> str:
    keys = list(params)
    if "max_attempts" in params:
        keys = ["max_attempts", *[key for key in keys if key != "max_attempts"]]
    parts: list[str] = []
    for key in keys:
        value = params[key]
        if isinstance(value, list):
            parts.append(f"{key}={','.join(str(item) for item in value)}")
        else:
            parts.append(f"{key}={value}")
    return "  ".join(parts)

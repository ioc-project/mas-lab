#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Build the :class:`HardeningInput` of a resolved session, or of manifest files read alone."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.hardening.model import Exposure, HardeningInput
from mas.runtime.spec.endpoint_usage import usage_listens

_INFRA_PROXY_KINDS = {"LLMProxy", "LLMLocal", "Infrastructure"}


def _host(url: str) -> str:
    return urlsplit(str(url or "")).hostname or ""


def _listens(row: dict[str, Any]) -> bool:
    usage = row.get("usage")
    try:
        return usage_listens(usage) if usage is not None else row.get("expose") is True
    except ValueError:
        return False


def a2a_exposure(name: str, row: dict[str, Any]) -> Exposure:
    a2a = row.get("a2a") if isinstance(row.get("a2a"), dict) else {}
    listen = a2a.get("listen") if isinstance(a2a.get("listen"), dict) else {}
    url = str(row.get("url") or "")
    identity = "none"
    if "admission" in a2a:
        callers = (a2a["admission"] or {}).get("callers") if isinstance(a2a["admission"], dict) else None
        identity = "verified" if isinstance(callers, dict) and callers else "empty"
    return Exposure(name, "a2a", url, str(listen.get("host") or _host(url)), identity)


def _exposures(
    applications: dict[str, dict[str, Any]],
    chains: dict[str, list[dict[str, Any]]],
    tool_servers: dict[str, dict[str, Any]],
) -> tuple[Exposure, ...]:
    out: list[Exposure] = []
    for name, row in sorted(applications.items()):
        if row.get("protocol") == "a2a" and _listens(row):
            out.append(a2a_exposure(name, row))
    for agent, rows in sorted(chains.items()):
        for entry in rows:
            params = entry.get("params") or {}
            if not _listens({"usage": entry.get("usage")}):
                continue
            # a layer verifies callers when its row carries a secret; ctl reads no layer's own parameters
            url = str(params.get("url") or "")
            verified = any(str(key).endswith("secret") and value for key, value in params.items())
            out.append(
                Exposure(
                    f"{agent}/{entry.get('row')}",
                    str(entry.get("type")),
                    url,
                    _host(url),
                    "verified" if verified else "none",
                )
            )
    for name, row in sorted(tool_servers.items()):
        if row.get("protocol") == "mcp" and _listens(row):
            # an MCP listener has no admission policy in this release
            out.append(Exposure(name, "mcp", str(row.get("url") or ""), str(row.get("host") or "127.0.0.1"), "none"))
    return tuple(out)


def _urls(
    llm: Iterable[dict[str, Any]],
    tool: Iterable[dict[str, Any]],
    applications: dict[str, dict[str, Any]],
    tool_servers: dict[str, dict[str, Any]],
) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    out += [(f"LLM destination {e.get('name')!r}", str(e["api_base"])) for e in llm if e.get("api_base")]
    out += [(f"tool destination {e.get('name')!r}", str(e["url"])) for e in tool if e.get("url")]
    for name, row in sorted(applications.items()):
        if row.get("url") and not _listens(row):
            out.append((f"application {name!r}", str(row["url"])))
    for name, row in sorted(tool_servers.items()):
        if row.get("url") and not _listens(row):
            out.append((f"tool server {name!r}", str(row["url"])))
    return tuple(out)


def _step_settings(entries: Iterable[dict[str, Any]], kind: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{kind} destination {e.get('name')!r} step {step.get('layer')!r}", dict(step.get("params") or {}))
        for e in entries
        for step in e.get("steps") or []
    ]


def from_session(
    agents: dict[str, dict[str, Any]],
    infra: ResolvedInfra | None,
    *,
    raw_documents: tuple[tuple[str, dict[str, Any]], ...] = (),
) -> HardeningInput:
    """The input for agents (effective specs) running on a resolved infra."""
    if infra is None:
        return HardeningInput(agents=dict(agents), raw_documents=raw_documents)
    llm: tuple[dict[str, Any], ...] = ()
    tool: tuple[dict[str, Any], ...] = ()
    from mas.ctl.infra.show import infra_show_payload

    payload = infra_show_payload(infra, sections=("llm", "tool"), connections=True, pipeline=True, session=True)
    llm, tool = tuple(payload.get("llm") or ()), tuple(payload.get("tool") or ())
    tool_servers = {str(k): dict(v) for k, v in (infra.tool_server_registry or {}).items()}
    settings: list[tuple[str, dict[str, Any]]] = [
        (f"LLM endpoint {name!r}", dict(proxy)) for name, proxy in sorted((infra.llm_proxies or {}).items())
    ]
    settings += _step_settings(llm, "LLM") + _step_settings(tool, "tool")
    settings += [(f"application {n!r}", dict(r)) for n, r in sorted((infra.applications or {}).items())]
    settings += [(f"tool server {n!r}", dict(r)) for n, r in sorted(tool_servers.items())]
    for agent, rows in sorted((infra.agent_chains or {}).items()):
        settings += [(f"agent {agent!r} chain row {e.get('row')!r}", dict(e.get("params") or {})) for e in rows]
    return HardeningInput(
        agents=dict(agents),
        exposures=_exposures(infra.applications or {}, infra.agent_chains or {}, tool_servers),
        llm=llm,
        tool=tool,
        tool_servers=tool_servers,
        urls=_urls(llm, tool, infra.applications or {}, tool_servers),
        settings=tuple(settings),
        engine_cache=dict((infra.runtime_engine or {}).get("cache") or {}),
        raw_documents=raw_documents,
    )


def from_documents(documents: Iterable[tuple[str, dict[str, Any]]]) -> HardeningInput:
    """The input for manifest files read alone: agents, applications, tool servers and LLM proxies."""
    docs = [(source, doc) for source, doc in documents if isinstance(doc, dict)]
    agents: dict[str, dict[str, Any]] = {}
    applications: dict[str, dict[str, Any]] = {}
    tool_servers: dict[str, dict[str, Any]] = {}
    llm: list[dict[str, Any]] = []
    for source, doc in docs:
        kind, spec = str(doc.get("kind") or ""), doc.get("spec")
        spec = spec if isinstance(spec, dict) else {}
        meta = doc.get("metadata")
        name = str((meta if isinstance(meta, dict) else {}).get("name") or Path(source).stem)
        if kind == "Agent":
            agents[name] = dict(spec)
        elif kind == "Application":
            endpoints = spec.get("endpoints")
            applications.update(
                {
                    str(k): dict(v)
                    for k, v in (endpoints if isinstance(endpoints, dict) else {}).items()
                    if isinstance(v, dict)
                }
            )
        elif kind == "ToolServerRegistry":
            rows = spec.get("tool_servers")
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("id"):
                    tool_servers[str(row["id"])] = dict(row)
        elif kind in _INFRA_PROXY_KINDS:
            proxy = spec.get("proxy") or spec.get("server")
            proxy = proxy if isinstance(proxy, dict) else {}
            steps = [
                {"layer": s.get("layer") or s.get("middleware"), "params": s.get("params") or {}}
                for s in (spec.get("pipeline") if isinstance(spec.get("pipeline"), list) else [])
                if isinstance(s, dict)
            ]
            llm.append({"name": name, "api_base": proxy.get("api_base") or "", "steps": steps, "proxy": dict(proxy)})
    settings: list[tuple[str, dict[str, Any]]] = [(f"LLM endpoint {e['name']!r}", e["proxy"]) for e in llm]
    settings += _step_settings(llm, "LLM")
    settings += [(f"application {n!r}", r) for n, r in sorted(applications.items())]
    settings += [(f"tool server {n!r}", r) for n, r in sorted(tool_servers.items())]
    return HardeningInput(
        agents=agents,
        exposures=_exposures(applications, {}, tool_servers),
        llm=tuple(llm),
        tool_servers=tool_servers,
        urls=_urls(llm, (), applications, tool_servers),
        settings=tuple(settings),
        raw_documents=tuple(docs),
    )


def raw_infra_documents(refs: Iterable[str], anchor: Path) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The infra files a session names, as written (``env:`` not expanded). Library refs are not read."""
    from mas.runtime.spec.source import load_yaml_file, resolve_ref_with_search

    out: list[tuple[str, dict[str, Any]]] = []
    for ref in refs:
        try:
            path = resolve_ref_with_search(str(ref), anchor)
            data = load_yaml_file(path)
        except (FileNotFoundError, ValueError, OSError):
            continue
        if isinstance(data, dict):
            out.append((str(ref), data))
    return tuple(out)

# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Expand ``NAME=scheme://…`` assignments into ``infra:`` CLI overrides."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlparse, urlunparse

import yaml

_HTTP_SCHEMES = {
    "a2a": "http",
    "a2as": "https",
    "mcp": "http",
    "mcps": "https",
}


def expand_connects(assignments: Sequence[str] | Iterable[str]) -> tuple[str, ...]:
    """Turn ``NAME=a2a://…`` / ``NAME=mcp://…#tool`` into ``infra:`` overrides.

    This is a shortcut over Application / ToolServerRegistry fields, not a
    second resolver. A2A peers become ``spec.endpoints`` ``usage: connect`` rows;
    MCP tools become ``spec.tool_servers`` ``usage: connect`` rows. Explicit
    ``--override`` values applied after these still win. ``--connect`` is the
    flag; ``--bind`` is the old name for the same expansion.
    """
    endpoints: dict[str, dict[str, Any]] = {}
    mcp_servers: dict[str, dict[str, Any]] = {}
    for source in assignments:
        name, uri = _split_assignment(source)
        parsed = urlparse(uri)
        scheme = (parsed.scheme or "").lower()
        if scheme not in _HTTP_SCHEMES:
            raise ValueError(
                f"{source!r} must use a2a://, a2as://, mcp://, or mcps:// "
                f"(got {scheme or 'no scheme'!r})"
            )
        http_url = _http_url(parsed)
        if scheme in {"a2a", "a2as"}:
            endpoints[name] = {"protocol": "a2a", "usage": "connect", "url": http_url}
            continue
        tool_name = (parsed.fragment or name).strip()
        if not tool_name:
            raise ValueError(f"MCP {source!r} needs a tool name or #fragment")
        server_id = _mcp_server_id(http_url)
        server = mcp_servers.setdefault(
            server_id,
            {
                "id": server_id,
                "protocol": "mcp",
                "usage": "connect",
                "url": http_url,
                "tools": [],
            },
        )
        if server.get("url") != http_url:
            raise ValueError(
                f"MCP {source!r} reuses id {server_id!r} with a different URL"
            )
        tools = server.setdefault("tools", [])
        if tool_name not in tools:
            tools.append(tool_name)

    overrides: list[str] = []
    for name, endpoint in endpoints.items():
        overrides.append(f"infra:spec.endpoints[{name!r}]={_yaml_flow(endpoint)}")
    for server in mcp_servers.values():
        server_id = str(server["id"])
        overrides.append(
            f"infra:spec.tool_servers[id={server_id}]={_yaml_flow(server)}"
        )
    return tuple(overrides)


def expand_listens(assignments: Sequence[str] | Iterable[str]) -> tuple[str, ...]:
    """Turn ``NAME=a2a://host:port/path`` into ``infra:`` overrides that serve ``NAME``.

    The mirror of :func:`expand_connects`: ``--connect`` says where to *use* a
    remote peer (``usage: connect``), ``--listen`` says where to *serve* an agent
    (``usage: listen``). Same Application endpoint field, same merge path.
    """
    overrides: list[str] = []
    for source in assignments:
        name, uri = _split_assignment(source, kind="listen")
        parsed = urlparse(uri)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"a2a", "a2as"}:
            raise ValueError(
                f"listen {source!r} must use a2a:// or a2as:// "
                f"(got {scheme or 'no scheme'!r}); serve an MCP tool with mas-mcp"
            )
        endpoint = {"protocol": "a2a", "usage": "listen", "url": _http_url(parsed)}
        overrides.append(f"infra:spec.endpoints[{name!r}]={_yaml_flow(endpoint)}")
    return tuple(overrides)


def combine_overrides(
    *,
    connects: Sequence[str] | Iterable[str] = (),
    overrides: Sequence[str] | Iterable[str] = (),
    listens: Sequence[str] | Iterable[str] = (),
) -> tuple[str, ...]:
    """``--connect`` and ``--listen`` expansions first, then explicit ``--override`` (later wins)."""
    return expand_connects(connects) + expand_listens(listens) + tuple(overrides)


def _split_assignment(source: str, *, kind: str = "connect") -> tuple[str, str]:
    if "=" not in source:
        raise ValueError(f"{kind} must be NAME=URI, got {source!r}")
    name, uri = source.split("=", 1)
    name = name.strip()
    uri = uri.strip()
    if not name or not uri:
        raise ValueError(f"{kind} must be NAME=URI, got {source!r}")
    return name, uri


def _http_url(parsed) -> str:
    http_scheme = _HTTP_SCHEMES[parsed.scheme.lower()]
    if not parsed.netloc:
        raise ValueError(f"URI is missing host: {parsed.geturl()!r}")
    return urlunparse((http_scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _mcp_server_id(http_url: str) -> str:
    parsed = urlparse(http_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = (parsed.path or "/mcp").strip("/").replace("/", "-") or "mcp"
    return f"mcp-{host}-{port}-{path}"


def _yaml_flow(value: Any) -> str:
    encoded = yaml.safe_dump(value, default_flow_style=True, sort_keys=False).strip()
    if encoded.endswith("\n..."):
        encoded = encoded[: -len("\n...")].strip()
    return encoded

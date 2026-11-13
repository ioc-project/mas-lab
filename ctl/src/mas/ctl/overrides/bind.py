# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Expand ``NAME=scheme://…`` assignments into ``infra:`` CLI overrides."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import yaml


@dataclass(frozen=True)
class _UrlScheme:
    """A ``scheme://`` a protocol plugin accepts on ``--connect`` / ``--listen``, as its library row declares it."""

    scheme: str
    protocol: str
    http_scheme: str
    connect_as: str  # ``endpoint`` (an Application row) or ``tool_server``
    listens: bool
    listen_hint: str = ""  # what to do instead, for a scheme that cannot be listened on


def _url_schemes() -> dict[str, _UrlScheme]:
    """Every scheme an installed protocol plugin declares (``url_schemes``, ``protocol_name``, ``connect_as``).

    ctl names no protocol: a library row says which schemes are its own and what they connect to.
    """
    from mas.runtime.registry import get_registry

    found: dict[str, _UrlScheme] = {}
    for entry in get_registry().all_entries():
        attrs = entry.attributes or {}
        protocol = str(attrs.get("protocol_name") or "").strip()
        schemes = attrs.get("url_schemes")
        if not protocol or not isinstance(schemes, dict):
            continue
        for scheme, http_scheme in schemes.items():
            found[str(scheme).lower()] = _UrlScheme(
                str(scheme).lower(),
                protocol,
                str(http_scheme),
                str(attrs.get("connect_as") or "endpoint"),
                bool(attrs.get("listen")),
                str(attrs.get("listen_hint") or ""),
            )
    return found


def expand_connects(assignments: Sequence[str] | Iterable[str]) -> tuple[str, ...]:
    """Turn ``NAME=<scheme>://…`` into ``infra:`` overrides, for each scheme a protocol plugin declares.

    This is a shortcut over Application / ToolServerRegistry fields, not a
    second resolver. A scheme the plugin connects as an endpoint becomes a
    ``spec.endpoints`` ``usage: connect`` row; one it connects as a tool server
    (``#fragment`` names the tool) becomes a ``spec.tool_servers`` row. Explicit
    ``--override`` values applied after these still win. ``--connect`` is the
    flag; ``--bind`` is the old name for the same expansion.
    """
    schemes = _url_schemes()
    endpoints: dict[str, dict[str, Any]] = {}
    servers: dict[str, dict[str, Any]] = {}
    for source in assignments:
        name, uri = _split_assignment(source)
        parsed = urlparse(uri)
        scheme = (parsed.scheme or "").lower()
        if scheme not in schemes:
            raise ValueError(f"{source!r} must use {_listed(schemes)} (got {scheme or 'no scheme'!r})")
        known = schemes[scheme]
        http_url = _http_url(parsed, known)
        if known.connect_as == "endpoint":
            endpoints[name] = {"protocol": known.protocol, "usage": "connect", "url": http_url}
            continue
        tool_name = (parsed.fragment or name).strip()
        if not tool_name:
            raise ValueError(f"{known.protocol} {source!r} needs a tool name or #fragment")
        server_id = _server_id(known.protocol, http_url)
        server = servers.setdefault(
            server_id,
            {"id": server_id, "protocol": known.protocol, "usage": "connect", "url": http_url, "tools": []},
        )
        if server.get("url") != http_url:
            raise ValueError(f"{known.protocol} {source!r} reuses id {server_id!r} with a different URL")
        tools = server.setdefault("tools", [])
        if tool_name not in tools:
            tools.append(tool_name)

    overrides: list[str] = []
    for name, endpoint in endpoints.items():
        overrides.append(f"infra:spec.endpoints[{name!r}]={_yaml_flow(endpoint)}")
    for server in servers.values():
        server_id = str(server["id"])
        overrides.append(f"infra:spec.tool_servers[id={server_id}]={_yaml_flow(server)}")
    return tuple(overrides)


def expand_listens(assignments: Sequence[str] | Iterable[str]) -> tuple[str, ...]:
    """Turn ``NAME=<scheme>://host:port/path`` into ``infra:`` overrides that serve ``NAME``.

    The mirror of :func:`expand_connects`: ``--connect`` says where to *use* a
    remote peer (``usage: connect``), ``--listen`` says where to *serve* an agent
    (``usage: listen``). Same Application endpoint field, same merge path.
    """
    overrides: list[str] = []
    for source in assignments:
        name, uri = _split_assignment(source, kind="listen")
        parsed = urlparse(uri)
        scheme = (parsed.scheme or "").lower()
        listening = {key: value for key, value in _url_schemes().items() if value.listens}
        if scheme not in listening:
            hint = _url_schemes().get(scheme, _UrlScheme("", "", "", "", False)).listen_hint
            raise ValueError(
                f"listen {source!r} must use {_listed(listening)} (got {scheme or 'no scheme'!r})"
                + (f"; {hint}" if hint else "")
            )
        known = listening[scheme]
        endpoint = {"protocol": known.protocol, "usage": "listen", "url": _http_url(parsed, known)}
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


def _listed(schemes: dict[str, _UrlScheme]) -> str:
    return ", ".join(f"{scheme}://" for scheme in sorted(schemes)) or "a scheme a protocol plugin declares"


def _http_url(parsed, known: _UrlScheme) -> str:
    http_scheme = known.http_scheme
    if not parsed.netloc:
        raise ValueError(f"URI is missing host: {parsed.geturl()!r}")
    return urlunparse((http_scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _server_id(protocol: str, http_url: str) -> str:
    parsed = urlparse(http_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = (parsed.path or f"/{protocol}").strip("/").replace("/", "-") or protocol
    return f"{protocol}-{host}-{port}-{path}"


def _yaml_flow(value: Any) -> str:
    encoded = yaml.safe_dump(value, default_flow_style=True, sort_keys=False).strip()
    if encoded.endswith("\n..."):
        encoded = encoded[: -len("\n...")].strip()
    return encoded

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map an infra ``ToolServerRegistry`` entry onto MCP client configuration.

Connection fields are resolved from infra before this adapter is constructed.
Unknown ``params`` keys are kept so a later SDK version can consume them without
a runtime code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mas.runtime import hostenv


def _expand_env_ref(value: str) -> str:
    """Expand ``env:VAR`` / ``env:VAR|default`` without logging the result.

    Manifests hold the value. ``env:VAR|default`` is an optional override.
    ``env:VAR`` is for secrets: unset means empty (the caller omits the key).
    """
    if not value.startswith("env:"):
        return value
    rest = value[4:].strip()
    if not rest:
        return ""
    var, sep, default = rest.partition("|")
    var = var.strip()
    default = default.strip() if sep else ""
    if not var:
        return default
    resolved = hostenv.get(var)
    if resolved:
        return resolved
    return default


def _expand_str_map(raw: dict[str, Any]) -> dict[str, str]:
    """Expand env refs and drop empty values so unset secrets are omitted."""
    out: dict[str, str] = {}
    for key, value in raw.items():
        resolved = _expand_env_ref(str(value))
        if resolved:
            out[str(key)] = resolved
    return out


@dataclass(frozen=True)
class MCPProviderSpec:
    """Normalized MCP provider binding from an infra server entry."""

    name: str
    transport: str
    tools: str | tuple[str, ...]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None
    follow_pagination: bool = True
    cache_ttl_ms: int | None = None
    cache_scope: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, spec: dict[str, Any]) -> "MCPProviderSpec":
        params = dict(spec.get("params") or {})
        tools = spec.get("tools", "*")
        if tools is None or tools == "*":
            claim: str | tuple[str, ...] = "*"
        elif isinstance(tools, str):
            claim = (tools,)
        else:
            claim = tuple(str(n) for n in tools)

        url = spec.get("url") or spec.get("endpoint") or params.get("url") or params.get("endpoint")
        if url:
            url = _expand_env_ref(str(url))
        timeout_raw = spec.get("timeout", params.get("timeout"))
        timeout = float(timeout_raw) if timeout_raw is not None else None
        headers = _expand_str_map(dict(spec.get("headers") or params.get("headers") or {}))
        env = _expand_str_map(dict(spec.get("env") or params.get("env") or {}))
        args = tuple(str(a) for a in (spec.get("args") or params.get("args") or []))
        cwd = spec.get("cwd") or params.get("cwd")
        command = spec.get("command") or params.get("command")
        transport = str(spec.get("transport") or params.get("transport") or ("streamable-http" if url else "stdio"))
        follow_raw = spec.get("follow_pagination", params.get("follow_pagination", True))
        cache_ttl_raw = spec.get("cache_ttl_ms", params.get("cache_ttl_ms"))
        cache_scope = spec.get("cache_scope") or params.get("cache_scope")

        return cls(
            name=str(spec.get("name") or "unnamed"),
            transport=transport,
            tools=claim,
            url=str(url) if url else None,
            command=str(command) if command else None,
            args=args,
            env=env,
            cwd=str(cwd) if cwd else None,
            headers=headers,
            timeout=timeout,
            follow_pagination=bool(follow_raw) if follow_raw is not None else True,
            cache_ttl_ms=int(cache_ttl_raw) if cache_ttl_raw is not None else None,
            cache_scope=str(cache_scope) if cache_scope else None,
            params=params,
        )

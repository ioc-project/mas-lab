#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map an infra ``ToolServerRegistry`` entry onto MCP client configuration.

Connection fields are resolved from infra before this adapter is constructed.
Unknown ``params`` keys are kept so a later SDK version can consume them without
a runtime code change. Headers and stdio env hold strings or SecretRef objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mas.runtime.secrets import SecretRef
from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.ref import check_http_header_secrets, is_ref, parse_secret_ref


def _as_header_value(key: str, value: Any) -> str | SecretRef:
    if is_ref(value):
        return parse_secret_ref(value, field=key)
    if isinstance(value, str) and value.startswith("env:"):
        raise SecretRefError(f"{key}: env: prefix is removed; write a SecretRef ({{secret: NAME}}) for a secret")
    return str(value) if value is not None else ""


def _map_values(raw: dict[str, Any]) -> dict[str, str | SecretRef]:
    out: dict[str, str | SecretRef] = {}
    for key, value in raw.items():
        mapped = _as_header_value(str(key), value)
        if mapped == "":
            continue
        out[str(key)] = mapped
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
    env: dict[str, str | SecretRef] = field(default_factory=dict)
    cwd: str | None = None
    headers: dict[str, str | SecretRef] = field(default_factory=dict)
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
        timeout_raw = spec.get("timeout", params.get("timeout"))
        timeout = float(timeout_raw) if timeout_raw is not None else None
        headers = _map_values(dict(spec.get("headers") or params.get("headers") or {}))
        check_http_header_secrets(headers)
        env = _map_values(dict(spec.get("env") or params.get("env") or {}))
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


def http_params(url: str, headers: Any, timeout: Any) -> dict[str, Any]:
    """Params of the ``http`` layer under an MCP layer. MCP endpoints redirect (``/mcp`` to ``/mcp/``) and the
    SDK's own client followed them."""
    params: dict[str, Any] = {"follow_redirects": True}
    if url:
        params["url"] = url
    if headers:
        params["headers"] = dict(headers)
    if timeout is not None:
        params["timeout"] = float(timeout)
    return params

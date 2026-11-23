#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP protocol layer over an http (or stdio) transport."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.stack.call import LayerLifecycle

from library_ioa.plugins.mcp.client import MCPClient
from library_ioa.plugins.mcp.client.wrapper import MCPClientWrapper
from library_ioa.plugins.mcp.spec import MCPProviderSpec, http_params


class McpLayer(LayerLifecycle):
    """MCP JSON-RPC over the transport below. The tool provider sits on top."""

    protocol_layer = True
    per = "destination"
    call_kinds = ("tool",)
    priority = 0

    def __init__(self, wrapper: MCPClientWrapper, *, inner: Any = None) -> None:
        super().__init__()
        self.wrapper = wrapper
        self.inner = inner

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "McpLayer":
        del call_kinds
        binding = MCPProviderSpec.from_dict(dict(params or {}))
        transport = binding.transport
        if transport in {"http", "streamable-http"}:
            transport = "streamable-http"
        client = MCPClient(
            url=binding.url,
            command=binding.command,
            args=list(binding.args),
            env=binding.env,
            cwd=binding.cwd,
            headers=binding.headers,
            timeout=binding.timeout,
            transport=transport,
            follow_pagination=binding.follow_pagination,
            cache_ttl_ms=binding.cache_ttl_ms,
            cache_scope=binding.cache_scope,
            http_client=(lambda: _http_client(inner)) if inner is not None else None,
        )
        wrapped = cls(MCPClientWrapper(client), inner=inner)
        return wrapped

    @classmethod
    def protocol_layers(cls, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
        """``protocol: mcp`` plus a url is sugar for ``[http, mcp]``, bottom first."""
        raw = dict(spec or {})
        url = str(raw.get("url") or raw.get("endpoint") or "").strip()
        if str(raw.get("transport") or "streamable-http") not in {"streamable-http", "http"}:
            url = ""
        mcp_params = {k: v for k, v in raw.items() if k not in {"pipeline", "layer", "middleware", "for", "per"}}
        steps = []
        if url:
            params = http_params(url, raw.get("headers"), raw.get("timeout"))
            steps.append({"layer": "http", "for": ["tool"], "params": params})
        steps.append({"layer": "mcp", "for": ["tool"], "params": mcp_params})
        return steps


def _http_client(inner: Any) -> Any:
    """The transport's async HTTP client. MCP streamable HTTP needs that, not the sync pool."""
    if inner is None:
        return None
    for name in ("aclient", "client"):
        client = getattr(inner, name, None)
        if callable(client):
            client = client()
        if client is not None:
            return client
    return None

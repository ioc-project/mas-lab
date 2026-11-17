#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP tool provider built from an infra ``ToolServerRegistry`` entry."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from mas.runtime.registry.provider_protocol import ToolProvider
from mas.runtime.spec.endpoint_usage import usage_connects

from library_ioa.plugins.mcp.client import MCPClient
from library_ioa.plugins.mcp.client.wrapper import MCPClientWrapper, run_sync
from library_ioa.plugins.mcp.contract import as_mas_tool_result, as_mas_tool_spec
from library_ioa.plugins.mcp.spec import MCPProviderSpec
from library_ioa.utils import translate_mcp_error

logger = logging.getLogger(__name__)


def _http_inner(binding: MCPProviderSpec) -> Any:
    """The http transport under an MCP layer, for a streamable-http server. SSE and stdio carry their own."""
    if not binding.url or binding.transport not in {"streamable-http", "http"}:
        return None
    try:
        from mas.runtime.layers.compose import LayerLoadError, apply_layer
    except ImportError:
        return None
    params: dict[str, Any] = {"url": binding.url, "follow_redirects": True}
    if binding.headers:
        params["headers"] = dict(binding.headers)
    if binding.timeout is not None:
        params["timeout"] = float(binding.timeout)
    try:
        return apply_layer(None, {"layer": "http", "for": ["tool"], "params": params})
    except LayerLoadError:
        return None


_HTTP_TRANSPORTS = {"streamable-http", "http"}
_SSE_TRANSPORTS = {"sse"}


class MCPClientFactory:
    """Build an MCP client from a normalized provider spec."""

    def __init__(self, spec: MCPProviderSpec) -> None:
        self.spec = spec

    def create_client(self) -> MCPClientWrapper:
        transport = self.spec.transport
        if transport in _HTTP_TRANSPORTS | _SSE_TRANSPORTS:
            if not self.spec.url:
                raise ValueError("MCP HTTP/SSE provider requires url")
            return MCPClientWrapper(
                MCPClient(
                    url=self.spec.url,
                    transport="sse" if transport in _SSE_TRANSPORTS else "streamable-http",
                    headers=self.spec.headers,
                    timeout=self.spec.timeout,
                    follow_pagination=self.spec.follow_pagination,
                    cache_ttl_ms=self.spec.cache_ttl_ms,
                    cache_scope=self.spec.cache_scope,
                )
            )
        if self.spec.command:
            return MCPClientWrapper(
                MCPClient(
                    command=self.spec.command,
                    args=list(self.spec.args),
                    env=self.spec.env,
                    cwd=self.spec.cwd,
                    timeout=self.spec.timeout,
                    transport="stdio",
                    follow_pagination=self.spec.follow_pagination,
                    cache_ttl_ms=self.spec.cache_ttl_ms,
                    cache_scope=self.spec.cache_scope,
                )
            )
        raise ValueError("MCP provider requires either command or url metadata")


class MCPToolProvider(ToolProvider):
    """MCP adapter: discover/list/call via an MCP client. Runtime only routes names here."""

    origin = "external"
    kind = "mcp"

    def __init__(
        self,
        client_wrapper: MCPClientWrapper,
        *,
        provider_name: str = "mcp",
        tools_claim: str | list[str] | tuple[str, ...] | None = "*",
        timeout: float | None = None,
        inner: Any = None,
    ) -> None:
        self._client = client_wrapper
        self.inner = inner
        self.provider_name = provider_name
        self.timeout = timeout
        if tools_claim is None or tools_claim == "*":
            self.tools_claim: str | tuple[str, ...] = "*"
        elif isinstance(tools_claim, str):
            self.tools_claim = (tools_claim,)
        else:
            self.tools_claim = tuple(str(n) for n in tools_claim)

    @classmethod
    def protocol_layers(cls, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
        from library_ioa.plugins.mcp.layer import McpLayer

        return McpLayer.protocol_layers(spec)

    @classmethod
    def from_provider_spec(cls, spec: dict[str, Any]) -> "MCPToolProvider":
        """Build a provider from a normalized MCP infra server entry.

        ``protocol: mcp`` plus a url is sugar for ``[http, mcp]``.
        """
        from library_ioa.plugins.mcp.layer import McpLayer

        binding = MCPProviderSpec.from_dict(spec)
        inner = _http_inner(binding)
        if inner is not None:
            layer = McpLayer.wrap(inner, spec)
            return cls(
                layer.wrapper,
                provider_name=binding.name,
                tools_claim=binding.tools,
                timeout=binding.timeout,
                inner=layer,
            )
        client = MCPClientFactory(binding).create_client()
        return cls(
            client,
            provider_name=binding.name,
            tools_claim=binding.tools,
            timeout=binding.timeout,
        )

    @classmethod
    def from_tool_servers(cls, server_items: list[Any]) -> list["MCPToolProvider"]:
        """Build one provider per usable MCP infra server."""
        providers: list[MCPToolProvider] = []
        seen: set[str] = set()
        for server in server_items:
            if not isinstance(server, dict):
                continue
            if str(server.get("protocol") or "").lower() != "mcp":
                continue
            if not usage_connects(server.get("usage")):
                continue
            ident = str(server.get("id") or server.get("name") or "").strip()
            if not ident or ident in seen:
                continue
            seen.add(ident)
            spec = dict(server)
            spec.update(name=ident, kind="mcp")
            providers.append(cls.from_provider_spec(spec))
        return providers

    def has_tools(self) -> bool:
        return self._client is not None

    def invalidate(self) -> None:
        drop = getattr(self._client, "invalidate_list_cache", None)
        if callable(drop):
            drop()

    def _list_user(self, ctx: Any) -> str:
        return str(getattr(ctx, "user", "") or "")

    def _list_coro(self, ctx: Any):
        return self._client.list_tools(user=self._list_user(ctx))

    def close(self) -> None:
        """Close the held MCP session (and a stdio server process). Idempotent."""
        try:
            closer = getattr(self._client, "close", None)
            if callable(closer):
                closer()
        finally:
            from mas.runtime.layers.lifecycle import call_stop

            call_stop(self.inner)

    def _run(self, coro: Any) -> Any:
        runner = getattr(self._client, "run", None)
        if callable(runner):
            return runner(coro)
        return run_sync(coro)

    def discover_tools(self, *, ctx: Any = None) -> List[Dict[str, Any]]:
        """MCP ``tools/list`` advertisement. Called at runtime init for ``*`` claims."""
        try:
            tools = self._run(self._list_coro(ctx))
            return [as_mas_tool_spec(item) for item in (tools or []) if isinstance(item, dict)]
        except Exception as exc:
            logger.error("Failed to list tools from MCP client: %s", exc, exc_info=True)
            raise

    def list_tools(self, *, ctx: Any = None) -> List[Dict[str, Any]]:
        """Advertise claimed names. Explicit lists skip ``tools/list``."""
        if self.tools_claim != "*":
            return [{"name": n, "parameters": {"type": "object", "properties": {}}} for n in self.tools_claim]
        return self.discover_tools(ctx=ctx)

    async def acall_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
        **kwargs: Any,
    ) -> Any:
        if self.tools_claim != "*" and tool_name not in self.tools_claim:
            return {
                "is_error": True,
                "content": f"[mcp] tool {tool_name!r} is not advertised",
                "tool": tool_name,
            }
        options = dict(kwargs)
        if self.timeout is not None:
            options.setdefault("timeout_seconds", self.timeout)
        try:
            call = self._client.call_tool(tool_name, arguments, **options)
            arun = getattr(self._client, "arun", None)
            result = await arun(call) if callable(arun) else await call
            mapped = as_mas_tool_result(result)
            if mapped.get("is_error"):
                mapped.setdefault("tool", tool_name)
            return mapped
        except Exception as exc:
            logger.error("Error calling MCP tool %s: %s", tool_name, exc, exc_info=True)
            return translate_mcp_error(exc)

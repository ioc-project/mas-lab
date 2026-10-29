#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from library_ioa.plugins.mcp.provider import MCPClientFactory, MCPToolProvider
from library_ioa.plugins.mcp.spec import MCPProviderSpec
from mas.runtime.registry.tool_provider_registry import ToolProviderRegistry, providers_from_manifest


class _Client:
    """Duck-typed MCPClientWrapper used at the plugin constructor boundary."""

    def __init__(self, tools=None, result=None, list_error=None, call_error=None) -> None:
        self.tools = (
            tools
            if tools is not None
            else [{"name": "calculator", "description": "calc", "inputSchema": {"type": "object"}}]
        )
        self.result = result if result is not None else {"content": [{"type": "text", "text": "4"}]}
        self.list_error = list_error
        self.call_error = call_error
        self.list_calls = 0

    async def list_tools(self, **_kwargs):
        self.list_calls += 1
        if self.list_error:
            raise self.list_error
        return self.tools

    async def call_tool(self, tool_name: str, arguments: dict, **kwargs):
        if self.call_error:
            raise self.call_error
        self.call_kwargs = kwargs
        return {"tool": tool_name, "arguments": arguments, **self.result}


def test_from_provider_spec_http_binds_client_fields() -> None:
    provider = MCPToolProvider.from_provider_spec(
        {
            "kind": "mcp",
            "name": "search",
            "transport": "streamable-http",
            "url": "http://127.0.0.1:9001/mcp",
            "headers": {"X-Test": "1"},
            "timeout": 5,
            "tools": "*",
        }
    )
    inner = provider._client._client
    assert provider.provider_name == "search"
    assert provider.tools_claim == "*"
    assert provider.origin == "external"
    assert provider.kind == "mcp"
    assert inner.url == "http://127.0.0.1:9001/mcp"
    assert inner.transport == "streamable-http"
    assert inner.headers == {"X-Test": "1"}
    assert inner.timeout == 5.0
    assert inner.cache_ttl_ms is None
    assert inner.cache_scope is None


def test_from_provider_spec_forwards_list_cache_policy() -> None:
    provider = MCPToolProvider.from_provider_spec(
        {
            "kind": "mcp",
            "name": "search",
            "url": "http://127.0.0.1:9001/mcp",
            "cache_ttl_ms": 1000,
            "cache_scope": "private",
        }
    )
    inner = provider._client._client
    assert inner.cache_ttl_ms == 1000
    assert inner.cache_scope == "private"


def test_from_provider_spec_stdio_and_sse() -> None:
    stdio = MCPToolProvider.from_provider_spec(
        {
            "kind": "mcp",
            "name": "local-mcp",
            "command": "python",
            "args": ["-m", "demo"],
            "env": {"A": "1"},
            "cwd": "/tmp",
            "tools": ["calculator"],
        }
    )
    assert stdio.tools_claim == ("calculator",)
    assert stdio._client._client.command == "python"
    assert stdio._client._client.transport == "stdio"

    sse = MCPToolProvider.from_provider_spec(
        {
            "kind": "mcp",
            "name": "sse-mcp",
            "transport": "sse",
            "url": "http://127.0.0.1:9001/sse",
        }
    )
    assert sse._client._client.transport == "sse"

    http_alias = MCPToolProvider.from_provider_spec(
        {
            "kind": "mcp",
            "name": "http-mcp",
            "transport": "http",
            "url": "http://127.0.0.1:9001/mcp",
        }
    )
    assert http_alias._client._client.transport == "streamable-http"


def test_from_tool_servers_skips_deploy_and_non_mcp() -> None:
    providers = MCPToolProvider.from_tool_servers(
        [
            {"id": "local", "protocol": "local"},
            {
                "id": "deploy-only",
                "protocol": "mcp",
                "usage": "deploy",
                "url": "http://127.0.0.1:9001/mcp",
            },
            {
                "id": "mcp-tools",
                "protocol": "mcp",
                "url": "http://127.0.0.1:9001/mcp",
            },
            {
                "name": "mcp-tools",
                "protocol": "mcp",
                "url": "http://127.0.0.1:9/dup",
            },
        ]
    )
    assert len(providers) == 1
    assert providers[0].provider_name == "mcp-tools"
    assert providers[0].kind == "mcp"
    assert providers[0].tools_claim == "*"


def test_factory_requires_url_or_command() -> None:
    with pytest.raises(ValueError, match="url"):
        MCPClientFactory(MCPProviderSpec.from_dict({"name": "x", "transport": "sse"})).create_client()
    with pytest.raises(ValueError, match="command or url"):
        MCPClientFactory(MCPProviderSpec.from_dict({"name": "x", "transport": "stdio"})).create_client()


def test_discover_maps_contract() -> None:
    client = _Client(
        tools=[
            {
                "name": "web-search",
                "input_schema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
                "annotations": {"idempotentHint": True},
            }
        ]
    )
    provider = MCPToolProvider(client, provider_name="search", tools_claim="*")
    first = provider.discover_tools()
    second = provider.discover_tools()
    assert first[0]["name"] == "web-search"
    assert first[0]["parameters"]["required"] == ["query"]
    assert first[0]["idempotent"] is True
    assert second[0]["name"] == first[0]["name"]


def test_explicit_claim_skips_live_list() -> None:
    client = _Client(
        tools=[
            {"name": "web-search", "description": "a"},
            {"name": "other-tool", "description": "b"},
        ]
    )
    provider = MCPToolProvider(client, provider_name="search", tools_claim=("other-tool",))
    names = [t["name"] for t in provider.list_tools()]
    assert names == ["other-tool"]
    assert client.list_calls == 0
    registry = ToolProviderRegistry()
    registry.register_provider(provider)
    registry.initialize()
    assert client.list_calls == 0
    out = registry.call_tool("other-tool", {})
    assert out["is_error"] is False
    assert out["result"] == "4"


def test_call_tool_maps_result_and_errors() -> None:
    provider = MCPToolProvider(_Client(), provider_name="calc")
    mapped = provider.call_tool("calculator", {"expression": "2 + 2"})
    assert mapped["status"] == "ok"
    assert mapped["result"] == "4"
    assert mapped["is_error"] is False

    failing = MCPToolProvider(_Client(call_error=RuntimeError("boom")))
    err = failing.call_tool("calculator", {})
    assert err["status"] == "error"
    assert err["is_error"] is True
    assert err["type"] == "RuntimeError"
    assert "boom" in err["error"]


def test_call_tool_marks_protocol_error_payload() -> None:
    provider = MCPToolProvider(_Client(result={"content": [{"type": "text", "text": "nope"}], "isError": True}))
    mapped = provider.call_tool("calculator", {})
    assert mapped["is_error"] is True
    assert mapped["tool"] == "calculator"
    assert mapped["result"] == "nope"


def test_call_tool_forwards_optional_timeout() -> None:
    client = _Client()
    provider = MCPToolProvider(client, timeout=2.5)
    provider.call_tool("calculator", {})
    assert client.call_kwargs["timeout_seconds"] == 2.5


def test_discover_failure_raises_not_swallowed() -> None:
    provider = MCPToolProvider(_Client(list_error=ConnectionError("down")))
    with pytest.raises(ConnectionError, match="down"):
        provider.discover_tools()


def test_has_tools_and_registry_star_init() -> None:
    provider = MCPToolProvider(_Client(), provider_name="calc-server")
    assert provider.has_tools() is True
    registry = ToolProviderRegistry()
    registry.register_provider(provider)
    tools = registry.list_tools()
    assert any(tool["name"] == "calculator" for tool in tools)
    result = registry.call_tool("calculator", {"expression": "2 + 2"})
    assert result["result"] == "4"


def test_mcp_kind_binds_via_urn_registry() -> None:
    providers = providers_from_manifest(
        {
            "spec": {
                "providers": [
                    {
                        "kind": "mcp",
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "http://127.0.0.1:9001/mcp",
                        "tools": "*",
                    }
                ]
            }
        }
    )
    assert len(providers) == 1
    assert isinstance(providers[0], MCPToolProvider)
    assert providers[0].provider_name == "search"
    assert providers[0].tools_claim == "*"


def test_invalidate_is_optional_on_duck_client() -> None:
    provider = MCPToolProvider(_Client())
    provider.invalidate()

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from library_ioa.plugins.mcp.spec import MCPProviderSpec
from mas.runtime.secrets import SecretRef
from mas.runtime.secrets.errors import SecretRefError


def test_params_endpoint_alias() -> None:
    spec = MCPProviderSpec.from_dict({"name": "s", "params": {"endpoint": "http://127.0.0.1:9/mcp"}})
    assert spec.url == "http://127.0.0.1:9/mcp"
    assert spec.transport == "streamable-http"


def test_star_claim_default_and_inferred_http_transport() -> None:
    spec = MCPProviderSpec.from_dict(
        {
            "kind": "mcp",
            "name": "search",
            "url": "http://127.0.0.1:9001/mcp",
        }
    )
    assert spec.name == "search"
    assert spec.tools == "*"
    assert spec.transport == "streamable-http"
    assert spec.url == "http://127.0.0.1:9001/mcp"
    assert spec.command is None
    assert spec.timeout is None
    assert spec.headers == {}
    assert spec.params == {}


def test_endpoint_alias_and_explicit_tool_string() -> None:
    spec = MCPProviderSpec.from_dict(
        {
            "name": "calc",
            "endpoint": "http://example.invalid/mcp",
            "tools": "calculator",
        }
    )
    assert spec.url == "http://example.invalid/mcp"
    assert spec.tools == ("calculator",)


def test_explicit_tool_list_and_stdio_fields() -> None:
    spec = MCPProviderSpec.from_dict(
        {
            "name": "stdio-tools",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "demo"],
            "env": {"MCP_DEMO": "1"},
            "cwd": "/tmp/mcp",
            "tools": ["web-search", "calc"],
        }
    )
    assert spec.transport == "stdio"
    assert spec.command == "python"
    assert spec.args == ("-m", "demo")
    assert spec.env == {"MCP_DEMO": "1"}
    assert spec.cwd == "/tmp/mcp"
    assert spec.tools == ("web-search", "calc")


def test_params_carry_url_headers_timeout_args_and_leftovers() -> None:
    spec = MCPProviderSpec.from_dict(
        {
            "name": "search",
            "params": {
                "url": "http://127.0.0.1:9001/mcp",
                "headers": {"Authorization": {"secret": {"name": "mcp", "prefix": "Bearer "}}},
                "timeout": 12,
                "args": ["--verbose"],
                "command": "uv",
                "cwd": "/opt/mcp",
                "env": {"A": 1},
                "transport": "sse",
                "experimental_oid": "keep-me",
            },
        }
    )
    assert spec.url == "http://127.0.0.1:9001/mcp"
    assert spec.transport == "sse"
    assert spec.headers == {"Authorization": SecretRef(name="mcp", prefix="Bearer ")}
    assert spec.timeout == 12.0
    assert spec.command == "uv"
    assert spec.args == ("--verbose",)
    assert spec.cwd == "/opt/mcp"
    assert spec.env == {"A": "1"}
    assert spec.params["experimental_oid"] == "keep-me"


def test_top_level_headers_timeout_override_params() -> None:
    spec = MCPProviderSpec.from_dict(
        {
            "name": "search",
            "url": "http://127.0.0.1:9001/mcp",
            "headers": {"X-Test": "yes"},
            "timeout": 3,
            "params": {"headers": {"X-Test": "no"}, "timeout": 99},
        }
    )
    assert spec.headers == {"X-Test": "yes"}
    assert spec.timeout == 3.0


def test_null_tools_is_star_and_missing_name_is_unnamed() -> None:
    spec = MCPProviderSpec.from_dict({"tools": None})
    assert spec.tools == "*"
    assert spec.name == "unnamed"
    assert spec.transport == "stdio"


def test_follow_pagination_and_cache_fields() -> None:
    spec = MCPProviderSpec.from_dict(
        {
            "name": "search",
            "url": "http://127.0.0.1:9001/mcp",
            "follow_pagination": False,
            "cache_ttl_ms": 1000,
            "cache_scope": "private",
        }
    )
    assert spec.follow_pagination is False
    assert spec.cache_ttl_ms == 1000
    assert spec.cache_scope == "private"


def test_follow_pagination_defaults_true() -> None:
    spec = MCPProviderSpec.from_dict({"name": "x", "command": "python"})
    assert spec.follow_pagination is True
    assert spec.cache_ttl_ms is None
    assert spec.cache_scope is None


def test_spec_is_frozen() -> None:
    spec = MCPProviderSpec.from_dict({"name": "x", "command": "python"})
    with pytest.raises(FrozenInstanceError):
        spec.name = "y"  # type: ignore[misc]


def test_env_prefix_on_secret_header_is_a_secret_ref_error() -> None:
    with pytest.raises(SecretRefError, match="SecretRef"):
        MCPProviderSpec.from_dict(
            {
                "name": "search",
                "url": "http://127.0.0.1:9001/mcp",
                "headers": {"Authorization": "env:MAS_TEST_MCP_HEADER"},
            }
        )


def test_env_prefix_on_stdio_env_is_a_secret_ref_error() -> None:
    with pytest.raises(SecretRefError, match="SecretRef"):
        MCPProviderSpec.from_dict(
            {
                "name": "search",
                "command": "python",
                "env": {"CHILD": "env:MAS_TEST_MCP_HEADER"},
            }
        )

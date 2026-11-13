#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP protocol adapters for MAS Lab.

The package contains the client/provider bridge, wire-contract conversions,
the production server factory, and the MCP Tasks extension. See the MCP
quickstart and reference documentation for registration and deployment.
"""

from library_ioa.plugins.mcp.contract import (
    as_mas_tool_result,
    as_mas_tool_spec,
    mas_tool_document_to_mcp,
)
from library_ioa.plugins.mcp.layer import McpLayer
from library_ioa.plugins.mcp.provider import MCPClientFactory, MCPToolProvider
from library_ioa.plugins.mcp.spec import MCPProviderSpec
from library_ioa.plugins.mcp.tasks import MCPTasksExtension

__all__ = [
    "MCPClientFactory",
    "MCPProviderSpec",
    "MCPToolProvider",
    "McpLayer",
    "MCPTasksExtension",
    "as_mas_tool_result",
    "as_mas_tool_spec",
    "mas_tool_document_to_mcp",
]

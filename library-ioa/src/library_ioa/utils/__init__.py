#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Helpers for MCP result/error shaping."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.comm.text import safe_text


def mcp_error_text(error: BaseException) -> str:
    """What a failed MCP call may say, to the model and in a log line.

    A JSON-RPC error the tool server answered is the tool's own output ("file not found") and is kept. A
    transport failure (an HTTP status error, a connection or TLS error) is its type plus
    ``<Type>: HTTP <status>`` or its text with urls cut to scheme, host and path: never a response body.
    """
    from mas.runtime.reliability.classify import safe_exception_text

    from library_ioa.plugins.mcp.client import McpError, first_error

    leaf = first_error(error)
    if isinstance(leaf, McpError):
        return safe_text(leaf)
    return safe_text(safe_exception_text(leaf))


def translate_mcp_error(error: Exception) -> dict[str, Any]:
    """Translate an MCP/SDK error into a MAS tool-error dict."""
    return {
        "status": "error",
        "error": mcp_error_text(error),
        "type": error.__class__.__name__,
        "is_error": True,
    }

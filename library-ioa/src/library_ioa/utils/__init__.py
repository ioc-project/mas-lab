#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Helpers for MCP result/error shaping."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.comm.text import safe_text


def translate_mcp_error(error: Exception) -> dict[str, Any]:
    """Translate an MCP/SDK error into a MAS tool-error dict."""
    return {
        "status": "error",
        "error": safe_text(error),
        "type": error.__class__.__name__,
        "is_error": True,
    }

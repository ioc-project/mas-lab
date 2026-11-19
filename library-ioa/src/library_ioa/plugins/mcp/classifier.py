#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP SDK span classifier. Hooks own tools/call and tools/list; other spans are internals."""

from __future__ import annotations

from mas.runtime.schema.transport import INTERNAL, SESSION
from mas.runtime.transport.classifier import (
    AliasTable,
    Decision,
    Drop,
    Emit,
    Passthrough,
    SpanView,
)

from library_ioa.plugins.mcp.aliases import ALIASES

SCOPE = "mcp-python-sdk"
SUPPORTED_RANGE = ">=2.2.0,<2.3"

_HOOKED = frozenset({"tools/call", "tools/list"})
# MCP span names carry the tool name ("tools/call <tool>"), so there is no fixed list to expect.
_MAPPED: tuple[str, ...] = ()


class MCPTransportClassifier:
    protocol = "mcp"
    sdk_distribution = "mcp"

    def scope_names(self) -> tuple[str, ...]:
        return (SCOPE,)

    def supported_sdk_range(self) -> str:
        return SUPPORTED_RANGE

    def mapped_span_names(self) -> tuple[str, ...]:
        return _MAPPED

    def aliases(self) -> AliasTable:
        return ALIASES

    def classify_start(self, span: SpanView) -> Decision:
        method = str(span.attributes.get("mcp.method.name") or "")
        if method in _HOOKED:
            return Drop()
        return Passthrough()

    def classify_end(self, span: SpanView) -> Decision:
        method = str(span.attributes.get("mcp.method.name") or "")
        if method in _HOOKED:
            return Drop()
        if method == "initialize":
            return Emit(
                {
                    "kind": SESSION,
                    "protocol": "mcp",
                    "side": "server" if span.kind == "SERVER" else "client",
                    "correlation_key": str(span.attributes.get("jsonrpc.request.id") or span.span_id),
                    "attributes": {"span_name": span.name, "method": method},
                }
            )
        if span.scope_name != SCOPE:
            return Passthrough()
        return Emit(
            {
                "kind": INTERNAL,
                "protocol": "mcp",
                "side": "server" if span.kind == "SERVER" else "client",
                "correlation_key": str(span.attributes.get("jsonrpc.request.id") or span.span_id),
                "attributes": {
                    "span_name": span.name,
                    "method": method,
                    "tool": str(span.attributes.get("gen_ai.tool.name") or ""),
                    "error": str(span.attributes.get("error.type") or ""),
                },
            }
        )


__all__ = ["MCPTransportClassifier", "SCOPE", "SUPPORTED_RANGE"]

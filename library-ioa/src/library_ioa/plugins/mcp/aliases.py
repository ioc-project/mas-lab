#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP native fields to observe-sdk names. ``mcp.message.*`` is not a KG event."""

from __future__ import annotations

from mas.runtime.schema.transport import DISCOVERY
from mas.runtime.transport.classifier import Alias, AliasTable

ALIASES = AliasTable(
    events={
        DISCOVERY: Alias(
            event="mcp.tools.discovered",
            attributes={"tools": "capability.content", "agent_id": "agent.name"},
        ),
        # mcp.message.* is INFORMATIONAL in SCOPE (the tool call is the fact).
    }
)


def alias_table() -> AliasTable:
    return ALIASES


__all__ = ["ALIASES", "alias_table"]

# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Small persistent MCP record for exercising request governance."""

import json
import sys
from pathlib import Path

from mcp.server import MCPServer

server = MCPServer("records")
state = Path(sys.argv[1])


@server.tool()
def records_write(value: str) -> str:
    """Save one record."""
    state.write_text(json.dumps({"value": value}))
    return "saved"


@server.tool()
def records_read() -> str:
    """Read the saved record."""
    return json.loads(state.read_text())["value"] if state.exists() else "empty"


if __name__ == "__main__":
    server.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[2]) if len(sys.argv) > 2 else 9200)

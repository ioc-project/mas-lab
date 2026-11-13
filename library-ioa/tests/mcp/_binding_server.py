# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Real MCP transport fixture with effects observable outside the client."""

import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

state = Path(sys.argv[1])
state.with_suffix(".started").write_text(str(os.getpid()))
server = MCPServer("records")


@server.tool()
def write(value: str) -> str:
    """Append one record."""
    with state.open("a") as output:
        output.write(value + "\n")
    return value


@server.tool()
def read() -> str:
    """Read the stored records."""
    return state.read_text() if state.exists() else "empty"


if __name__ == "__main__":
    if len(sys.argv) > 2:
        server.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[2]))
    else:
        server.run()

"""Tiny stdio MCP server for session-reuse tests: ``pid`` returns its process id."""

import os
import time

from mcp.server.mcpserver import MCPServer

server = MCPServer("pid-server")


@server.tool()
def pid() -> str:
    time.sleep(0.1)
    return str(os.getpid())


if __name__ == "__main__":
    server.run()

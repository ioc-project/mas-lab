#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Each MCP wrapper keeps its own loop thread."""

from __future__ import annotations

import threading

from library_ioa.plugins.mcp.client import MCPClient
from library_ioa.plugins.mcp.client.wrapper import MCPClientWrapper


def _mcp_loop_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "mcp-loop"]


def test_eight_wrappers_start_eight_mcp_loop_threads() -> None:
    # A session that another test opened keeps its loop thread for the life of the process: count only ours.
    before = set(_mcp_loop_threads())
    wrappers = [MCPClientWrapper(MCPClient(url="http://127.0.0.1:9/mcp")) for _ in range(8)]
    try:
        for wrapper in wrappers:
            wrapper._loop.ensure()
        assert len(set(_mcp_loop_threads()) - before) == 8
        loops = [wrapper._loop for wrapper in wrappers]
        assert len({id(loop) for loop in loops}) == 8
    finally:
        for wrapper in wrappers:
            wrapper.close()

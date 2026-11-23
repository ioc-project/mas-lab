#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The three calls whose request bytes must not change when no identity layer is named.

Each scenario drives today's code (MCP tool call, A2A HTTP send, one LLM vendor layer) against a
loopback fake and returns the normalised requests the fake saw. The goldens in ``golden/`` were recorded
from the tree before the identity layer existed; ``test_wiring_no_identity_golden`` compares to them.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from wiring_servers import FakeLlm, FakeMcp, normalise, serve_a2a

GOLDEN = Path(__file__).parent / "golden"


async def mcp_scenario() -> list[dict[str, Any]]:
    from library_ioa.plugins.mcp.provider import MCPToolProvider

    with FakeMcp() as server:
        spec = {"name": "demo", "protocol": "mcp", "url": server.url + "/mcp", "tools": ["echo"], "kind": "mcp"}
        provider = MCPToolProvider.from_provider_spec(spec)
        try:
            result = await provider.acall_tool("echo", {"q": 1})
            assert result["is_error"] is False
        finally:
            await asyncio.to_thread(provider.close)
        return normalise(server.seen, server.port)


async def a2a_scenario() -> list[dict[str, Any]]:
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm

    def handler(prompt: str, **_: Any) -> dict[str, str]:
        return {"text": f"peer:{prompt}"}

    with serve_a2a(handler) as peer:
        comm = A2AAgentComm(url=peer.url, timeout=10)
        try:
            assert await comm.asend("peer", "hello") == "peer:hello"
        finally:
            comm.stop()
        port = int(peer.url.rsplit(":", 1)[1])
        return normalise([s for s in peer.seen if s.method == "POST"], port)


async def llm_scenario() -> list[dict[str, Any]]:
    from library_ioa.plugins.http_layer import HttpCallTransport
    from mas.library.standard.plugins.llm.openai import OpenAILLMProvider

    with FakeLlm() as server:
        transport = HttpCallTransport(url=server.url + "/v1")
        provider = OpenAILLMProvider.wrap(
            transport,
            {"api_base": server.url + "/v1", "api_key": {"secret": "llm-key"}},
            call_kinds=("llm",),
        )
        try:
            out = await provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}])
            assert out["content"] == "ok"
        finally:
            transport.stop()
        return normalise(server.seen, server.port)


SCENARIOS = {"mcp": mcp_scenario, "a2a": a2a_scenario, "llm": llm_scenario}


def load_golden(name: str) -> Any:
    return json.loads((GOLDEN / f"{name}.json").read_text())

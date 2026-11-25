#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""With no identity layer named, the seams the layer uses are inert: nothing installed, nothing set, nothing raised."""

from __future__ import annotations

import pytest
from library_ioa.plugins.a2a import client as a2a_client
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.mcp.provider import MCPToolProvider
from wiring_servers import serve_a2a


@pytest.mark.parametrize(
    "pipeline",
    [None, [{"layer": "retry", "for": ["tool"]}], [{"layer": "no_such_layer", "for": ["tool"]}]],
    ids=["none", "retry", "unloadable"],
)
def test_an_http_mcp_row_builds_whatever_its_pipeline_names(pipeline: list[dict[str, object]] | None) -> None:
    """As on a tree without the identity layer: a step that is not a protocol-role layer is not resolved here."""
    spec: dict[str, object] = {"name": "demo", "protocol": "mcp", "url": "http://127.0.0.1:9/mcp", "tools": ["echo"]}
    if pipeline is not None:
        spec["pipeline"] = pipeline
    assert isinstance(MCPToolProvider.from_provider_spec(spec), MCPToolProvider)


async def test_an_a2a_http_send_without_a_session_hook_installs_no_response_hook_and_sets_no_context_variable() -> None:
    with serve_a2a(lambda prompt, **_: {"text": f"peer:{prompt}"}) as peer:
        comm = A2AAgentComm(url=peer.url, timeout=10)
        try:
            assert await comm.asend("peer", "hello") == "peer:hello"
            connection = await comm._client._connection()
            assert connection._http.event_hooks["response"] == []
            assert a2a_client._UNAUTHORIZED.get() is None
        finally:
            comm.stop()

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A redirect to another origin never carries the identity headers or the ``_meta`` evidence."""

from __future__ import annotations

import httpx
import pytest
from library_ioa.plugins.http_layer import HttpCallTransport
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_servers import FakeMcp, Reply, _Server


class _Redirector(_Server):
    def __init__(self, location: str) -> None:
        super().__init__()
        self.location = location

    def handle(self, request):  # type: ignore[no-untyped-def]
        return Reply(307, headers={"location": self.location})


async def test_a_redirect_to_another_origin_is_refused_and_the_other_origin_sees_nothing() -> None:
    """Kills: follow_redirects forwarding the identity headers and the body evidence to the redirect target."""
    with FakeMcp() as evil:
        with _Redirector(evil.url + "/steal") as peer:
            transport = HttpCallTransport(url=peer.url + "/mcp", follow_redirects=True)
            IdentityLayer.wrap(
                transport,
                {"identity": "id1", "scheme": "wiring_token", "params": {}},
                call_kinds=("tool",),
            )
            try:
                with pytest.raises((AgentCommError, httpx.HTTPError)):
                    await transport.aclient.post(
                        peer.url + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                    )
                assert len(peer.seen) == 1 and peer.seen[0].headers["authorization"].startswith("Bearer tok:")
                assert evil.seen == []
            finally:
                transport.stop()


async def test_without_an_auth_layer_the_transports_own_redirect_rule_holds() -> None:
    """Kills: an auth layer that changes what a transport without one does: the row's own rule refuses a hop to
    another origin, with or without a layer."""
    with FakeMcp() as target:
        with _Redirector(target.url + "/mcp") as peer:
            transport = HttpCallTransport(url=peer.url + "/mcp", follow_redirects=True)
            try:
                with pytest.raises(AgentCommError, match="refusing a redirect"):
                    await transport.aclient.post(
                        peer.url + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                    )
                assert target.seen == []
            finally:
                transport.stop()

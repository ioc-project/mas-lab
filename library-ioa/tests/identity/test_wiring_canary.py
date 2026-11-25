#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A secret configured in the scheme reaches the peer and nothing else: not a log, an error, a repr or a counter."""

from __future__ import annotations

import logging

import pytest
from wiring_builders import a2a_stack, chat, close, envelope, identity_step, key_ref, llm_provider, mcp_provider
from wiring_servers import FakeLlm, FakeMcp, Reply, serve_a2a

CANARY = "CANARY-9f2b7c1d-top-secret"


async def test_the_secret_is_only_ever_on_the_wire(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    shown: list[str] = []
    with (
        FakeMcp() as mcp,
        FakeLlm() as llm,
        FakeLlm(gate=lambda r: Reply(401, body={})) as refusing,
        serve_a2a(lambda p, **_: {"text": p}) as peer,
    ):
        provider = mcp_provider(mcp.url, identity_step("wiring_canary", {"key": key_ref(CANARY)}))
        try:
            shown.append(str(await provider.acall_tool("echo", {})))
            shown.append(repr(provider) + repr(provider._client))
        finally:
            await close(provider)
        for server in (llm, refusing):
            llm_p, transport = llm_provider(server.url, "wiring_canary", {"key": key_ref(CANARY)})
            try:
                try:
                    await chat(llm_p)
                except Exception as exc:
                    shown.append(repr(exc) + str(exc.__cause__))
                shown.append(repr(llm_p) + repr(transport) + repr(transport._auth))
            finally:
                transport.stop()
        stack, link, layer = a2a_stack(peer.url, "wiring_canary", {"key": key_ref(CANARY)})
        try:
            await stack.asend(envelope())
            shown.append(repr(layer) + repr(layer.caller) + repr(stack))
        finally:
            link.stop()
        # it did reach the peers, so the test could fail
        assert f"Bearer {CANARY}" in {s.headers.get("authorization") for s in mcp.posts()}
        assert f"Bearer {CANARY}" in {s.headers.get("authorization") for s in llm.posts()}
        assert f"Bearer {CANARY}" in {s.headers.get("authorization") for s in peer.sends()}
    assert CANARY not in "\n".join(shown)
    # the peer is in this process, so its own server logs (it is the one that is shown the credential) are not ours
    ours = [r.getMessage() for r in caplog.records if not r.name.startswith(("a2a.server", "uvicorn"))]
    assert CANARY not in "\n".join(ours)

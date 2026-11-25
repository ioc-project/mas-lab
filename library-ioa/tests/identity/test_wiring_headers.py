#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity layer on MCP, A2A HTTP and an LLM call: headers per (tool, principal), fail closed, one 401 retry."""

from __future__ import annotations

import re
from typing import Any

import pytest
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.lib.stack import Envelope, Layer, Stack
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_builders import a2a_stack, chat, close, envelope, identity_step, key_ref, llm_provider, mcp_provider
from wiring_schemes import Raising, TokenPerTask
from wiring_servers import FakeLlm, FakeMcp, Reply, serve_a2a


def _calls(server: FakeMcp) -> dict[str, str]:
    return {
        s.json()["params"]["name"] + "@" + s.headers["authorization"].split(":")[1]: s.headers["authorization"]
        for s in server.posts("tools/call")
    }


async def test_mcp_headers_follow_the_tool_and_the_principal() -> None:
    with FakeMcp() as server:
        provider = mcp_provider(server.url, identity_step("wiring_token", {"prefix": "p"}))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
                await provider.acall_tool("other", {})
            with bind({"actor": "agent-b"}):
                await provider.acall_tool("echo", {})
        finally:
            await close(provider)
        assert _calls(server) == {
            "echo@agent-a": "Bearer p:agent-a:tools/call:echo",
            "other@agent-a": "Bearer p:agent-a:tools/call:other",
            "echo@agent-b": "Bearer p:agent-b:tools/call:echo",
        }


async def test_a2a_http_headers_follow_the_principal() -> None:
    seen: list[str] = []
    with serve_a2a(lambda prompt, **_: {"text": f"peer:{prompt}"}) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_token", {"prefix": "p"})
        try:
            with bind({"actor": "agent-a"}):
                assert await stack.asend(envelope("one")) == "peer:one"
            with bind({"actor": "agent-b"}):
                assert await stack.asend(envelope("two")) == "peer:two"
        finally:
            link.stop()
        seen = [s.headers.get("authorization", "") for s in peer.sends()]
    # the peer declares one skill, so the operation is that skill
    assert seen == ["Bearer p:agent-a:peer", "Bearer p:agent-b:peer"]


async def test_llm_call_carries_the_evidence() -> None:
    with FakeLlm() as server:
        provider, transport = llm_provider(server.url, "wiring_token", {"prefix": "p"})
        try:
            with bind({"actor": "agent-a"}):
                await chat(provider)
        finally:
            transport.stop()
        assert server.posts()[0].headers["authorization"] == "Bearer p:agent-a:"


# --- fail closed -------------------------------------------------------------------------------------------


async def test_a_scheme_that_raises_sends_nothing_and_leaks_nothing_on_every_path() -> None:
    errors: list[str] = []
    with FakeMcp() as mcp, FakeLlm() as llm, serve_a2a(lambda p, **_: {"text": p}) as peer:
        provider = mcp_provider(mcp.url, identity_step("wiring_raising"))
        try:
            result = await provider.acall_tool("echo", {})
            errors.append(str(result))
        finally:
            await close(provider)
        llm_p, transport = llm_provider(llm.url, "wiring_raising")
        try:
            with pytest.raises(Exception) as caught:
                await chat(llm_p)
            errors.append(repr(caught.value) + repr(caught.value.__cause__))
        finally:
            transport.stop()
        stack, link, _ = a2a_stack(peer.url, "wiring_raising")
        try:
            with pytest.raises(AgentCommError) as caught_a:
                await stack.asend(envelope())
            errors.append(repr(caught_a.value))
            assert re.fullmatch(r"identity: unavailable", str(caught_a.value))
        finally:
            link.stop()
        assert mcp.seen == [] and llm.seen == [] and peer.sends() == []
    assert Raising.SECRET not in "".join(errors)


@pytest.mark.parametrize(
    "evidence",
    [
        {"headers": {"x-a": "ok\r\nx-injected: 1"}},
        {"headers": {"x-a": "v"}, "native": {"tls": {}}},
    ],
    ids=["crlf", "unknown-native"],
)
async def test_bad_evidence_is_a_denial_and_nothing_is_sent(evidence: dict[str, Any]) -> None:
    with FakeLlm() as server:
        provider, transport = llm_provider(server.url, "scripted", {"evidence": [evidence], "repeat_last": True})
        try:
            with pytest.raises(Exception, match="identity: invalid"):
                await chat(provider)
        finally:
            transport.stop()
        assert server.seen == []


# --- one retry on 401 ----------------------------------------------------------------------------------------


def _gate_challenge(request: Any) -> Reply | None:
    if request.headers.get("authorization", "").startswith("Bearer chal:"):
        return None
    return Reply(401, headers={"WWW-Authenticate": 'Bearer realm="r"'}, body={"error": "body-must-not-reach-scheme"})


async def test_a_401_is_answered_once_with_a_challenge_evidence() -> None:
    with FakeLlm(gate=_gate_challenge) as server:
        provider, transport = llm_provider(server.url, "wiring_token", {"prefix": "p"})
        try:
            with bind({"actor": "agent-a"}):
                out = await chat(provider)
        finally:
            transport.stop()
        assert out["content"] == "ok"
        assert [s.headers["authorization"] for s in server.posts()] == ["Bearer p:agent-a:", "Bearer chal:agent-a"]
    challenge = [c for c in TokenPerTask.CALLS if c.get("phase") == "challenge"]
    assert len(challenge) == 1
    assert challenge[0]["peer"] == {
        "challenge": {"realm": "r"},
        "wire": 'Bearer realm="r"',
        "transport": {"status": 401, "headers": {"www-authenticate": 'Bearer realm="r"'}},
    }


async def test_the_401_retry_publishes_an_identity_challenge_event() -> None:
    from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream

    stream, seen = EventStream(), []
    stream.subscribe(seen.append)
    token = set_event_stream(stream)
    try:
        with FakeLlm(gate=_gate_challenge) as server:
            provider, transport = llm_provider(server.url, "wiring_token", {"prefix": "p"})
            try:
                with bind({"actor": "agent-a"}):
                    await chat(provider)
            finally:
                transport.stop()
        with FakeLlm(gate=lambda r: Reply(401, headers={"WWW-Authenticate": "Bearer"}, body={})) as refusing:
            provider, transport = llm_provider(refusing.url, "wiring_token", {"prefix": "p"})
            try:
                with pytest.raises(Exception, match="refused:unauthorized"):
                    await chat(provider)
            finally:
                transport.stop()
    finally:
        reset_event_stream(token)
    answered = [e for e in seen if e["kind"] == "identity.challenge"]
    assert [(e["direction"], e["outcome"], e.get("code")) for e in answered] == [
        ("answered", "ok", None),
        ("answered", "denied", "refused:unauthorized"),
    ]
    assert all(e["scheme"] == "wiring_token" and e["record"] == "id1" and e["protocol"] == "llm" for e in answered)


async def test_a_second_401_is_a_denial_and_does_not_loop() -> None:
    with FakeLlm(gate=lambda r: Reply(401, headers={"WWW-Authenticate": "Bearer"}, body={})) as server:
        provider, transport = llm_provider(server.url, "wiring_token")
        try:
            with pytest.raises(Exception, match="identity: refused:unauthorized"):
                await chat(provider)
        finally:
            transport.stop()
        assert len(server.posts()) == 2


# --- advertise: each adapter carries only the sections it knows ---------------------------------------------


KEY = "k" * 16
PE_HOLDER = {"key": key_ref(KEY), "subject": "holder-1", "claims": {"tier": "gold"}}


async def test_a2a_extension_uris_the_scheme_advertises_are_declared_on_the_wire() -> None:
    """Kills: an A2A adapter that drops ``advertise`` (the peer never learns the extension)."""
    with serve_a2a(lambda p, **_: {"text": p}) as peer:
        stack, link, _ = a2a_stack(peer.url, "presentation_exchange", PE_HOLDER)
        try:
            with bind({"actor": "agent-a"}):
                await stack.asend(envelope("hi"))
        finally:
            link.stop()
        (seen,) = peer.sends()
    assert seen.headers["a2a-extensions"] == "urn:example:presentation-exchange"


async def test_a_section_the_adapter_does_not_carry_is_refused_and_nothing_is_sent() -> None:
    """Kills: an adapter that sends evidence declaring a section it cannot carry (an MCP section on A2A)."""
    with serve_a2a(lambda p, **_: {"text": p}) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_pe_mcp_section", PE_HOLDER)
        try:
            with pytest.raises(AgentCommError, match="advertise_section_unsupported"):
                await stack.asend(envelope("hi"))
        finally:
            link.stop()
        assert peer.sends() == []


class _MetadataOnlyBottom(Layer):
    """Stands in for a2a-rpc over slim: only message metadata crosses (``WIRE_HEADERS`` is False)."""

    needs_lower = False
    WIRE_HEADERS = False
    endpoint = "slim://peer"

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[Envelope] = []

    async def asend(self, envelope: Envelope) -> Any:
        self.seen.append(envelope)
        return "ok"


class _PassThrough(Layer):
    """Any ordinary layer between the identity layer and the transport (retry, logging, ...)."""


@pytest.mark.parametrize("between", [0, 1, 2])
async def test_headers_go_to_metadata_whatever_sits_between_identity_and_the_transport(between: int) -> None:
    bottom = _MetadataOnlyBottom()
    identity = IdentityLayer(identity="id1", scheme="wiring_bearer", params={"token": "t"}, agent_id="a")
    await Stack([bottom, *[_PassThrough() for _ in range(between)], identity]).asend(
        Envelope(payload="p", meta={"target": "x"})
    )
    (sent,) = bottom.seen
    assert "authorization" in sent.meta["metadata"]["mas.headers"]
    assert "authorization" not in sent.headers


async def test_a2a_http_resend_after_a_401_keeps_the_message_id() -> None:
    """A verifier binds a challenge to the message: the answered resend must be the same message."""
    refused: list[bool] = []

    def gate(seen: Any) -> Reply | None:
        if seen.rpc_method() == "SendMessage" and not refused:
            refused.append(True)
            return Reply(401, headers={"WWW-Authenticate": "Bearer"}, body={})
        return None

    with serve_a2a(lambda prompt, **_: {"text": f"peer:{prompt}"}, gate=gate) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_token", {"prefix": "p"})
        try:
            with bind({"actor": "agent-a"}):
                assert await stack.asend(envelope("one")) == "peer:one"
        finally:
            link.stop()
        ids = [s.json()["params"]["message"]["messageId"] for s in peer.sends()]
    assert len(ids) == 2
    assert ids[0] == ids[1]

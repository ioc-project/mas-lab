#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Value-free events at the identity boundary: which fire, with which fields, and that no secret is in them."""

from __future__ import annotations

import json
from typing import Any

import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.lib.stack import Envelope, PeerChallenge, Stack
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.library.standard.plugins.identity.layer import IdentityLayer
from test_wiring_inbound import AUD, KEY
from wiring_builders import a2a_stack, close, envelope, identity_step, key_ref, mcp_provider
from wiring_servers import FakeMcp, serve_a2a

CANARY = "CANARY-9f2b7c1d-top-secret"


def _kinds(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["kind"] == kind]


async def test_present_events_carry_the_fields_and_never_the_credential(events: list[dict[str, Any]]) -> None:
    """Kills: an event that shows the evidence, the header, or more of the URL than its host."""
    with FakeMcp() as server:
        provider = mcp_provider(server.url, identity_step("wiring_canary", {"key": key_ref(CANARY)}))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
                await provider.acall_tool("echo", {})
        finally:
            await close(provider)
        host = f"127.0.0.1:{server.port}"
    presents = _kinds(events, "identity.present")
    assert presents and all(e["scheme"] == "wiring_canary" and e["record"] == "id1" for e in presents)
    assert {e["protocol"] for e in presents} == {"mcp"} and {e["host"] for e in presents} == {host}
    assert all(e["outcome"] == "ok" and e["phase"] == "initiate" and e["latency_ms"] >= 0 for e in presents)
    assert all(set(e) <= observe.FIELDS | set(observe.COMMON) for e in events)
    assert CANARY not in json.dumps(events) and "Bearer" not in json.dumps(events)


async def test_a_denied_present_says_the_code_and_nothing_is_sent(events: list[dict[str, Any]]) -> None:
    with FakeMcp() as server:
        provider = mcp_provider(server.url, identity_step("wiring_raising"))
        try:
            await provider.acall_tool("echo", {})
        finally:
            await close(provider)
    denied = [e for e in _kinds(events, "identity.present") if e["outcome"] == "denied"]
    assert denied and all(e["code"] == "unavailable" for e in denied)
    assert "secret" not in json.dumps(events).lower()


async def test_verify_and_challenge_events_hash_the_caller_and_name_no_nonce(events: list[dict[str, Any]]) -> None:
    layer = IdentityLayer(
        identity="srv",
        scheme="presentation_exchange",
        params={"key": key_ref(KEY), "subject": "verifier", "requirements": ["tier"], "audience": AUD},
        agent_id="srv-agent",
        audience={"protocol": "a2a", "target": AUD},
    )

    async def handler(_envelope: Envelope) -> str:
        raise AssertionError("the handler must not run")

    asked = Envelope(payload="hi", meta={"protocol": "a2a", "context_id": "ctx-1", "message_id": "msg-1"})
    with pytest.raises(PeerChallenge) as refused:
        await layer.serve(handler)(asked)
    nonce = refused.value.challenge["nonce"]
    verifies = _kinds(events, "identity.verify")
    assert [e["outcome"] for e in verifies] == ["challenged"] and verifies[0]["code"]
    assert "session" not in verifies[0]
    assert [e["direction"] for e in _kinds(events, "identity.challenge")] == ["issued"]
    assert nonce not in json.dumps(events)


async def test_a_revocation_publishes_one_event_with_the_record(events: list[dict[str, Any]]) -> None:
    record = {"id": "id1", "scheme": "signed_badge", "params": {"key": key_ref(KEY), "subject": "alice"}}
    await IdentityLayer(identity="id1", scheme="signed_badge", params=record["params"]).caller.revoke("badge")
    (revoked,) = _kinds(events, "identity.revoke")
    assert (revoked["record"], revoked["scheme"], revoked["source"]) == ("id1", "signed_badge", "identity")
    assert {"ts", "event_id"} <= set(revoked)


async def test_no_event_without_an_identity_layer(events: list[dict[str, Any]]) -> None:
    """Kills: telemetry that runs on a stack that has no identity configured."""
    with serve_a2a(lambda p, **_: {"text": p}) as peer:
        link = A2AAgentComm(url=peer.url, timeout=10)
        try:
            await Stack([LinkLayer(link)]).asend(envelope("hi"))
        finally:
            link.stop()
    assert events == []


async def test_a_subscriber_sees_exactly_what_the_stream_publishes(events: list[dict[str, Any]]) -> None:
    """The events go through the process event stream: its subscribers (a redactor, a UI) are the ones that apply."""
    with serve_a2a(lambda p, **_: {"text": p}) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_token", {"prefix": "p"})
        try:
            with bind({"actor": "agent-a"}):
                await stack.asend(envelope("hi"))
        finally:
            link.stop()
    assert [e["kind"] for e in events] == ["identity.present"]
    assert all(set(e) <= observe.FIELDS | set(observe.COMMON) for e in events)

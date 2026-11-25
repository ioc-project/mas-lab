#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity layer on a stack: build, share, attach, send, answer one challenge, serve, fail closed.

Everything runs against fakes: a lower layer that records what it is sent, a call transport that records what is
installed on it, and a scripted wallet. No network, no clock, no sleep.
"""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
import pytest
from mas.library.standard.lib.identity import holders, observe
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAP_SIGN,
    CAPABILITIES,
    ChallengeRequired,
    Evidence,
    HandshakeContext,
    IdentityError,
    Verified,
)
from mas.library.standard.lib.stack import (
    SESSION_HOOK_META,
    AuthorizableTransport,
    Envelope,
    Layer,
    PeerChallenge,
    SessionInfo,
    Unauthenticated,
)
from mas.library.standard.lib.stack.owner import conversation_id
from mas.library.standard.plugins.identity.layer import (
    EXTENSIONS_HEADER,
    HEADERS_KEY,
    IdentityLayer,
    lookup_scheme,
    owner_key,
    reset_shared,
)
from mas.library.standard.testing.identity_fakes import FakeChannel, bound_secrets
from mas.library.standard.testing.identity_scripted import Scripted
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.secrets import SecretRef


class Plain(Scripted):
    """Presents and handshakes; neither signs nor verifies."""

    scheme: ClassVar[str] = "plain"
    capabilities: ClassVar[frozenset[str]] = CAPABILITIES - {CAP_DELEGATE, CAP_SIGN, "verify"}


class Signing(Scripted):
    scheme: ClassVar[str] = "signing"
    capabilities: ClassVar[frozenset[str]] = CAPABILITIES - {CAP_DELEGATE}


class Broken(Scripted):
    scheme: ClassVar[str] = "broken"

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Broken:
        raise RuntimeError("canary-in-the-exception")


SCHEMES = {"plain": Plain, "signing": Signing, "broken": Broken, "scripted": Scripted}


@pytest.fixture(autouse=True)
def _clean() -> Any:
    reset_shared()
    holders.reset()
    yield
    reset_shared()
    holders.reset()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observe, "publish_event", seen.append)
    monkeypatch.setattr(observe, "emit_to_channel", lambda event: None)
    return seen


def make(scheme: str = "plain", evidence: list[Evidence] | None = None, **kw: Any) -> IdentityLayer:
    params = {"evidence": evidence if evidence is not None else [{"headers": {"authorization": "Bearer t"}}]}
    params["repeat_last"] = True
    return IdentityLayer(identity="rec", scheme=scheme, params=params, schemes=SCHEMES, **kw)


def scripted_of(layer: IdentityLayer) -> Scripted:
    return layer.caller.scheme  # type: ignore[return-value]


class Lower(Layer):
    """The bottom of a send stack: records what it is sent, answers from a script."""

    needs_lower = False

    def __init__(self, *replies: Any, endpoint: str = "https://peer.example", wire_headers: bool = True) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.WIRE_HEADERS = wire_headers
        self.replies = list(replies) or ["ok"]
        self.sent: list[Envelope] = []
        self.facts: Mapping[str, Any] = {}

    async def asend(self, envelope: Envelope) -> Any:
        self.sent.append(envelope)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def peer_facts(self, envelope: Envelope) -> Mapping[str, Any]:
        return self.facts


def stacked(layer: IdentityLayer, lower: Lower) -> IdentityLayer:
    layer.bind(lower)
    return layer


def send(layer: IdentityLayer, payload: Any = "hello", **meta: Any) -> Any:
    return asyncio.run(layer.asend(Envelope(payload=payload, meta=meta)))


# --- building --------------------------------------------------------------------------------------


def test_an_unknown_parameter_or_scope_is_refused_by_name() -> None:
    with pytest.raises(AgentCommError, match="unknown parameter 'audiance'"):
        IdentityLayer(identity="r", scheme="plain", schemes=SCHEMES, audiance={})
    with pytest.raises(AgentCommError, match="scope is agent or process"):
        IdentityLayer(identity="r", scheme="plain", schemes=SCHEMES, scope="everyone")


def test_an_unknown_scheme_bad_params_and_a_scheme_that_cannot_be_built_say_so_without_values() -> None:
    with pytest.raises(AgentCommError, match="unknown scheme 'nope'"):
        IdentityLayer(identity="r", scheme="nope", schemes=SCHEMES)
    with pytest.raises(AgentCommError, match=r"params are invalid \(1 problems\)"):
        IdentityLayer(identity="r", scheme="plain", params={"evidence": "text"}, schemes=SCHEMES)
    with pytest.raises(AgentCommError) as err:
        IdentityLayer(identity="r", scheme="broken", schemes=SCHEMES)
    assert "RuntimeError" in str(err.value) and "canary" not in str(err.value) and err.value.__cause__ is None


def test_the_registry_finds_a_scheme_by_name_and_not_an_unknown_one() -> None:
    assert lookup_scheme("x", {"x": Plain}) is Plain
    assert lookup_scheme("no-such-scheme-anywhere") is None


def test_a_missing_secret_stops_the_start_with_one_value_free_event(events: list[dict[str, Any]]) -> None:
    class Keyed(Scripted):
        PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}

    with pytest.raises(AgentCommError, match="missing_secret"):
        IdentityLayer(
            identity="rec",
            scheme="keyed",
            params={"key": SecretRef(name="absent")},
            schemes={"keyed": Keyed},
            agent_id="desk",
        )
    (event,) = [e for e in events if e["kind"] == "identity.startup"]
    assert (event["outcome"], event["code"], event["record"], event["scheme"], event["actor"]) == (
        "denied",
        "refused:missing_secret",
        "rec",
        "keyed",
        "desk",
    )
    with bound_secrets({"present": "v"}):  # an optional ref may be absent, a present one is fine
        IdentityLayer(
            identity="rec",
            scheme="keyed",
            params={"a": SecretRef(name="present"), "b": SecretRef(name="gone", optional=True)},
            schemes={"keyed": Keyed},
        )


# --- sharing and holding wallets -------------------------------------------------------------------


def test_layers_of_one_agent_bind_and_parameters_share_one_wallet_and_others_do_not() -> None:
    same = [make(agent_id="a", holder_scope="run-1"), make(agent_id="a", holder_scope="run-1")]
    assert same[0].caller is same[1].caller
    assert make(agent_id="a", holder_scope="run-2").caller is not same[0].caller
    assert make(agent_id="b", holder_scope="run-1").caller is not same[0].caller
    assert make("plain", [{"headers": {"x": "1"}}], agent_id="a", holder_scope="run-1").caller is not same[0].caller


def test_a_process_wallet_is_one_for_every_agent_and_held_by_none() -> None:
    one = make(agent_id="a", scope="process", holder_scope="r")
    two = make(agent_id="b", scope="process", holder_scope="r")
    assert one.caller is two.caller
    assert holders.records_of("a", "r") == [] and holders.records_of("", "r") == ["rec"]


def test_a_started_layer_is_held_by_its_agent_until_it_stops() -> None:
    layer = make(agent_id="desk", holder_scope="r")
    assert holders.records_of("desk", "r") == ["rec"] and holders.wallets("desk", "rec", "r") == [layer.caller]
    layer.stop()
    layer.stop()
    assert holders.records_of("desk", "r") == []


def test_a_revocation_closes_the_sessions_of_every_layer_that_presents_with_the_wallet() -> None:
    layer = make(agent_id="desk")
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    closed: list[int] = []
    transport.hooks[0].on_invalidate(lambda: closed.append(1))
    layer.caller.notify_revoked("scripted")
    assert closed == [1]


def test_a_layer_that_is_collected_leaves_no_listener_on_a_wallet_that_outlives_it() -> None:
    first = make(agent_id="desk", holder_scope="r")
    wallet = first.caller
    first_hooks = first._hooks
    del first, first_hooks
    gc.collect()
    wallet.notify_revoked("scripted")  # nothing to call: the finalizer took the listener away
    assert wallet.is_revoked("scripted")


# --- attaching to a call transport -----------------------------------------------------------------


class FakeTransport(AuthorizableTransport):
    def __init__(self, url: str, names: tuple[str, ...] = ()) -> None:
        self.url = url
        self._names = names
        self.auth: httpx.Auth | None = None
        self.guard: Any = None
        self.hooks: list[Any] = []

    def use_auth(self, auth: httpx.Auth, *, guard: Any = None) -> None:
        self.auth, self.guard = auth, guard

    def add_session_hook(self, hook: Any) -> None:
        self.hooks.append(hook)

    def static_header_names(self) -> tuple[str, ...]:
        return self._names


def test_a_tool_transport_gets_the_auth_the_guard_and_a_session_hook() -> None:
    layer = make(agent_id="desk", audience={"protocol": "mcp", "target": "https://row.example"})
    transport = FakeTransport("http://a.test:9000/mcp", names=("authorization",))
    layer.attach(transport, call_kinds=("tool",))
    assert transport.auth is not None and transport.guard == transport.auth.guard_request
    (hook,) = transport.hooks
    assert hook.scope()  # a scope per acting principal
    assert transport.auth._static == frozenset({"authorization"})


def test_an_llm_transport_gets_the_auth_alone() -> None:
    layer = make()
    transport = FakeTransport("http://llm.test/v1")
    layer.attach(transport, call_kinds=("llm",))
    assert transport.auth is not None and transport.hooks == []


def test_wrap_installs_the_layer_on_an_http_transport_and_refuses_any_other() -> None:
    transport = FakeTransport("http://a.test/mcp")
    params = {
        "identity": "rec",
        "scheme": "plain",
        "params": {"evidence": [{"headers": {"a": "b"}}]},
        "schemes": SCHEMES,
    }
    assert IdentityLayer.wrap(transport, params, call_kinds=("tool",)) is transport and transport.auth is not None
    with pytest.raises(AgentCommError, match="needs an http transport"):
        IdentityLayer.wrap(object(), params)


def test_the_hook_of_a_held_session_keeps_what_it_began_with_for_the_request_that_opens_it() -> None:
    layer = make(
        agent_id="desk",
        evidence=[{"headers": {"authorization": "Bearer b"}, "advertise": {"a2a.extensions": ["urn:x"]}}],
    )
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    (hook,) = transport.hooks
    advertised = asyncio.run(hook.begin(frozenset({"a2a.extensions", "unknown.section"}), "initialize"))
    assert advertised == {"a2a.extensions": ["urn:x"]}
    kept = hook.take_begun("initialize")
    assert kept is not None and kept["headers"] == {"authorization": "Bearer b"}
    assert hook.take_begun("initialize") is None  # once


def test_begun_evidence_past_its_own_expiry_is_asked_for_again() -> None:
    now = [100.0]
    scheme = Plain([Evidence(headers={"a": "b"}, valid_until=150.0)], repeat_last=True)
    layer = IdentityLayer(identity="rec", scheme="plain", caller=Caller("rec", scheme, clock=lambda: now[0]))
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    (hook,) = transport.hooks
    asyncio.run(hook.begin(frozenset(), "initialize"))
    asyncio.run(hook.begin(frozenset(), "tools/list"))
    assert hook.take_begun("initialize") is not None  # still inside its life
    now[0] = 200.0
    assert hook.take_begun("tools/list") is None  # the credential ended while it waited


def test_a_hook_whose_wallet_refuses_to_present_fails_closed_without_a_value() -> None:
    layer = make(agent_id="desk", evidence=[])
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    (hook,) = transport.hooks
    with pytest.raises(AgentCommError, match="no_credential"):
        asyncio.run(hook.begin(frozenset(), "initialize"))


def test_a_refusal_is_kept_for_the_call_that_made_it_and_for_no_other() -> None:
    layer = make(agent_id="desk")
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    (hook,) = transport.hooks
    refusal = AgentCommError("identity: refused:redirect")
    hook.refused("tools/call:echo", refusal)
    assert hook.take_refusal("tools/call:other") is None
    assert hook.take_refusal("tools/call:echo") is refusal and hook.take_refusal("tools/call:echo") is None
    hook.refused("initialize", refusal)  # a setup request shares one key with initialize and the notifications
    assert hook.take_refusal("notifications/initialized") is refusal


class Session(SessionInfo):
    def __init__(self, peer: Mapping[str, Any], replies: list[Any] | None = None) -> None:
        self.peer = peer
        self.channel = FakeChannel(replies or [])

    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]:
        return await self.channel.request(method, params, data_key=data_key)


def test_an_established_session_runs_the_handshake_and_returns_the_meta_for_later_calls() -> None:
    class Shaking(Plain):
        async def handshake(self, ctx: HandshakeContext) -> Evidence:
            self.handshake_contexts.append(ctx)
            return Evidence(meta={"io.t/session": "s-1"})

    layer = IdentityLayer(identity="rec", scheme="shaking", params={}, schemes={"shaking": Shaking}, agent_id="desk")
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    (hook,) = transport.hooks
    meta = asyncio.run(hook.established(Session({"serverInfo": {"name": "s"}})))
    assert meta == {"io.t/session": "s-1"}
    ctx = scripted_of(layer).handshake_contexts[0]
    assert ctx["audience"] == {"protocol": "mcp", "target": "http://a.test"} and ctx["peer"]["serverInfo"] == {
        "name": "s"
    }


def test_a_session_of_a_wallet_that_cannot_handshake_has_nothing_to_settle() -> None:
    class NoShake(Plain):
        capabilities: ClassVar[frozenset[str]] = frozenset()

    layer = IdentityLayer(identity="rec", scheme="noshake", params={}, schemes={"noshake": NoShake})
    transport = FakeTransport("http://a.test/mcp")
    layer.attach(transport, call_kinds=("tool",))
    assert asyncio.run(transport.hooks[0].established(Session({}))) == {}


# --- send stacks -----------------------------------------------------------------------------------


def test_a_send_carries_the_evidence_as_headers_and_names_the_message_once() -> None:
    lower = Lower()
    layer = stacked(
        make(evidence=[{"headers": {"authorization": "Bearer t"}, "meta": {"io.t/x": 1}}], agent_id="desk"), lower
    )
    assert send(layer, "hi", intent="ask") == "ok"
    (sent,) = lower.sent
    assert sent.headers["authorization"] == "Bearer t" and sent.meta["metadata"] == {"io.t/x": 1}
    assert sent.meta["message_id"] and sent.meta["context_id"] and sent.meta[SESSION_HOOK_META] is not None
    req = scripted_of(layer).requests[0]
    assert req["audience"] == {"protocol": "a2a", "target": "https://peer.example"}
    assert req["task"] == "ask" and req["request"]["body_text"] == "hi" and req["principal"]["actor"] == "desk"


def test_a_message_that_already_has_ids_keeps_them_and_a_row_audience_wins() -> None:
    lower = Lower()
    layer = stacked(make(audience={"protocol": "a2a", "target": "https://row.example"}), lower)
    send(layer, "hi", message_id="m-1", context_id="c-1", url="https://other.example")
    (sent,) = lower.sent
    assert (sent.meta["message_id"], sent.meta["context_id"]) == ("m-1", "c-1")
    assert scripted_of(layer).requests[0]["audience"]["target"] == "https://row.example"


def test_the_target_comes_from_the_message_when_the_layer_below_names_none() -> None:
    lower = Lower(endpoint="")
    layer = stacked(make(), lower)
    send(layer, "hi", url="https://by-url.example")
    assert scripted_of(layer).requests[0]["audience"]["target"] == "https://by-url.example"
    send(layer, "hi", target="by-target")
    assert scripted_of(layer).requests[1]["audience"]["target"] == "by-target"


def test_headers_cross_as_message_metadata_over_a_transport_with_no_http() -> None:
    lower = Lower(wire_headers=False)
    layer = stacked(make(evidence=[{"headers": {"authorization": "Bearer t"}}]), lower)
    send(layer, "hi")
    (sent,) = lower.sent
    assert dict(sent.headers) == {} and sent.meta["metadata"][HEADERS_KEY] == {"authorization": "Bearer t"}


def test_the_extensions_a_scheme_advertises_go_in_the_extensions_header() -> None:
    lower = Lower()
    layer = stacked(make(evidence=[{"advertise": {"a2a.extensions": ["urn:a", "urn:b"]}}]), lower)
    send(layer, "hi")
    assert lower.sent[0].headers[EXTENSIONS_HEADER] == "urn:a, urn:b"


def test_the_one_skill_a_peer_declares_names_the_operation_and_several_leave_it_to_the_intent() -> None:
    lower = Lower()
    layer = stacked(make(), lower)
    lower.facts = {"skills": ["pay"]}
    send(layer, "hi", intent="ask")
    lower.facts = {"skills": ["pay", "refund"]}
    send(layer, "hi", intent="ask")
    send(layer, "hi", intent="ask", task="named")
    assert [r["task"] for r in scripted_of(layer).requests] == ["pay", "ask", "named"]
    assert scripted_of(layer).requests[0]["peer"] == {"skills": ["pay"]}


def test_a_scheme_that_wraps_the_text_replaces_the_payload_and_a_signing_one_signs_the_wire_form() -> None:
    lower = Lower()
    layer = IdentityLayer(
        identity="rec",
        scheme="signing",
        params={"evidence": [{"payload": "WRAPPED hi", "headers": {"x-a": "1"}}], "repeat_last": True},
        schemes=SCHEMES,
    )
    scripted_of(layer)._signs.append(Evidence(headers={"signature": "s-1"}, payload="SIGNED hi"))
    stacked(layer, lower)
    send(layer, "hi")
    (sent,) = lower.sent
    assert sent.payload == "SIGNED hi" and sent.headers["signature"] == "s-1" and sent.headers["x-a"] == "1"
    (signed,) = scripted_of(layer).sign_requests
    view = signed["request"]
    assert view["method"] == "SendMessage" and view["body_text"] == "WRAPPED hi" and len(view["body_sha256"]) == 64
    assert view["headers"] == {"x-a": "1"}


def test_a_signed_structured_payload_is_signed_over_its_canonical_form() -> None:
    lower = Lower()
    layer = IdentityLayer(
        identity="rec",
        scheme="signing",
        params={"evidence": [{"headers": {"x-a": "1"}}], "repeat_last": True},
        schemes=SCHEMES,
    )
    scripted_of(layer)._signs.append(Evidence(headers={"signature": "s"}))
    stacked(layer, lower)
    send(layer, {"b": 1, "a": [2]})
    (signed,) = scripted_of(layer).sign_requests
    assert "body_text" not in signed["request"] and len(signed["request"]["body_sha256"]) == 64


def test_a_peer_challenge_is_answered_once_with_the_same_message_and_the_answer_is_an_event(
    events: list[dict[str, Any]],
) -> None:
    lower = Lower(Unauthenticated({"nonce": "n-1"}, wire='Bearer nonce="n-1"'), "done")
    layer = stacked(
        make(
            evidence=[{"headers": {"authorization": "first"}}, {"headers": {"authorization": "answer"}}],
            agent_id="desk",
        ),
        lower,
    )
    assert send(layer, "hi") == "done"
    first, again = lower.sent
    assert first.headers["authorization"] == "first" and again.headers["authorization"] == "answer"
    assert first.meta["message_id"] == again.meta["message_id"] and first.meta["context_id"] == again.meta["context_id"]
    init, answer = scripted_of(layer).requests
    assert answer["phase"] == "challenge" and answer["peer"] == {
        "challenge": {"nonce": "n-1"},
        "wire": 'Bearer nonce="n-1"',
    }
    (event,) = [e for e in events if e["kind"] == "identity.challenge"]
    assert (event["outcome"], event["direction"], event["protocol"], event["actor"]) == (
        "ok",
        "answered",
        "a2a",
        "desk",
    )


def test_a_peer_that_refuses_the_answer_too_is_a_denial_and_the_message_is_not_sent_a_third_time(
    events: list[dict[str, Any]],
) -> None:
    lower = Lower(Unauthenticated({}), Unauthenticated({}))
    layer = stacked(make(), lower)
    with pytest.raises(AgentCommError, match="unauthorized"):
        send(layer, "hi")
    assert len(lower.sent) == 2
    (event,) = [e for e in events if e["kind"] == "identity.challenge"]
    assert (event["outcome"], event["code"]) == ("denied", "refused:unauthorized")


def test_a_challenge_for_another_audience_is_not_answered_and_nothing_more_is_sent() -> None:
    lower = Lower(Unauthenticated({"nonce": "n", "audience": "https://third.example"}))
    layer = stacked(make(), lower)
    with pytest.raises(AgentCommError, match="wrong_audience"):
        send(layer, "hi")
    assert len(lower.sent) == 1


def test_a_wallet_that_cannot_present_stops_the_send_before_anything_leaves() -> None:
    lower = Lower()
    layer = stacked(make(evidence=[]), lower)
    with pytest.raises(AgentCommError, match="no_credential"):
        send(layer, "hi")
    assert lower.sent == []
    revoked = stacked(make(evidence=[{"headers": {"a": "b"}}], agent_id="r"), Lower())
    revoked.caller.notify_revoked("scripted")
    with pytest.raises(AgentCommError, match="revoked"):
        send(revoked, "hi")


def test_an_unbound_layer_is_a_stack_error_not_a_crash() -> None:
    with pytest.raises(Exception, match="unbound"):
        send(make(), "hi")


# --- serving ---------------------------------------------------------------------------------------


def verifying(verified: Verified | Exception | None = None, **kw: Any) -> IdentityLayer:
    layer = IdentityLayer(identity="rec", scheme="scripted", params={}, schemes=SCHEMES, **kw)
    scripted_of(layer)._verified = verified
    return layer


GOOD = Verified(subject="alice", claims={"iss": "https://idp.example"}, source="token")


async def _handler(envelope: Envelope) -> Any:
    return envelope


def serve(layer: IdentityLayer, envelope: Envelope) -> Envelope:
    return asyncio.run(layer.serve(_handler)(envelope))


def test_a_layer_whose_wallet_cannot_verify_leaves_inbound_requests_alone() -> None:
    layer = make()
    assert layer.wraps_inbound is False and layer.serve(_handler) is _handler


def test_a_verified_caller_reaches_the_handler_as_a_principal_and_an_owned_conversation() -> None:
    layer = verifying(
        Verified(
            subject="alice",
            claims={"iss": "https://idp"},
            source="token",
            actors=["svc"],
            credential_keys=["authorization"],
        ),
        agent_id="desk",
    )
    replaced: list[str] = []
    forgot: list[list[str]] = []
    envelope = Envelope(
        payload="WRAPPED",
        meta={
            "protocol": "a2a",
            "context_id": "client-ctx",
            "message_id": "m",
            "task_id": "t",
            "metadata": {"k": "v"},
            "replace_text": replaced.append,
            "forget_metadata": forgot.append,
        },
        headers={"authorization": "Bearer x"},
    )
    out = serve(layer, envelope)
    assert layer.wraps_inbound is True
    principal = out.meta["principal"]
    assert principal["subject"] == "alice" and principal["actor"] == "desk" and principal["chain"] == ["svc"]
    owner = out.meta["owner"]
    assert owner == owner_key("rec", GOOD | {"subject": "alice", "claims": {"iss": "https://idp"}})
    assert out.meta["context_id"] == conversation_id(owner, "client-ctx") == principal["session"]
    assert forgot == [["authorization"]] and out.meta["credential_keys"] == ["authorization"] and replaced == []
    inbound = scripted_of(layer).inbounds[0]
    assert inbound["session"] == "client-ctx" and inbound["request_id"] == "m" and inbound["task_id"] == "t"
    assert (
        inbound["payload"] == "WRAPPED" and inbound["meta"] == {"k": "v"} and inbound["audience"]["protocol"] == "a2a"
    )


def test_a_scheme_that_unwraps_the_text_gives_the_handler_the_text_and_the_transport_the_same() -> None:
    layer = verifying(Verified(subject="alice", claims={}, source="token", payload="plain text"))
    replaced: list[str] = []
    out = serve(layer, Envelope(payload="WRAPPED", meta={"protocol": "a2a", "replace_text": replaced.append}))
    assert out.payload == "plain text" and replaced == ["plain text"]


def test_the_row_audience_is_what_a_verifier_binds_to_else_the_endpoint_below() -> None:
    row = verifying(GOOD, audience={"protocol": "a2a", "target": "https://row.example"})
    serve(row, Envelope(payload="x"))
    assert scripted_of(row).inbounds[0]["audience"] == {"protocol": "a2a", "target": "https://row.example"}
    bare = verifying(GOOD, holder_scope="bare")
    bare.bind(Lower(endpoint="https://below.example"))
    serve(bare, Envelope(payload="x", meta={"protocol": "mcp"}))
    assert scripted_of(bare).inbounds[0]["audience"] == {"protocol": "mcp", "target": "https://below.example"}
    assert "payload" not in scripted_of(bare).inbounds[0]  # mcp is not a text protocol


def test_a_request_with_no_credential_is_answered_with_the_challenge_the_scheme_built(
    events: list[dict[str, Any]],
) -> None:
    asked = ChallengeRequired(
        {"nonce": "n-1", "audience": "t", "private/x": "y"}, carry={"a2a.metadata_key": "ch"}, wire="W"
    )
    layer = verifying(asked, agent_id="desk")
    with pytest.raises(PeerChallenge) as err:
        serve(layer, Envelope(payload="x"))
    assert err.value.challenge == {"nonce": "n-1", "audience": "t", "private/x": "y"}
    assert err.value.carry == {"a2a.metadata_key": "ch"} and err.value.wire == "W"
    (event,) = [e for e in events if e["kind"] == "identity.challenge"]
    assert (event["direction"], event["outcome"], event["actor"]) == ("issued", "ok", "desk")


def test_any_other_failure_to_verify_is_a_closed_refusal_and_the_handler_never_runs() -> None:
    ran: list[int] = []

    async def handler(envelope: Envelope) -> Any:
        ran.append(1)

    for failure, code in (
        (IdentityError("refused", "bad_proof"), "refused:bad_proof"),
        (RuntimeError("secret-canary"), "unavailable"),
    ):
        layer = verifying(failure)
        with pytest.raises(PeerChallenge) as err:
            asyncio.run(layer.serve(handler)(Envelope(payload="x")))
        assert err.value.code == code and "canary" not in str(err.value) and err.value.challenge == {}
    assert ran == []


def test_a_verifier_that_reports_a_non_string_issuer_is_refused() -> None:
    layer = verifying(Verified(subject="a", claims={"iss": 3}, source="token"))
    with pytest.raises(PeerChallenge) as err:
        serve(layer, Envelope(payload="x"))
    assert err.value.code == "refused:bad_issuer"
    with pytest.raises(PeerChallenge, match="bad_issuer"):
        owner_key("rec", {"subject": "a", "claims": {"iss": 3}})


def test_the_owner_is_a_digest_of_record_issuer_and_subject_and_nothing_less() -> None:
    base = owner_key("rec", {"subject": "a", "claims": {"iss": "i"}})
    assert base.startswith("own:") and len(base) == 44
    assert (
        len(
            {
                base,
                owner_key("rec2", {"subject": "a", "claims": {"iss": "i"}}),
                owner_key("rec", {"subject": "b", "claims": {"iss": "i"}}),
                owner_key("rec", {"subject": "a", "claims": {"iss": "j"}}),
                owner_key("rec", {"subject": "a", "claims": {}}),
            }
        )
        == 5
    )

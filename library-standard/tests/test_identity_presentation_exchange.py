#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The presentation-exchange double is a wallet and a verifier: the kit runs on it, and its refusals hold."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mas.library.standard.lib.identity.contract import (
    ChallengeRequired,
    HandshakeContext,
    IdentityError,
    IdentityScheme,
    Inbound,
    InteractionRequired,
)
from mas.library.standard.testing.identity_conformance import SchemeConformance
from mas.library.standard.testing.identity_fakes import FAKE_NOW, FakeChannel, bound_secrets, make_request
from mas.library.standard.testing.identity_presentation_exchange import (
    MAX_LIVE_NONCES,
    MAX_PRESENTATION_CHARS,
    PRESENTATION_HEADER,
    PresentationExchange,
    sign_presentation,
    submit_channel,
    unwrap_message,
    wrap_message,
)
from mas.runtime.secrets.ref import SecretRef

AUD = "https://verifier.example"
KEY = SecretRef(name="pe-key")
STORE = {"pe-key": "pe-secret-key-12345"}
PARAMS: dict[str, Any] = {
    "key": KEY,
    "subject": "desk-member",
    "claims": {"group": "infraadmin", "region": "eu"},
    "requirements": ["group"],
    "audience": AUD,
}


@pytest.fixture(autouse=True)
def _store():
    with bound_secrets(STORE):
        yield


def _inbound(headers: dict[str, str] | None = None, **extra: Any) -> Inbound:
    out = Inbound(
        direction="request",
        headers=headers or {},
        audience={"protocol": "mcp", "target": AUD},
        now=FAKE_NOW,
        session="s1",
    )
    out.update(extra)  # type: ignore[typeddict-item]
    return out


def _challenge(verifier: PresentationExchange, **extra: Any) -> dict[str, Any]:
    with pytest.raises(ChallengeRequired) as asked:
        asyncio.run(verifier.verify(_inbound(**extra)))
    return dict(asked.value.challenge)


def _answer(
    holder: PresentationExchange, challenge: dict[str, Any], audience: str = AUD, **extra: Any
) -> dict[str, Any]:
    """The inbound that carries the holder's presentation of ``challenge``."""
    shown = asyncio.run(
        holder.present(
            make_request(phase="challenge", target=audience, peer={"challenge": challenge}, request={"method": "x"})
        )
    )
    return {"headers": shown["headers"], **extra}


def _verify(verifier: PresentationExchange, **kw: Any) -> Any:
    return asyncio.run(verifier.verify(_inbound(**kw)))


class TestPresentationExchangeConformance(SchemeConformance):
    def make_scheme(self) -> IdentityScheme:
        return PresentationExchange.from_spec(self.good_params())

    def good_params(self) -> dict[str, Any]:
        return dict(PARAMS)

    def secrets(self) -> dict[str, str]:
        return {"pe-key": self.canary}

    def _submitted(self, nonce: str, audience: str) -> dict[str, Any]:
        claims = {"group": "infraadmin"}
        return {
            "alg": "hmac-sha256",
            "nonce": nonce,
            "audience": audience,
            "subject": "desk-member",
            "claims": claims,
            "proof": sign_presentation(self.canary, nonce, audience, claims),
        }

    def _nonce(self, scheme: IdentityScheme) -> str:
        with pytest.raises(ChallengeRequired) as asked:
            asyncio.run(scheme.verify(self.challenge_inbound()))  # type: ignore[attr-defined]
        return str(asked.value.challenge["nonce"])

    def challenge_inbound(self) -> Inbound:
        return _inbound()

    def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound:
        submitted = self._submitted(self._nonce(scheme), audience or AUD)
        return _inbound({PRESENTATION_HEADER: json.dumps(submitted)}, audience={"protocol": "mcp", "target": AUD})

    def answer_challenge(self, challenge: Any, audience: str) -> Inbound:
        submitted = self._submitted(str(challenge["nonce"]), audience)
        return _inbound({PRESENTATION_HEADER: json.dumps(submitted)})

    def oversized_answer(self, challenge: Any, audience: str) -> Inbound:
        submitted = self._submitted(str(challenge["nonce"]), audience)
        return _inbound({PRESENTATION_HEADER: json.dumps({**submitted, "pad": "x" * MAX_PRESENTATION_CHARS})})

    def message_inbound(self, scheme: IdentityScheme) -> Inbound:
        submitted = self._submitted(self._nonce(scheme), AUD)
        # the wrapper carries the canary in a claim the verifier never returns
        submitted["claims"] = {"group": "infraadmin"}
        text = wrap_message("hello", {**submitted, "extra": self.canary})
        return _inbound(payload=text, audience={"protocol": "a2a", "target": AUD})

    def make_handshake_context(self) -> HandshakeContext:
        return HandshakeContext(
            record="rec1",
            principal={"actor": "a"},
            audience={"protocol": "mcp", "target": AUD},
            task="",
            peer={"challenge": {"nonce": "n1", "audience": AUD, "requirements": ["group"]}},
            channel=FakeChannel([{"session": "s-1"}]),
            now=FAKE_NOW,
        )


# --- the holder ------------------------------------------------------------------------------------


def test_a_holder_answers_a_challenge_and_the_verifier_admits_it() -> None:
    verifier, holder = PresentationExchange.from_spec(PARAMS), PresentationExchange.from_spec(PARAMS)
    challenge = _challenge(verifier)
    verified = _verify(verifier, headers=_answer(holder, challenge)["headers"])
    assert verified["subject"] == "desk-member" and verified["claims"] == {"group": "infraadmin"}


def test_a_message_is_wrapped_and_unwrapped_with_the_text_intact() -> None:
    verifier, holder = PresentationExchange.from_spec(PARAMS), PresentationExchange.from_spec(PARAMS)
    challenge = _challenge(verifier)
    shown = asyncio.run(
        holder.present(
            make_request(
                phase="challenge", target=AUD, peer={"challenge": challenge}, request={"method": "m", "body_text": "hi"}
            )
        )
    )
    verified = _verify(verifier, payload=shown["payload"])
    assert verified["payload"] == "hi" and "presentation" not in verified["payload"]
    assert unwrap_message("not json") is None
    assert unwrap_message(json.dumps({"presentation": 1})) is None
    assert unwrap_message(json.dumps({"presentation": {}, "text": 3})) is None


def test_the_holder_declares_itself_per_protocol() -> None:
    holder = PresentationExchange.from_spec(PARAMS)
    mcp = asyncio.run(holder.present(make_request(protocol="mcp")))
    a2a = asyncio.run(holder.present(make_request(protocol="a2a")))
    other = asyncio.run(holder.present(make_request(protocol="http")))
    assert "mcp.capabilities.extensions" in mcp["advertise"] and "a2a.extensions" in a2a["advertise"]
    assert other["advertise"] == {}


@pytest.mark.parametrize(
    ("peer", "reason"),
    [
        ({"challenge": "nope"}, "no_request"),
        ({"challenge": {"audience": AUD}}, "no_nonce"),
        ({"challenge": {"nonce": "n", "audience": "https://other.example"}}, "wrong_audience"),
        ({"challenge": {"nonce": "n", "audience": AUD, "requirements": ["clearance"]}}, "requirements_unmet"),
    ],
)
def test_a_holder_refuses_a_challenge_it_cannot_or_must_not_answer(peer: dict[str, Any], reason: str) -> None:
    holder = PresentationExchange.from_spec(PARAMS)
    with pytest.raises(IdentityError) as err:
        asyncio.run(holder.present(make_request(phase="challenge", target=AUD, peer=peer, request={"method": "x"})))
    assert err.value.reason == reason


def test_a_pending_human_step_is_asked_before_the_answer() -> None:
    holder = PresentationExchange.from_spec({**PARAMS, "pending_polls": 1})
    peer = {"challenge": {"nonce": "n", "audience": AUD, "requirements": ["group"]}}
    req = make_request(phase="challenge", target=AUD, peer=peer, request={"method": "x"})
    with pytest.raises(InteractionRequired):
        asyncio.run(holder.present(req))
    assert PRESENTATION_HEADER in asyncio.run(holder.present(req))["headers"]


def test_a_holder_does_not_sign_and_is_not_asked_for_another_identity() -> None:
    holder = PresentationExchange.from_spec(PARAMS)
    with pytest.raises(IdentityError) as err:
        asyncio.run(holder.present(make_request(phase="sign")))
    assert err.value.reason == "no_sign"
    with pytest.raises(IdentityError) as err:
        asyncio.run(holder.present(make_request(identity="other")))
    assert err.value.code == "no_credential"


def test_a_revoked_holder_stops_presenting_and_says_so() -> None:
    holder = PresentationExchange.from_spec(PARAMS)
    asyncio.run(holder.revoke("exchange"))
    assert asyncio.run(holder.describe("exchange"))["facts"]["revoked"] is True
    assert [i["status"] for i in holder.identities()] == ["revoked"]
    assert holder.select(make_request()) is None
    with pytest.raises(IdentityError) as err:
        asyncio.run(holder.present(make_request()))
    assert err.value.code == "revoked"
    with pytest.raises(IdentityError) as err:
        asyncio.run(
            holder.handshake(HandshakeContext(**TestPresentationExchangeConformance().make_handshake_context()))
        )
    assert err.value.code == "revoked"


def test_a_handshake_sends_the_presentation_on_the_session_and_keeps_the_session_id() -> None:
    verifier, holder = PresentationExchange.from_spec(PARAMS), PresentationExchange.from_spec(PARAMS)
    challenge = _challenge(verifier)
    channel = submit_channel(verifier, AUD, session="s1", now=FAKE_NOW)
    evidence = asyncio.run(
        holder.handshake(
            HandshakeContext(
                record="r",
                principal={},
                audience={"protocol": "mcp", "target": AUD},
                task="",
                peer={"challenge": challenge},
                channel=channel,
                now=FAKE_NOW,
            )
        )
    )
    assert evidence["meta"]["presentationexchange/session"] == "session-for-desk-member"


def test_a_handshake_the_verifier_does_not_admit_is_refused() -> None:
    verifier, holder = PresentationExchange.from_spec(PARAMS), PresentationExchange.from_spec(PARAMS)
    channel = submit_channel(verifier, AUD, session="s1", now=FAKE_NOW)
    with pytest.raises(IdentityError) as err:
        asyncio.run(
            holder.handshake(
                HandshakeContext(
                    record="r",
                    principal={},
                    audience={"protocol": "mcp", "target": AUD},
                    task="",
                    peer={"challenge": {"nonce": "never-issued", "audience": AUD, "requirements": ["group"]}},
                    channel=channel,
                    now=FAKE_NOW,
                )
            )
        )
    assert err.value.reason == "not_admitted"


# --- the verifier ----------------------------------------------------------------------------------


def _forged(challenge: dict[str, Any], **changes: Any) -> str:
    claims = changes.pop("claims", {"group": "infraadmin"})
    audience = changes.pop("audience", AUD)
    body = {
        "alg": "hmac-sha256",
        "nonce": challenge["nonce"],
        "audience": audience,
        "subject": "desk-member",
        "claims": claims,
        "proof": sign_presentation(STORE["pe-key"], challenge["nonce"], audience, claims),
        **changes,
    }
    return json.dumps(body)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"audience": "https://other.example"}, "challenge_invalid"),
        ({"alg": "none"}, "algorithm_not_allowed"),
        ({"claims": ["not", "a", "mapping"]}, "malformed"),
        ({"proof": "0" * 64}, "bad_proof"),
        ({"claims": {"region": "eu"}}, "requirements_unmet"),
        ({"subject": ""}, "malformed"),
        ({"nonce": 3}, "challenge_invalid"),
    ],
)
def test_a_forged_presentation_is_refused_with_one_closed_reason(changes: dict[str, Any], reason: str) -> None:
    verifier = PresentationExchange.from_spec(PARAMS)
    challenge = _challenge(verifier)
    body = json.loads(_forged(challenge))
    body.update(changes)
    if "claims" in changes and isinstance(changes["claims"], dict):
        body["proof"] = sign_presentation(STORE["pe-key"], challenge["nonce"], AUD, changes["claims"])
    with pytest.raises(IdentityError) as err:
        _verify(verifier, headers={PRESENTATION_HEADER: json.dumps(body)})
    assert err.value.reason == reason


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "x" * (MAX_PRESENTATION_CHARS + 1)])
def test_a_presentation_that_is_not_a_bounded_object_is_refused(raw: str) -> None:
    verifier = PresentationExchange.from_spec(PARAMS)
    with pytest.raises(IdentityError) as err:
        _verify(verifier, headers={PRESENTATION_HEADER: raw})
    assert err.value.reason in {"malformed", "too_large"}


def test_a_nonce_is_spent_once_and_bound_to_its_caller_task_and_time() -> None:
    verifier = PresentationExchange.from_spec(PARAMS)
    challenge = _challenge(verifier, task_id="t1")
    good = {PRESENTATION_HEADER: _forged(challenge)}
    with pytest.raises(IdentityError):  # another task
        _verify(verifier, headers=good, task_id="t2")
    with pytest.raises(IdentityError):  # already spent by that refused try
        _verify(verifier, headers=good, task_id="t1")
    late = _challenge(verifier, task_id="t1")
    with pytest.raises(IdentityError):  # past the nonce's own life
        asyncio.run(
            verifier.verify(_inbound({PRESENTATION_HEADER: _forged(late)}, task_id="t1", now=FAKE_NOW + 3600.0))
        )


def test_a_caller_holds_a_few_live_nonces_and_the_oldest_goes_first() -> None:
    verifier = PresentationExchange.from_spec(PARAMS)
    nonces = [_challenge(verifier)["nonce"] for _ in range(MAX_LIVE_NONCES + 1)]
    first = {"nonce": nonces[0]}
    with pytest.raises(IdentityError):
        _verify(verifier, headers={PRESENTATION_HEADER: _forged(first)})
    last = {"nonce": nonces[-1]}
    assert _verify(verifier, headers={PRESENTATION_HEADER: _forged(last)})["subject"] == "desk-member"


def test_the_verifier_needs_a_clock_a_policy_and_valid_params() -> None:
    verifier = PresentationExchange.from_spec(PARAMS)
    with pytest.raises(IdentityError) as err:
        asyncio.run(
            verifier.verify(Inbound(direction="request", headers={}, audience={"protocol": "mcp", "target": AUD}))
        )
    assert err.value.reason == "no_clock"
    with pytest.raises(IdentityError) as err:
        asyncio.run(PresentationExchange.from_spec({"key": KEY, "subject": "s"}).verify(_inbound()))
    assert err.value.reason == "not_a_verifier"
    with pytest.raises(IdentityError) as err:
        PresentationExchange.from_spec({**PARAMS, "requirements": []})
    assert err.value.reason == "empty_policy"
    with pytest.raises(IdentityError):
        PresentationExchange.from_spec({"key": KEY})
    assert "audience" in repr(verifier) and STORE["pe-key"] not in repr(vars(verifier))


def test_a_required_claim_value_is_checked_not_only_its_presence() -> None:
    verifier = PresentationExchange.from_spec({**PARAMS, "require_values": {"group": "other"}})
    challenge = _challenge(verifier)
    with pytest.raises(IdentityError) as err:
        _verify(verifier, headers={PRESENTATION_HEADER: _forged(challenge)})
    assert err.value.reason == "requirements_unmet"

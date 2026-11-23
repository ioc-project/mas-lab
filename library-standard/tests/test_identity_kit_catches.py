#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The kit is mutation-tested: each deliberately broken scheme must turn the named check red, with the named reason.

One mutant per assumption of the contract. ``Lean`` and ``Sound`` are the sound schemes the mutants break.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from typing import Any

import pytest
from identity_kit_bases import (
    AUD,
    MAIN,
    AdvisoryServer,
    ChallengingVerifier,
    Lean,
    Server,
    Sound,
    make_advisory_kit,
    make_kit,
    make_server_kit,
    make_verifier_kit,
)
from mas.library.standard.lib.identity.contract import (
    CAP_SIGN,
    CAP_VERIFY,
    ChallengeRequired,
    Delegation,
    Evidence,
    IdentityError,
    IdentityInfo,
    Inbound,
    PresentRequest,
    Verified,
    Verifier,
)
from mas.library.standard.testing.identity_conformance import SchemeConformance
from mas.library.standard.testing.identity_fakes import CANARY, bound_secrets
from mas.runtime.secrets import current_resolver


def lean(name: str, **members: Any) -> type[Lean]:
    return type(name, (Lean,), {"__module__": __name__, **members})


def sound(name: str, **members: Any) -> type[Sound]:
    return type(name, (Sound,), {"__module__": __name__, **members})


def verifier(name: str, **members: Any) -> type[ChallengingVerifier]:
    return type(name, (ChallengingVerifier,), {"__module__": __name__, **members})


def advisory(name: str, **members: Any) -> type[AdvisoryServer]:
    return type(name, (AdvisoryServer,), {"__module__": __name__, **members})


# --- mutants of a wallet of one ------------------------------------------------------------------


async def _host(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(headers={"Host": "evil.example"})


async def _meta(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(meta={"session": "s"})


async def _mutates(self: Lean, req: PresentRequest) -> Evidence:
    req["task"] = "changed"
    return Evidence(headers={"x-a": "b"})


async def _leaky_reason(self: Lean, req: PresentRequest) -> Evidence:
    raise IdentityError("refused", CANARY)


async def _payload(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(payload="wrapped", valid_until=req["now"] + 60)


async def _advertise_leak(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(advertise={"mcp.capabilities.experimental": {"k": CANARY}}, valid_until=req["now"] + 60)


async def _advertise_unknown(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(advertise={"mcp.capabilities.experimentl": {}}, valid_until=req["now"] + 60)


async def _wall_clock(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(headers={"x-a": "b"}, valid_until=time.time() + 60)


async def _any_phase(self: Lean, req: PresentRequest) -> Evidence:
    return Evidence(headers={"x-a": "b"})


async def _ignores_identity(self: Lean, req: PresentRequest) -> Evidence:
    return self.own(req)


async def _remembers(self: Lean, req: PresentRequest) -> Evidence:
    self.seen = current_resolver().reveal(self._token)
    return self.own(req)


class _Counter(Lean):
    n = 0

    async def present(self, req: PresentRequest) -> Evidence:
        self.n += 1
        return Evidence(headers={"x-n": str(self.n)}, valid_until=req["now"] + 60)


def _leaky_identities(self: Lean) -> Sequence[IdentityInfo]:
    return [IdentityInfo(id=MAIN, kind="agent", subject=CANARY, labels=[], status="active")]


def _twin_identities(self: Lean) -> Sequence[IdentityInfo]:
    info = IdentityInfo(id=MAIN, kind="agent", subject="", labels=[], status="active")
    return [info, info]


def _bad_status(self: Lean) -> Sequence[IdentityInfo]:
    return [IdentityInfo(id=MAIN, kind="agent", subject="", labels=[], status="maybe")]


def _selects_unlisted(self: Lean, req: PresentRequest) -> str | None:
    return "ghost"


def _select_mutates(self: Lean, req: PresentRequest) -> str | None:
    req["task"] = "changed"
    return MAIN


class _Drifting(Lean):
    calls = 0

    def select(self, req: PresentRequest) -> str | None:
        _Drifting.calls += 1
        return MAIN if _Drifting.calls % 2 else None


class _LeakyRepr(Lean):
    def __repr__(self) -> str:
        return f"LeakyRepr({CANARY})"


class _KeepsOnClass(Lean):
    """Keeps the revealed value where an instance scan does not look: a class attribute."""

    async def present(self, req: PresentRequest) -> Evidence:
        type(self).remembered = CANARY  # type: ignore[attr-defined]
        return await super().present(req)


_STASH: list[str] = []


class _KeepsInModule(Lean):
    """Keeps the revealed value in a module global, which neither the instance nor its class shows."""

    async def present(self, req: PresentRequest) -> Evidence:
        out = await super().present(req)
        _STASH[:] = [CANARY]
        return out


class _Signing(Lean):
    """Signs what it is given: the evidence is a digest of the request it was asked to sign."""

    capabilities = frozenset({CAP_SIGN})

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "sign":
            digest = hashlib.sha256(json.dumps(req["request"], sort_keys=True).encode()).hexdigest()
            return Evidence(headers={"x-signature": digest})
        return await super().present(req)


class _ConstantSign(_Signing):
    """Returns the same evidence for every request: it signs nothing it was given."""

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "sign":
            return Evidence(headers={"x-signature": "constant"})
        return await Lean.present(self, req)


class _BareDeclared(Lean):
    capabilities = frozenset({CAP_VERIFY})


class _UndeclaredVerifier(Lean, Verifier):
    async def verify(self, inbound: Any) -> Any:
        return {"subject": "u", "claims": {}, "source": "token"}


# --- mutants of the sound scheme -----------------------------------------------------------------


async def _delegate_widens(self: Sound, delegation: Delegation) -> IdentityInfo:
    return IdentityInfo(
        id="d-wide",
        kind="delegated",
        subject="u",
        labels=[],
        status="active",
        grant={"scopes": ["read", "write", "admin"], "valid_until": 1_000_300.0, "depth": 1},
        parent=MAIN,
    )


async def _delegate_no_grant(self: Sound, delegation: Delegation) -> IdentityInfo:
    info = await Sound.delegate(self, delegation)
    return IdentityInfo(**{k: v for k, v in info.items() if k != "grant"})  # type: ignore[typeddict-item]


async def _delegate_refuses(self: Sound, delegation: Delegation) -> IdentityInfo:
    raise IdentityError("refused", "no_delegation")


class _Fresh(Sound):
    n = 0

    async def delegate(self, delegation: Delegation) -> IdentityInfo:
        info = await Sound.delegate(self, delegation)
        self.n += 1
        return IdentityInfo(**{**info, "id": f"d-{self.n}"})  # type: ignore[typeddict-item]


async def _acts_as_itself(self: Sound, req: PresentRequest) -> Evidence:
    out = dict(await Sound.present(self, req))
    if req.get("identity", MAIN) != MAIN:
        out["headers"] = dict(self.own(req)["headers"])
    return out


def _derived_outlives(self: Sound, req: PresentRequest, grant: Any) -> dict[str, Any]:
    return {"scopes": ["read"], "audience": "https://other.example", "valid_until": req["now"] + 60, "depth": 1}


def _revoked_derived_listed_active(self: Sound) -> Sequence[IdentityInfo]:
    return [{**info, "status": "active"} for info in Lean.identities(self)] + [  # type: ignore[misc]
        {**info, "status": "active"} for info in self.derived.values()
    ]


async def _revoke_noop(self: Sound, ref: str) -> None:
    return None


async def _verify_refuses_all(self: Sound, inbound: Inbound) -> Verified:
    raise IdentityError("refused", "everything")


async def _verify_ignores_audience(self: Sound, inbound: Inbound) -> Verified:
    if not inbound.get("headers"):
        raise IdentityError("refused", "no_credential")
    return {"subject": "u", "claims": {}, "source": "token"}


async def _verify_no_credential_check(self: Sound, inbound: Inbound) -> Verified:
    if inbound.get("audience", {}).get("target") != AUD:
        raise IdentityError("refused", "wrong_audience")
    return {"subject": "u", "claims": {}, "source": "token"}


async def _verify_echoes_header(self: Sound, inbound: Inbound) -> Verified:
    return {"subject": "u", "claims": {"seen": inbound["headers"]["authorization"]}, "source": "token"}


async def _leaky_describe(self: Sound, ref: str) -> Any:
    return {"facts": {"token": CANARY}}


async def _handshakes_after_revoke(self: Sound, ctx: Any) -> Evidence:
    return Evidence(meta={"x/session": "s1"})


async def _verify_everyone(self: Lean, inbound: Inbound) -> Verified:
    return {"subject": "anyone", "claims": {}, "source": "token"}


async def _verify_always_challenges(self: Lean, inbound: Inbound) -> Verified:
    raise ChallengeRequired({"nonce": "n1", "audience": AUD})


VerifiesEveryone = type(
    "VerifiesEveryone",
    (Lean, Verifier),
    {"__module__": __name__, "capabilities": frozenset({CAP_VERIFY}), "verify": _verify_everyone},
)
ChallengesWithoutHook = type(
    "ChallengesWithoutHook",
    (Lean, Verifier),
    {"__module__": __name__, "capabilities": frozenset({CAP_VERIFY}), "verify": _verify_always_challenges},
)

VF = "test_delegated_evidence_is_bound_and_value_free"
ACCEPTS = "test_a_verifier_accepts_its_valid_inbound"
NOTVALID = "test_a_verifier_refuses_what_is_not_valid"
NOCRED = "test_verify_refuses_an_inbound_without_a_credential"
CHK = "test_challenges_are_single_use_and_audience_bound"
WALLET = "test_the_wallet_lists_value_free_identities"
SELECT = "test_select_names_a_listed_identity_and_presents_nothing"
SHOWS = "test_present_shows_the_identity_it_is_asked_for"
PURE = "test_present_is_pure_given_params_and_request"

# (mutant, the check that must turn red, a fragment of the reason it gives)
CASES: list[tuple[type, str, str]] = [
    # the wallet
    (lean("LeakyIdentities", identities=_leaky_identities), WALLET, "show a secret"),
    (lean("TwinIdentities", identities=_twin_identities), WALLET, "unique"),
    (lean("BadStatus", identities=_bad_status), WALLET, "identity_status_bad"),
    (lean("SelectsUnlisted", select=_selects_unlisted), SELECT, "does not list"),
    (lean("SelectMutates", select=_select_mutates), SELECT, "mutated"),
    (_Drifting, SELECT, "differs"),
    (lean("IgnoresIdentity", present=_ignores_identity), SHOWS, "does not hold"),
    (lean("Remembers", present=_remembers), "test_no_revealed_secret_is_kept_on_the_object", "keeps a revealed"),
    (_LeakyRepr, "test_errors_are_value_free", "repr of the scheme"),
    # deterministic present
    (lean("WallClock", present=_wall_clock), PURE, "wall clock"),
    (_Counter, PURE, "same instance is asked twice"),
    (lean("Mutates", present=_mutates), PURE, "mutated its request"),
    (lean("NoGoodParams", kit_no_good_params=True), "test_from_spec_rejects_bad_params", "good_params"),
    # output shape
    (lean("HostHeader", present=_host), "test_present_output_is_valid", "header_denied"),
    (lean("BadMeta", present=_meta), "test_present_output_is_valid", "meta_key_not_namespaced"),
    (lean("PayloadOnMcp", present=_payload), "test_present_output_is_valid", "payload_unsupported"),
    (lean("AdvertiseUnknown", present=_advertise_unknown), "test_present_output_is_valid", "advertise_section"),
    (lean("AdvertiseLeaks", present=_advertise_leak), "test_present_output_is_valid", "advertise shows a secret"),
    (lean("LeakyReason", present=_leaky_reason), "test_errors_are_value_free", "shows a value"),
    (lean("LenientSpec", from_spec=classmethod(lambda cls, p: cls())), "test_from_spec_rejects_bad_params", "accepted"),
    (lean("BadSchema", PARAMS_SCHEMA={"type": "object", "required": 5}), "test_declares_the_contract", "valid JSON"),
    (lean("SignsUndeclared", present=_any_phase), "test_sign_phase_is_honest", "sign capability"),
    (_KeepsInModule, "test_no_revealed_secret_is_kept_on_the_object", "module global"),
    (_ConstantSign, "test_sign_is_exercised", "signs nothing"),
    (_KeepsOnClass, "test_no_revealed_secret_is_kept_on_the_object", "keeps a revealed secret"),
    # capability honesty
    (_BareDeclared, "test_capability_honesty_both_ways", "capability 'verify'"),
    (_UndeclaredVerifier, "test_capability_honesty_both_ways", "capability 'verify'"),
    # evidence bound to the challenge and the audience
    (verifier("ReplayReason", replay_reason="replayed"), CHK, "uniform"),
    (verifier("ReusedNonce", fixed_nonce=True), CHK, "issued twice"),
    (verifier("NoSizeCap", cap_size=False), CHK, "oversized"),
    (verifier("CarryLeak", carry_leak=True), CHK, "carry shows a secret"),
    (verifier("EchoWire", echo_wire=True), CHK, "echoed"),
    (verifier("AcceptedTwice", spend_nonce=False), CHK, "accepted twice"),
    (verifier("AcceptedElsewhere", bind_audience=False), CHK, "for another audience"),
    (verifier("LeaksPayload", leak_payload=True), "test_a_verified_payload_never_holds_the_credential", "credential"),
    (verifier("KeepsWrapper", keep_wrapper=True), "test_a_verified_payload_never_holds_the_credential", "wrapper"),
    (verifier("NoHooks", kit_hooks=False), CHK, "challenge_inbound"),
    (verifier("NoAnswer", kit_hooks="no_answer"), CHK, "answer_challenge"),
    (verifier("NoOversized", kit_hooks="no_oversized"), CHK, "oversized_answer"),
    (VerifiesEveryone, NOCRED, "no credential"),
    (ChallengesWithoutHook, NOCRED, "challenge_inbound"),
    # advisory challenges
    (advisory("AdvisoryNonce", nonce=True), "test_an_advisory_challenge_is_static_and_value_free", "nonce"),
    (advisory("AdvisoryEcho", echo_header=True), "test_an_advisory_challenge_is_static_and_value_free", "echoes"),
    (advisory("AdvisoryDrift", drift=True), "test_an_advisory_challenge_is_static_and_value_free", "differs"),
    (
        advisory("AdvisoryOther", names_other_audience=True),
        "test_an_advisory_challenge_is_static_and_value_free",
        "another audience",
    ),
    # delegation
    (sound("DelegateWidens", delegate=_delegate_widens), "test_delegation_only_narrows", "widens"),
    (sound("DelegateNoGrant", delegate=_delegate_no_grant), "test_delegation_only_narrows", "its own"),
    (sound("DelegateRefuses", delegate=_delegate_refuses), "test_a_delegating_scheme_delegates", "never delegates"),
    (_Fresh, "test_a_delegation_is_deterministic", "same identity"),
    (sound("ActsAsItself", present=_acts_as_itself), "test_a_delegation_acts_as_the_parent", "own credential"),
    (sound("GrantWrongAudience", child_grant=_derived_outlives), VF, "another audience"),
    (sound("AcceptsAnyAudience", verify=_verify_ignores_audience), VF, "accepted for another"),
    (
        sound("ChildSurvivesParent", _revoked=lambda self, identity: identity in self.revoked_ids),
        "test_revoking_a_parent_revokes_what_it_derived",
        "still presents",
    ),
    # verify
    (sound("VerifyNeedsHeaders", verify=_verify_ignores_audience), NOTVALID, "another audience"),
    (sound("VerifyNoCredentialCheck", verify=_verify_no_credential_check), NOTVALID, "credential removed"),
    (sound("VerifyRefusesItsOwn", verify=_verify_refuses_all), ACCEPTS, "refused its own valid inbound"),
    (sound("VerifyEchoesHeader", verify=_verify_echoes_header), ACCEPTS, "echoed the credential"),
    (sound("LeakyDescribe", describe=_leaky_describe), "test_describe_is_public", "shows a secret"),
    # revocation
    (sound("RevokeNoop", revoke=_revoke_noop), "test_a_revoked_identity_refuses_everything", "listed as revoked"),
    (
        sound("HandshakesAfterRevoke", handshake=_handshakes_after_revoke),
        "test_a_revoked_identity_refuses_everything",
        "handshakes",
    ),
    (
        sound("ListsRevokedAsActive", identities=_revoked_derived_listed_active),
        "test_a_revoked_identity_refuses_everything",
        "listed as revoked",
    ),
]


def _kit_for(cls: type) -> SchemeConformance:
    if issubclass(cls, ChallengingVerifier):
        return make_verifier_kit(cls)
    if issubclass(cls, AdvisoryServer):
        return make_advisory_kit(cls)
    if issubclass(cls, Server):
        return make_server_kit(cls)
    return make_kit(cls)  # type: ignore[arg-type]


@pytest.mark.parametrize(("cls", "check", "why"), CASES, ids=[f"{c.__name__}-{k}" for c, k, _ in CASES])
def test_a_broken_scheme_turns_its_check_red(cls: type, check: str, why: str) -> None:
    kit = _kit_for(cls)
    with bound_secrets(kit.secrets()), pytest.raises(AssertionError, match=why):
        getattr(kit, check)()


@pytest.mark.parametrize("cls", [Lean, Sound], ids=lambda c: c.__name__)
def test_the_sound_schemes_pass_every_check(cls: type) -> None:
    kit = _kit_for(cls)
    with bound_secrets(kit.secrets()):
        for name in dir(kit):
            if name.startswith("test_"):
                getattr(kit, name)()


def test_a_scheme_that_signs_what_it_is_given_passes_the_sign_check() -> None:
    kit = make_kit(_Signing)
    with bound_secrets(kit.secrets()):
        kit.test_sign_is_exercised()
        kit.test_no_revealed_secret_is_kept_on_the_object()

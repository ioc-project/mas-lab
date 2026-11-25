#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The signed-badge and presentation-exchange doubles are wallets: the kit runs on both, and their key paths hold."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mas.library.standard.lib.identity.contract import IdentityError, IdentityScheme, Inbound, InteractionRequired
from mas.library.standard.testing.identity_conformance import SchemeConformance
from mas.library.standard.testing.identity_fakes import bound_secrets, make_request
from mas.library.standard.testing.identity_signed_badge import BADGE_HEADER, SignedBadge
from mas.runtime.secrets.ref import SecretRef

KEY = SecretRef(name="badge-key")
STORE = {"badge-key": "issuer-key-12345", "other-key": "another-key-12345"}
PARAMS: dict[str, Any] = {
    "key": KEY,
    "subject": "desk-member",
    "claims": {"group": "infraadmin"},
    "requirements": ["group"],
}


@pytest.fixture(autouse=True)
def _store():
    with bound_secrets(STORE):
        yield


class TestSignedBadgeConformance(SchemeConformance):
    def make_scheme(self) -> IdentityScheme:
        return SignedBadge.from_spec(self.good_params())

    def good_params(self) -> dict[str, Any]:
        return {**PARAMS, "audience": "https://tools.example"}

    def secrets(self) -> dict[str, str]:
        return {"badge-key": self.canary}

    def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound:
        shown = asyncio.run(SignedBadge.from_spec(self.good_params()).present(make_request()))
        return Inbound(
            direction="request",
            headers=shown["headers"],
            audience={"protocol": "mcp", "target": audience or "https://tools.example"},
        )


def _present(scheme: SignedBadge) -> str:
    return asyncio.run(scheme.present(make_request()))["headers"][BADGE_HEADER]


def _verify(scheme: SignedBadge, header: str | None):
    headers = {} if header is None else {BADGE_HEADER: header}
    return asyncio.run(
        scheme.verify(Inbound(direction="request", headers=headers, audience={"protocol": "a2a", "target": "t"}))
    )


def test_a_presented_badge_verifies_offline_and_the_key_is_not_kept_on_the_object() -> None:
    holder, verifier = SignedBadge.from_spec(PARAMS), SignedBadge.from_spec(PARAMS)
    verified = _verify(verifier, _present(holder))
    assert verified["subject"] == "desk-member" and verified["claims"] == {"group": "infraadmin"}
    assert "issuer-key-12345" not in repr(vars(holder)) and "issuer-key-12345" not in repr(holder)


@pytest.mark.parametrize("tamper", ["flip-proof", "other-key", "missing-claim", "no-header", "garbage"])
def test_a_bad_badge_is_refused(tamper: str) -> None:
    verifier = SignedBadge.from_spec(PARAMS)
    header: str | None
    if tamper == "other-key":
        header = _present(SignedBadge.from_spec({**PARAMS, "key": SecretRef(name="other-key")}))
    elif tamper == "missing-claim":
        header = _present(SignedBadge.from_spec({**PARAMS, "claims": {}}))
    elif tamper == "flip-proof":
        good = _present(SignedBadge.from_spec(PARAMS))
        header = good[:-1] + ("0" if good[-1] != "0" else "1")
    elif tamper == "no-header":
        header = None
    else:
        header = "not-a-badge"
    with pytest.raises(IdentityError):
        _verify(verifier, header)


def test_a_verifier_with_an_audience_refuses_a_badge_shown_elsewhere() -> None:
    verifier = SignedBadge.from_spec({**PARAMS, "audience": "t"})
    shown = _present(SignedBadge.from_spec(PARAMS))
    assert _verify(verifier, shown)["subject"] == "desk-member"
    elsewhere = Inbound(direction="request", headers={BADGE_HEADER: shown}, audience={"protocol": "a2a", "target": "u"})
    with pytest.raises(IdentityError) as err:
        asyncio.run(verifier.verify(elsewhere))
    assert err.value.reason == "wrong_audience"


def test_revoke_stops_presenting_and_describe_says_so() -> None:
    scheme = SignedBadge.from_spec(PARAMS)
    assert asyncio.run(scheme.describe("badge"))["facts"]["revoked"] is False
    asyncio.run(scheme.revoke("badge"))
    assert asyncio.run(scheme.describe("badge"))["facts"]["revoked"] is True
    assert [i["status"] for i in scheme.identities()] == ["revoked"]
    with pytest.raises(IdentityError) as err:
        asyncio.run(scheme.present(make_request()))
    assert err.value.code == "revoked"


def test_a_pending_human_step_is_asked_then_succeeds() -> None:
    scheme = SignedBadge.from_spec({**PARAMS, "pending_polls": 1})
    with pytest.raises(InteractionRequired):
        asyncio.run(scheme.present(make_request()))
    assert BADGE_HEADER in asyncio.run(scheme.present(make_request()))["headers"]

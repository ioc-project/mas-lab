#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The conformance kit, run for every scheme and base class library-standard ships, and for the sound test schemes."""

from __future__ import annotations

from typing import Any

from identity_kit_bases import (
    AUD,
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
from mas.library.standard.lib.identity.contract import HandshakeContext, IdentityScheme, Inbound, PresentRequest
from mas.library.standard.plugins.identity.none import NoIdentity
from mas.library.standard.testing.identity_conformance import SchemeConformance
from mas.library.standard.testing.identity_fakes import FakeChannel, make_request
from mas.library.standard.testing.identity_scripted import Scripted


def _inbound(audience: str) -> Inbound:
    return Inbound(
        direction="request",
        protocol="mcp",
        headers={"authorization": "Bearer an-accepted-token"},
        audience={"protocol": "mcp", "target": audience},
    )


class TestNoneConformance(SchemeConformance):
    def make_scheme(self) -> IdentityScheme:
        return NoIdentity.from_spec({})

    def good_params(self) -> dict[str, Any]:
        return {}

    def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound:
        return _inbound(audience or AUD)  # anyone verifies as anonymous: any audience, any credential


class TestScriptedConformance(SchemeConformance):
    """The double replays what it is given. It does not declare ``delegate``: it has no authority to narrow."""

    sign_binds_request = False  # canned evidence: it signs nothing

    def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound:
        return _inbound(audience or AUD)

    def make_scheme(self) -> IdentityScheme:
        return Scripted(
            [{"headers": {"authorization": "Bearer " + self.canary}, "valid_until": 1_000_100.0, "reusable": True}],
            handshakes=[{"meta": {"scripted/session": "s1"}}],
            signs=[{"headers": {"signature": "sig"}}],
            verified={"subject": "u1", "claims": {}, "source": "token"},
            repeat_last=True,
        )

    def good_params(self) -> dict[str, Any]:
        return {"evidence": [{"headers": {"x-a": "b"}}], "repeat_last": True}

    def make_handshake_context(self) -> HandshakeContext:
        return HandshakeContext(
            record="rec1",
            principal={"actor": "a"},
            audience={"protocol": "mcp", "target": "t"},
            task="",
            peer={},
            channel=FakeChannel(),
            now=1_000_000.0,
        )

    def make_sign_request(self) -> PresentRequest:
        return make_request(request={"method": "POST", "url": "https://x", "body_sha256": ""})

    def test_no_revealed_secret_is_kept_on_the_object(self) -> None:
        """Not applicable: the double holds the evidence it was scripted with, canary included, by construction."""


class TestVerifierOnlyConformance(SchemeConformance):
    """Every kit check runs for a verify-only scheme, with no successful present required."""

    def make_scheme(self) -> IdentityScheme:
        return Server()

    def good_params(self) -> dict[str, Any]:
        return {"a": "x"}

    def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound:
        return _inbound(audience or AUD)


def _named(name: str, kit: SchemeConformance) -> type[SchemeConformance]:
    return type(name, (type(kit),), {})


TestLean = _named("TestLean", make_kit(Lean))
TestSound = _named("TestSound", make_kit(Sound))
TestChallengingVerifier = _named("TestChallengingVerifier", make_verifier_kit(ChallengingVerifier))
TestAdvisoryServer = _named("TestAdvisoryServer", make_advisory_kit(AdvisoryServer))
TestServer = _named("TestServer", make_server_kit(Server))

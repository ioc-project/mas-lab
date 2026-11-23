#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The sound schemes the kit mutants break, and the kits that run them.

``Lean`` is a wallet of one identity that presents one bearer and declares nothing. ``Sound`` has every capability but
``sign`` and derives narrower identities inside its wallet. ``ChallengingVerifier`` verifies by challenge. This module
reads no clock and draws no randomness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, cast

from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAP_DESCRIBE,
    CAP_HANDSHAKE,
    CAP_REVOKE,
    CAP_VERIFY,
    ChallengeRequired,
    Delegation,
    Delegator,
    Describer,
    Evidence,
    Grant,
    HandshakeContext,
    Handshaker,
    IdentityError,
    IdentityInfo,
    IdentityScheme,
    Inbound,
    PresentRequest,
    PublicView,
    Revoker,
    Verified,
    Verifier,
)
from mas.library.standard.lib.identity.rules import check_narrowing, validate
from mas.library.standard.lib.identity.verifier_only import VerifierOnly
from mas.library.standard.testing.identity_conformance import SchemeConformance, run
from mas.library.standard.testing.identity_fakes import CANARY, FAKE_NOW, FakeChannel, make_request
from mas.runtime.secrets import SecretRef, current_resolver

AUD = "https://tools.example"
MAIN = "main"
MAIN_GRANT: Grant = {"scopes": ["read", "write"], "valid_until": FAKE_NOW + 3600.0, "depth": 0}
SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": ["token"],
    "additionalProperties": False,
    "properties": {"token": {"x-secret": True}},
}
TOKEN_REF = SecretRef(name="tok")


class Lean(IdentityScheme):
    """A wallet of one identity: presents one bearer, declares nothing."""

    scheme: ClassVar[str] = "lean"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = SCHEMA
    kit_no_handshake_context = False
    kit_no_sign_request = False
    kit_no_valid_inbound = False
    kit_no_good_params = False
    kit_same_other_parent = False

    def __init__(self, token: SecretRef = TOKEN_REF) -> None:
        self._token = token
        self.revoked_ids: set[str] = set()

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Lean:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls(params["token"])

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def identities(self) -> Sequence[IdentityInfo]:
        status = "revoked" if MAIN in self.revoked_ids else "active"
        return [IdentityInfo(id=MAIN, kind="agent", subject="", labels=[], status=status, grant=dict(MAIN_GRANT))]

    def select(self, req: PresentRequest) -> str | None:
        return None if MAIN in self.revoked_ids else MAIN

    def own(self, req: PresentRequest) -> Evidence:
        token = current_resolver().reveal(self._token)
        return Evidence(headers={"authorization": "Bearer " + token}, valid_until=req["now"] + 60, reusable=True)

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "sign":
            raise IdentityError("unsatisfiable", "no_sign")
        identity = req.get("identity", MAIN)
        if identity != MAIN:
            raise IdentityError("no_credential")
        if MAIN in self.revoked_ids:
            raise IdentityError("revoked")
        return self.own(req)


class AdvisoryServer(VerifierOnly):
    """A resource server that answers no credential with a static RFC 6750 hint: realm and scope, no nonce."""

    scheme: ClassVar[str] = "advisory"
    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_VERIFY})
    ADVISORY_CHALLENGE: ClassVar[bool] = True
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    nonce = False  # the challenge carries a nonce although the flag says it is advice
    echo_header = False  # the hint repeats an inbound header value
    drift = False  # the hint differs from one call to the next
    names_other_audience = False  # the hint names another audience's address

    def __init__(self) -> None:
        self.count = 0

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> AdvisoryServer:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls()

    async def verify(self, inbound: Inbound) -> Verified:
        headers = inbound.get("headers", {})
        if headers.get("authorization") == "Bearer an-accepted-token":
            if inbound.get("audience", {}).get("target") != AUD:
                raise IdentityError("refused", "wrong_audience")
            return Verified(subject="u", claims={}, source="token")
        if headers.get("authorization"):
            raise IdentityError("refused", "invalid_token")
        self.count += 1
        hint: dict[str, Any] = {"realm": "tools", "requirements": ["read"]}
        wire = 'Bearer realm="tools", scope="read"'
        if self.nonce:
            hint["nonce"] = "n1"
        if self.echo_header:
            wire += f' hint="{headers.get("x-echo-mark", "")}"'
        if self.drift:
            hint["realm"] = f"tools{self.count}"
        if self.names_other_audience:
            hint["x/see"] = "https://elsewhere.example/realm"
        raise ChallengeRequired(hint, wire=wire)


def _accepted(target: str) -> Inbound:
    return Inbound(
        direction="request",
        protocol="mcp",
        headers={"authorization": "Bearer an-accepted-token"},
        audience={"protocol": "mcp", "target": target},
    )


def make_advisory_kit(cls: type[AdvisoryServer]) -> SchemeConformance:
    class Kit(SchemeConformance):
        def make_scheme(self) -> IdentityScheme:
            return cls()

        def good_params(self) -> dict[str, Any]:
            return {"a": "x"}

        def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound | None:
            return _accepted(audience or AUD)

    return Kit()


class Sound(Lean, Handshaker, Verifier, Describer, Revoker, Delegator):
    """Every capability but ``sign``: derives narrower identities in its wallet, audience-bound, revocable."""

    scheme: ClassVar[str] = "sound"
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {CAP_DELEGATE, CAP_HANDSHAKE, CAP_VERIFY, CAP_DESCRIBE, CAP_REVOKE}
    )

    def __init__(self, token: SecretRef = TOKEN_REF) -> None:
        super().__init__(token)
        self.derived: dict[str, IdentityInfo] = {}

    def identities(self) -> Sequence[IdentityInfo]:
        out = [dict(info) for info in super().identities()]
        out += [
            {**info, "status": "revoked" if info["id"] in self.revoked_ids else "active"}
            for info in self.derived.values()
        ]
        return cast(list[IdentityInfo], out)

    def child_grant(self, req: PresentRequest, grant: Mapping[str, Any]) -> dict[str, Any]:
        return {**grant, "audience": req["audience"]["target"]}

    def _revoked(self, identity: str) -> bool:
        info = self.derived.get(identity)
        return identity in self.revoked_ids or (info is not None and info.get("parent", "") in self.revoked_ids)

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "sign":
            raise IdentityError("unsatisfiable", "no_sign")
        identity = req.get("identity", MAIN)
        info = self.derived.get(identity)
        if identity != MAIN and info is None:
            raise IdentityError("no_credential")
        if self._revoked(identity):
            raise IdentityError("revoked")
        if info is None:
            return self.own(req)
        return {
            "headers": {"authorization": "Bearer obo", "x-on-behalf-of": info["subject"]},
            "valid_until": req["now"] + 60,
            "reusable": True,
            "grant": self.child_grant(req, info.get("grant", {})),
        }

    async def delegate(self, delegation: Delegation) -> IdentityInfo:
        parent = delegation.get("identity", MAIN)
        if parent != MAIN and parent not in self.derived:
            raise IdentityError("no_credential")
        if self._revoked(parent):
            raise IdentityError("revoked")
        grant = delegation.get("grant", {})
        parent_grant = MAIN_GRANT if parent == MAIN else self.derived[parent].get("grant", {})
        check_narrowing(parent_grant, grant, target=delegation.get("audience", {}).get("target", ""))
        digest = hashlib.sha256(repr((parent, sorted(grant.items()))).encode()).hexdigest()[:8]
        info = IdentityInfo(
            id=f"d-{digest}",
            kind="delegated",
            subject=delegation.get("principal", {}).get("subject", ""),
            labels=[],
            status="active",
            grant=dict(grant),
            parent=parent,
        )
        self.derived.setdefault(info["id"], info)
        return self.derived[info["id"]]

    async def handshake(self, ctx: HandshakeContext) -> Evidence:
        if MAIN in self.revoked_ids:
            raise IdentityError("revoked")
        return Evidence(meta={"x/session": "s1"})

    async def verify(self, inbound: Inbound) -> Verified:
        if not inbound.get("headers"):
            raise IdentityError("refused", "no_credential")
        if inbound.get("audience", {}).get("target") != AUD:
            raise IdentityError("refused", "wrong_audience")
        return {"subject": "u", "claims": {}, "source": "token"}

    async def describe(self, ref: str) -> PublicView:
        return PublicView(facts={"scheme": "sound"})

    async def revoke(self, ref: str) -> None:
        self.revoked_ids.add(ref)


def make_kit(cls: type[Lean]) -> SchemeConformance:
    class Kit(SchemeConformance):
        def make_scheme(self) -> IdentityScheme:
            return cls()

        def secrets(self) -> dict[str, str]:
            return {"tok": self.canary}

        def good_params(self) -> dict[str, Any] | None:
            return None if cls.kit_no_good_params else {"token": TOKEN_REF}

        def other_parent_evidence(self, grant: Any) -> Evidence:
            if cls.kit_same_other_parent:
                return self.parent_evidence(grant)
            return super().other_parent_evidence(grant)

        def delegation(self, grant: Grant) -> Delegation:
            return Delegation(
                identity=MAIN,
                grant={"scopes": ["read"], "valid_until": FAKE_NOW + 300.0, "depth": 1},
                principal={"actor": "agent-a", "subject": "user-1"},
                audience={"protocol": "mcp", "target": AUD},
            )

        def widening_grant(self) -> Grant:
            return {"scopes": ["read", "admin"], "valid_until": FAKE_NOW + 300.0, "depth": 1}

        def make_handshake_context(self) -> HandshakeContext | None:
            if cls.kit_no_handshake_context:
                return None
            return HandshakeContext(
                record="rec1",
                principal={"actor": "a"},
                audience={"protocol": "mcp", "target": AUD},
                task="",
                peer={},
                channel=FakeChannel(),
                now=FAKE_NOW,
            )

        def make_sign_request(self) -> PresentRequest | None:
            return None if cls.kit_no_sign_request else make_request(request={"method": "GET", "url": AUD})

        def evidence_inbound(self, evidence: Evidence, target: str) -> Inbound:
            return _accepted(target)

        def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound | None:
            return None if cls.kit_no_valid_inbound else _accepted(audience or AUD)

    return Kit()


class ChallengingVerifier(VerifierOnly):
    """Verifies by challenge: a nonce, single use, bound to the audience."""

    scheme: ClassVar[str] = "challenging"
    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_VERIFY})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    replay_reason = audience_reason = "challenge_invalid"
    cap_size = True
    RETURNS_PAYLOAD: ClassVar[bool] = True
    leak_payload = False
    keep_wrapper = False
    echo_wire = False
    fixed_nonce = False
    carry_leak = False
    spend_nonce = True
    bind_audience = True
    kit_hooks: bool | str = True  # False: no challenge hooks; "no_answer", "no_oversized": that one left out

    def __init__(self) -> None:
        self.count = 0
        self.live: set[str] = set()

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> ChallengingVerifier:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls()

    async def verify(self, inbound: Inbound) -> Verified:
        headers = inbound.get("headers", {})
        if "n" not in headers:
            if "payload" in inbound:
                out = Verified(subject="u", claims={}, source="token")
                out["payload"] = f"hello {CANARY}" if self.leak_payload else "hello"
                if self.keep_wrapper:
                    out["payload"] = inbound["payload"]
                return out
            self.count += 1
            nonce = "n1" if self.fixed_nonce else f"n{self.count}"
            self.live.add(nonce)
            carry = {"a2a.metadata_key": CANARY} if self.carry_leak else None
            raise (Echo if self.echo_wire else ChallengeRequired)(
                {"nonce": nonce, "audience": AUD}, carry=carry, wire="WIRE-TEXT"
            )
        if self.cap_size and len(inbound.get("payload", "")) > 1000:
            raise IdentityError("refused", "too_large")
        nonce = headers["n"]
        if nonce not in self.live:
            raise IdentityError("refused", self.replay_reason)
        if self.spend_nonce:
            self.live.discard(nonce)
        if self.bind_audience and headers["a"] != AUD:
            raise IdentityError("refused", self.audience_reason)
        return Verified(subject="u", claims={}, source="token")


class Echo(ChallengeRequired):
    def __repr__(self) -> str:
        return f"challenge_required {self.wire}"


def make_verifier_kit(cls: type[ChallengingVerifier]) -> SchemeConformance:
    """A kit for a challenging verifier; ``cls.kit_hooks`` leaves out the challenge hooks the kit must then demand."""
    hooks = cls.kit_hooks

    def ask() -> Inbound:
        return Inbound(direction="request", headers={}, audience={"protocol": "mcp", "target": AUD})

    def answer(challenge: Mapping[str, Any], audience: str, **extra: Any) -> Inbound:
        return Inbound(
            direction="request",
            headers={"n": challenge["nonce"], "a": audience},
            audience={"protocol": "mcp", "target": AUD},
            **extra,
        )

    class Kit(SchemeConformance):
        def make_scheme(self) -> IdentityScheme:
            return cls()

        def good_params(self) -> dict[str, Any]:
            return {"a": "x"}

        def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound | None:
            try:
                run(cast(Verifier, scheme).verify(self._stamped(ask())))
            except ChallengeRequired as challenge:
                return answer(challenge.challenge, audience or AUD)
            raise AssertionError("the verifier did not challenge")

        def challenge_inbound(self) -> Inbound | None:
            return ask() if hooks is not False else None

        def answer_challenge(self, challenge: Mapping[str, Any], audience: str) -> Inbound | None:
            return answer(challenge, audience) if hooks not in (False, "no_answer") else None

        def oversized_answer(self, challenge: Mapping[str, Any], audience: str) -> Inbound | None:
            return answer(challenge, audience, payload="x" * 5000) if hooks not in (False, "no_oversized") else None

        def message_inbound(self, scheme: IdentityScheme) -> Inbound | None:
            return Inbound(direction="request", payload="[wrapper]wrapped", headers={})

    return Kit()


class Server(VerifierOnly):
    """A resource server that only verifies: it wants a credential and its own audience."""

    scheme: ClassVar[str] = "server"
    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_VERIFY})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Server:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls()

    async def verify(self, inbound: Inbound) -> Verified:
        if not inbound.get("headers", {}).get("authorization"):
            raise IdentityError("refused", "no_credential")
        if inbound.get("audience", {}).get("target") != AUD:
            raise IdentityError("refused", "wrong_audience")
        return Verified(subject="u", claims={}, source="token")


def make_server_kit(cls: type[Server]) -> SchemeConformance:
    class Kit(SchemeConformance):
        def make_scheme(self) -> IdentityScheme:
            return cls()

        def good_params(self) -> dict[str, Any]:
            return {"a": "x"}

        def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound | None:
            return _accepted(audience or AUD)

    return Kit()

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schemes the wiring tests register as plugins: they behave the way a test needs, and know no real credential."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from mas.library.standard.lib.identity.contract import (
    ChallengeRequired,
    Evidence,
    Grant,
    HandshakeContext,
    Handshaker,
    IdentityError,
    IdentityInfo,
    IdentityScheme,
    Inbound,
    PresentRequest,
    Verified,
    Verifier,
)
from mas.library.standard.testing.identity_presentation_exchange import PresentationExchange
from mas.library.standard.testing.identity_scripted import Scripted
from mas.library.standard.testing.identity_signed_badge import SignedBadge
from mas.runtime.secrets import current_resolver

FAR = 4102444800.0  # 2100-01-01


class OneIdentity:
    """A wallet of one identity: what a test scheme needs to be a wallet (``identities`` and ``select``)."""

    def identities(self) -> Sequence[IdentityInfo]:
        return [IdentityInfo(id="main", kind="test", subject="", labels=[], status="active")]

    def select(self, req: PresentRequest) -> str | None:
        return "main"


class TokenPerTask(OneIdentity, IdentityScheme):
    """A bearer made of who, and what for: ``Bearer <prefix>:<actor>:<task>``. Counts what it was asked."""

    scheme: ClassVar[str] = "wiring_token"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"prefix": {"type": "string"}, "challenge_prefix": {"type": "string"}},
    }
    CALLS: ClassVar[list[PresentRequest]] = []

    def __init__(self, prefix: str, challenge_prefix: str) -> None:
        self._prefix = prefix
        self._challenge_prefix = challenge_prefix

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> TokenPerTask:
        return cls(str(params.get("prefix", "tok")), str(params.get("challenge_prefix", "chal")))

    async def present(self, req: PresentRequest) -> Evidence:
        self.CALLS.append(req)
        who = req.get("principal", {}).get("actor", "")
        if req.get("phase") == "challenge":
            return Evidence(headers={"authorization": f"Bearer {self._challenge_prefix}:{who}"})
        task = req.get("task", "")
        return Evidence(
            headers={"authorization": f"Bearer {self._prefix}:{who}:{task}"}, valid_until=FAR, reusable=True
        )


class Raising(OneIdentity, IdentityScheme):
    """A scheme that fails with a message holding a secret: nothing of it may surface."""

    scheme: ClassVar[str] = "wiring_raising"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    SECRET = "s3cr3t-canary-do-not-leak"

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Raising:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        raise RuntimeError(f"backend said no for {self.SECRET}")


class FailsOnToolCall(OneIdentity, IdentityScheme):
    """Presents fine for every request except a tool call, which it refuses with ``refused:no_tools``."""

    scheme: ClassVar[str] = "wiring_fail_on_call"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> FailsOnToolCall:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        if str(req.get("task", "")).startswith("tools/call"):
            raise IdentityError("refused", "no_tools")
        return Evidence(headers={"authorization": "Bearer ok"})


class FailsOnTask(OneIdentity, IdentityScheme):
    """Presents fine except for the request whose operation is named in the params (``on``): that one is refused."""

    scheme: ClassVar[str] = "wiring_fail_on"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object", "properties": {"on": {"type": "string"}}}

    def __init__(self, on: str) -> None:
        self._on = on

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> FailsOnTask:
        return cls(str(params.get("on", "")))

    async def present(self, req: PresentRequest) -> Evidence:
        if str(req.get("task", "")) == self._on:
            raise IdentityError("refused", "no_setup")
        return Evidence(headers={"authorization": "Bearer ok"})


class IssuerBearer(OneIdentity, IdentityScheme, Verifier):
    """Verifies ``x-sub`` as the subject and reports ``x-iss`` (when sent) as ``claims.iss``, as a token one would."""

    scheme: ClassVar[str] = "wiring_issuer"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> IssuerBearer:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence(headers={"authorization": "Bearer ok"})

    async def verify(self, inbound: Inbound) -> Verified:
        headers = inbound.get("headers", {})
        if "x-sub" not in headers:
            raise IdentityError("refused", "no_subject")
        claims: dict[str, Any] = {"iss": headers["x-iss"]} if "x-iss" in headers else {}
        if headers.get("x-bad-iss"):
            claims["iss"] = 7
        return Verified(subject=headers["x-sub"], claims=claims, source="token")


class SingleUse(OneIdentity, IdentityScheme):
    """A credential that can be shown once: ``Bearer use-<n>``, ``n`` counting every presentation. Never cached."""

    scheme: ClassVar[str] = "wiring_single_use"
    capabilities: ClassVar[frozenset[str]] = frozenset({"advertise"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    PRESENTED: ClassVar[list[str]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> SingleUse:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        self.PRESENTED.append(str(req.get("task", "")))
        return Evidence(
            headers={"authorization": f"Bearer use-{len(self.PRESENTED)}"},
            advertise={"mcp.capabilities.experimental": {"io.test/single": {}}},
        )


class Canary(OneIdentity, IdentityScheme):
    """A bearer whose configured secret is in the credential it presents, as a real scheme's would be."""

    scheme: ClassVar[str] = "wiring_canary"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "required": ["key"],
        "properties": {"key": {"x-secret": True}},
    }

    def __init__(self, key: Any) -> None:
        self._key_ref = key

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Canary:
        if not params.get("key"):
            raise IdentityError("invalid", "params")
        return cls(params["key"])

    async def present(self, req: PresentRequest) -> Evidence:
        secret = current_resolver().reveal(self._key_ref)
        return Evidence(headers={"authorization": f"Bearer {secret}"}, valid_until=FAR, reusable=True)


class HeaderBearer(OneIdentity, IdentityScheme, Verifier):
    """Presents ``Bearer <token>``; verifies that the caller showed ``Bearer ok``, else asks for it."""

    scheme: ClassVar[str] = "wiring_bearer"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object", "properties": {"token": {"type": "string"}}}

    def __init__(self, token: str) -> None:
        self._token = token

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> HeaderBearer:
        return cls(str(params.get("token", "ok")))

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence(headers={"authorization": f"Bearer {self._token}"})

    async def verify(self, inbound: Inbound) -> Verified:
        shown = inbound.get("headers", {}).get("authorization")
        if shown is None:
            raise ChallengeRequired({"realm": "wiring"})
        if shown != "Bearer ok":
            raise IdentityError("refused", "bad_token")
        return Verified(subject="u1", claims={}, source="token")


class RefusingHandshake(OneIdentity, IdentityScheme, Handshaker):
    """A scheme whose session handshake always refuses with the code ``refused:xyz``."""

    scheme: ClassVar[str] = "wiring_refusing"
    capabilities: ClassVar[frozenset[str]] = frozenset({"handshake"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> RefusingHandshake:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence()

    async def handshake(self, ctx: HandshakeContext) -> Evidence:
        raise IdentityError("refused", "xyz")


class PeerRecording(OneIdentity, IdentityScheme, Handshaker):
    """Records the ``peer`` of every presentation request, and settles a handshake with no extra fields."""

    scheme: ClassVar[str] = "wiring_peer"
    capabilities: ClassVar[frozenset[str]] = frozenset({"handshake"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    SEEN: ClassVar[list[tuple[str, str, Mapping[str, Any] | None]]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> PeerRecording:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        self.SEEN.append((req.get("task", ""), req.get("phase", "initiate"), req.get("peer")))
        return Evidence()

    async def handshake(self, ctx: HandshakeContext) -> Evidence:
        self.SEEN.append(("handshake", "handshake", dict(ctx["peer"])))
        return Evidence()


class AlwaysChallenge(OneIdentity, IdentityScheme, Verifier):
    """A verifier that answers every request with a challenge, whatever it was shown. Counts both sides."""

    scheme: ClassVar[str] = "wiring_always_challenge"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    VERIFIES: ClassVar[list[int]] = []
    ANSWERS: ClassVar[list[Mapping[str, Any]]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> AlwaysChallenge:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "challenge":
            self.ANSWERS.append(dict(req.get("peer", {})))
        return Evidence(headers={"x-answer": "again"})

    async def verify(self, inbound: Inbound) -> Verified:
        self.VERIFIES.append(1)
        raise ChallengeRequired({"nonce": f"n{len(self.VERIFIES)}", **_audience_of(inbound), "wiring/extra": "no"})


class MisaudiencedExchange(PresentationExchange):
    """A presentation exchange whose verifier issues its challenges for another audience than the one asked."""

    scheme: ClassVar[str] = "wiring_pe_misaudience"
    VERIFIES: ClassVar[list[int]] = []

    def _issue(self, caller: str, request_id: str, now: float) -> ChallengeRequired:
        self.VERIFIES.append(1)
        asked = super()._issue(caller, request_id, now)
        asked.challenge["audience"] = "mallory"
        return asked


class ReplayingExchange(PresentationExchange):
    """A holder that answers every challenge with the first presentation it made: a replay of an observed answer."""

    scheme: ClassVar[str] = "wiring_pe_replaying"
    FIRST: ClassVar[list[Evidence]] = []

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "challenge":
            if not self.FIRST:
                self.FIRST.append(await super().present(req))
            return self.FIRST[0]
        return await super().present(req)


class McpSectionOnA2a(PresentationExchange):
    """The presentation-exchange reference declaring an MCP section where the adapter speaks A2A: it must be refused."""

    scheme: ClassVar[str] = "wiring_pe_mcp_section"

    async def present(self, req: PresentRequest) -> Evidence:
        evidence = await super().present(req)
        if "advertise" in evidence:
            evidence["advertise"] = {"mcp.capabilities.experimental": {"example": {"on": True}}}
        return evidence


class InboundRecorder(OneIdentity, IdentityScheme, Verifier):
    """A verifier that admits every caller and records the inbound request it was handed."""

    scheme: ClassVar[str] = "wiring_inbound_recorder"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    SEEN: ClassVar[list[Inbound]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> InboundRecorder:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence()

    async def verify(self, inbound: Inbound) -> Verified:
        self.SEEN.append(inbound)
        return Verified(subject="u1", claims={}, source="other")


class WireVerifier(OneIdentity, IdentityScheme, Verifier):
    """A verifier for a peer with its own challenge shape: it hands the layer a serialized ``wire`` challenge."""

    scheme: ClassVar[str] = "wiring_wire_verifier"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    WIRE: ClassVar[str] = '{"foreign":"shape","n":1}'

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> WireVerifier:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence()

    async def verify(self, inbound: Inbound) -> Verified:
        carry = {"a2a.metadata_key": "wiring-challenge", "http.auth_scheme": "Wiring"}
        raise ChallengeRequired({"nonce": "n1", **_audience_of(inbound)}, carry=carry, wire=self.WIRE)


class HttpChallenge(OneIdentity, IdentityScheme, Verifier):
    """Both sides of a challenge over HTTP: the holder answers ``peer["challenge"]["nonce"]`` in a bearer header,
    the verifier issues a nonce, admits the answer once, and says who came."""

    scheme: ClassVar[str] = "wiring_http_challenge"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    PEERS: ClassVar[list[Mapping[str, Any]]] = []
    INBOUND: ClassVar[list[Inbound]] = []
    _issued: ClassVar[list[str]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> HttpChallenge:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "challenge":
            peer = dict(req.get("peer", {}))
            self.PEERS.append(peer)
            return Evidence(headers={"authorization": f"Bearer proof:{peer['challenge']['nonce']}"})
        return Evidence()

    async def verify(self, inbound: Inbound) -> Verified:
        self.INBOUND.append(inbound)
        given = inbound.get("headers", {}).get("authorization", "")
        nonce = given.removeprefix("Bearer proof:") if given.startswith("Bearer proof:") else ""
        if nonce and nonce in self._issued:
            self._issued.remove(nonce)  # single use
            return Verified(subject="holder-1", claims={}, source="other")
        issued = f"n{len(self.INBOUND)}"
        self._issued.append(issued)
        raise ChallengeRequired({"nonce": issued, **_audience_of(inbound)})


class AdvisoryBearer(OneIdentity, IdentityScheme, Verifier):
    """A verifier whose challenge is advice, not an exchange: a static Bearer hint (realm, audience, what to show) with
    no nonce, the same for every request. It accepts ``Bearer ok``."""

    scheme: ClassVar[str] = "wiring_advisory"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    ADVISORY_CHALLENGE: ClassVar[bool] = True
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    HINT: ClassVar[Mapping[str, Any]] = {
        "realm": "advice",
        "schemes": ["Bearer"],
        "requirements": ["jwt-svid"],
    }

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> AdvisoryBearer:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence(headers={"authorization": "Bearer ok"})

    async def verify(self, inbound: Inbound) -> Verified:
        if inbound.get("headers", {}).get("authorization") == "Bearer ok":
            return Verified(subject="holder-1", claims={}, source="other")
        raise ChallengeRequired(dict(self.HINT), carry={"http.auth_scheme": "Bearer"})


class AdvisoryCaller(AdvisoryBearer):
    """The advisory verifier, and a holder that shows the wrong thing and records the hint it was given."""

    scheme: ClassVar[str] = "wiring_advisory_caller"
    ANSWERS: ClassVar[list[Mapping[str, Any]]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> AdvisoryCaller:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "challenge":
            self.ANSWERS.append(dict(req.get("peer", {})))
        return Evidence(headers={"authorization": "Bearer nope"})


class Granting(OneIdentity, IdentityScheme, Verifier):
    """Presents ``Bearer granted`` with a grant, and verifies that header as ``alice`` acting through two agents."""

    scheme: ClassVar[str] = "wiring_granting"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify", "revoke"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    GRANT: ClassVar[Grant] = Grant(
        scopes=["rail", "pay:eur"], audience="https://pay.example:8443/mcp?token=x", valid_until=FAR, depth=1
    )

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Granting:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence(headers={"authorization": "Bearer granted"}, valid_until=FAR, reusable=True, grant=self.GRANT)

    async def revoke(self, ref: str) -> None:
        return None

    async def verify(self, inbound: Inbound) -> Verified:
        if inbound.get("headers", {}).get("authorization") != "Bearer granted":
            raise IdentityError("refused", "no_grant")
        return Verified(
            subject="alice", claims={}, source="token", actors=["orchestrator", "agent-a"], grant=self.GRANT
        )


class DataAnswer(OneIdentity, IdentityScheme, Verifier):
    """A verifier whose answer is a data part of the message (the reserved ``Inbound.meta`` key). Records the meta."""

    scheme: ClassVar[str] = "wiring_data_answer"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    KEY: ClassVar[str] = "mas.a2a/data_parts"
    SEEN: ClassVar[list[Mapping[str, Any]]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> DataAnswer:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        return Evidence()

    async def verify(self, inbound: Inbound) -> Verified:
        meta = dict(inbound.get("meta", {}))
        self.SEEN.append(meta)
        parts = meta.get(self.KEY)
        if not parts:
            raise IdentityError("refused", "no_answer")
        if parts[0].get("answer") != "ok":
            raise IdentityError("refused", "bad_answer")
        return Verified(subject=str(parts[0].get("subject", "u1")), claims={}, source="other")


class WireExchange(OneIdentity, IdentityScheme, Verifier):
    """A peer with its own challenge shape: the verifier issues a ``wire`` challenge (and ``carry``), and the holder
    answers from the ``wire`` alone, so a transport that drops it cannot be answered."""

    scheme: ClassVar[str] = "wiring_wire_exchange"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}
    WIRE: ClassVar[str] = json.dumps({"foreign": "shape", "n": 7, "pad": "x" * 6000})
    CARRY: ClassVar[Mapping[str, str]] = {"a2a.metadata_key": "wiring-challenge", "http.auth_scheme": "Wiring"}
    PEERS: ClassVar[list[Mapping[str, Any]]] = []

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> WireExchange:
        return cls()

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") != "challenge":
            return Evidence()
        peer = dict(req.get("peer", {}))
        self.PEERS.append(peer)
        wire = peer.get("wire")
        if not isinstance(wire, str):
            raise IdentityError("refused", "no_wire")
        return Evidence(headers={"x-answer": f"n{json.loads(wire)['n']}"})

    async def verify(self, inbound: Inbound) -> Verified:
        if inbound.get("headers", {}).get("x-answer") == "n7":
            return Verified(subject="u1", claims={}, source="other")
        raise ChallengeRequired({"nonce": "n1", **_audience_of(inbound)}, carry=dict(self.CARRY), wire=self.WIRE)


def _audience_of(inbound: Inbound) -> dict[str, str]:
    """The audience a verifier binds its challenge to: the target the host says the caller reached, when it knows one
    (the holder answers a challenge only for the audience it asked)."""
    target = inbound.get("audience", {}).get("target")
    return {"audience": str(target)} if target else {}


SCHEMES = {
    "scripted": Scripted,
    "signed_badge": SignedBadge,
    "presentation_exchange": PresentationExchange,
    "wiring_advisory_caller": AdvisoryCaller,
    "wiring_advisory": AdvisoryBearer,
    "wiring_data_answer": DataAnswer,
    "wiring_wire_exchange": WireExchange,
    "wiring_granting": Granting,
    "wiring_inbound_recorder": InboundRecorder,
    "wiring_wire_verifier": WireVerifier,
    "wiring_http_challenge": HttpChallenge,
    "wiring_pe_mcp_section": McpSectionOnA2a,
    "wiring_always_challenge": AlwaysChallenge,
    "wiring_pe_misaudience": MisaudiencedExchange,
    "wiring_pe_replaying": ReplayingExchange,
    "wiring_peer": PeerRecording,
    "wiring_refusing": RefusingHandshake,
    "wiring_bearer": HeaderBearer,
    "wiring_token": TokenPerTask,
    "wiring_raising": Raising,
    "wiring_canary": Canary,
    "wiring_fail_on_call": FailsOnToolCall,
    "wiring_single_use": SingleUse,
    "wiring_fail_on": FailsOnTask,
    "wiring_issuer": IssuerBearer,
}

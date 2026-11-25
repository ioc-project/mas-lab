#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A reference presentation-exchange scheme: an in-process fake of a verifiable-presentation flow.

One scheme plays both roles so a test can wire a holder and a verifier with no network and no
credential library:

1. the verifier, shown nothing, raises ``ChallengeRequired`` with a request
   ``{nonce, audience, requirements}`` (single-use nonce, bound to one audience);
2. the holder answers through ``handshake``: a presentation of the claims the request asks for, signed
   (HMAC) over ``(nonce, audience, claims)``, sent on the session channel;
3. the verifier, shown a presentation (header ``x-presentation``, or the message text wrapped in an
   envelope on A2A), checks the nonce is issued and unspent, the audience matches, the proof is valid and
   every requirement is satisfied, then returns ``Verified`` (with the message text, identity layer removed).

The verifier follows the hardening checklist of the scheme guide: the nonce is spent before any signature work;
it is bound to the caller (``Inbound.session``), the task (``Inbound.task_id``, else the request id
``Inbound.request_id``) and the verifier's own audience; each caller holds a few live nonces and eviction
stays inside that caller's slot; unknown, expired,
replayed and wrongly bound nonces get one refusal; the audience is the verifier's own record, never the
presented token's; the algorithm is allow-listed; presentation and challenge are size-capped; errors carry no
token text or claim value; a verifier with an empty policy refuses to start.

Nonces come from a counter, never from randomness, so runs replay. This is a fake: the signature scheme
and message shapes exist to exercise the interfaces, not to interoperate with anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from mas.library.standard.lib.identity.contract import (
    ChallengeRequired,
    Describer,
    Evidence,
    HandshakeContext,
    Handshaker,
    IdentityError,
    IdentityInfo,
    IdentityScheme,
    Inbound,
    InteractionRequired,
    PresentRequest,
    PublicView,
    Revoker,
    Verified,
    Verifier,
)
from mas.library.standard.lib.identity.rules import validate
from mas.runtime.secrets import current_resolver

PRESENTATION_HEADER = "x-presentation"
SUBMIT_METHOD = "presentation/submit"
META_SESSION = "presentationexchange/session"
ALGORITHMS = frozenset({"hmac-sha256"})
MAX_PRESENTATION_CHARS = 8192
MAX_CLAIMS = 32
MAX_LIVE_NONCES = 4
REFUSED_CHALLENGE = "challenge_invalid"
CARRY = {"a2a.metadata_key": "presentation-exchange-challenge"}


def _advertise(protocol: str) -> dict[str, Any]:
    """What the holder declares to a peer, under the section of the protocol it speaks (none for another)."""
    if protocol == "a2a":
        return {"a2a.extensions": ["urn:example:presentation-exchange"]}
    if protocol == "mcp":
        return {"mcp.capabilities.extensions": {"urn:example:presentation-exchange": {"holder": True}}}
    return {}


def challenge_in(peer: Mapping[str, Any]) -> Any:
    """The challenge a peer declared: ``peer["challenge"]``, the one place every transport puts it."""
    return peer.get("challenge")


def sign_presentation(key: str, nonce: str, audience: str, claims: Mapping[str, Any]) -> str:
    """HMAC-SHA256 over the canonical JSON of ``[nonce, audience, claims]``."""
    message = json.dumps([nonce, audience, dict(claims)], sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


class PresentationExchange(IdentityScheme, Handshaker, Verifier, Describer, Revoker):
    """Holder (``handshake``, ``present``) and verifier (``verify``) of a nonce-and-audience-bound presentation."""

    scheme: ClassVar[str] = "presentation_exchange"
    RETURNS_PAYLOAD: ClassVar[bool] = True  # a message on A2A is unwrapped: the text without the presentation
    capabilities: ClassVar[frozenset[str]] = frozenset({"handshake", "verify", "describe", "revoke"})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "required": ["key", "subject"],
        "additionalProperties": False,
        "properties": {
            "key": {"x-secret": True},
            "subject": {"type": "string", "minLength": 1, "maxLength": 128},
            "claims": {"type": "object"},
            "requirements": {"type": "array", "items": {"type": "string"}},
            "require_values": {"type": "object"},
            "pending_polls": {"type": "integer", "minimum": 0, "maximum": 10},
            "audience": {"type": "string", "minLength": 1, "maxLength": 256},
            "ttl": {"type": "number", "minimum": 10},
        },
    }

    def __init__(
        self,
        key: str,
        subject: str,
        claims: Mapping[str, Any],
        requirements: list[str],
        audience: str,
        ttl: float,
        require_values: Mapping[str, Any] | None = None,
        pending_polls: int = 0,
    ) -> None:
        self._revoked = False
        self._require_values = dict(require_values or {})
        self._pending_polls = pending_polls
        self._key_ref = key  # a SecretRef: revealed only where it signs or checks
        self._subject = subject
        self._claims = dict(claims)
        self._requirements = list(requirements)
        self._audience = audience
        self._ttl = ttl
        self._counter = 0
        # caller key -> live nonces (oldest first) -> (task or request id, expires_at)
        self._live: dict[str, OrderedDict[str, tuple[str, float]]] = {}

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> PresentationExchange:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        audience = str(params.get("audience", ""))
        requirements = list(params.get("requirements", []))
        if audience and not requirements:
            raise IdentityError("invalid", "empty_policy")  # a verifier with no policy admits anyone: refuse to start
        return cls(
            params["key"],
            str(params["subject"]),
            params.get("claims", {}),
            requirements,
            audience,
            float(params.get("ttl", 30.0)),
            params.get("require_values", {}),
            int(params.get("pending_polls", 0)),
        )

    def __repr__(self) -> str:
        return f"PresentationExchange(audience={self._audience!r})"

    # --- holder ---------------------------------------------------------------------------

    def _submission(self, request: Any, audience: str) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise IdentityError("refused", "no_request")
        nonce = request.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise IdentityError("refused", "no_nonce")
        if request.get("audience") != audience:
            raise IdentityError("refused", "wrong_audience")
        wanted = request.get("requirements", [])
        if not isinstance(wanted, list) or any(w not in self._claims for w in wanted):
            raise IdentityError("unsatisfiable", "requirements_unmet")
        claims = {name: self._claims[name] for name in wanted}
        return {
            "alg": "hmac-sha256",
            "nonce": nonce,
            "audience": audience,
            "subject": self._subject,
            "claims": claims,
            "proof": sign_presentation(self._reveal_key(), nonce, audience, claims),
        }

    def _reveal_key(self) -> str:
        return current_resolver().reveal(self._key_ref)

    def identities(self) -> Sequence[IdentityInfo]:
        status = "revoked" if self._revoked else "active"
        return [IdentityInfo(id="exchange", kind="exchange", subject=self._subject, labels=[], status=status)]

    def select(self, req: PresentRequest) -> str | None:
        return None if self._revoked else "exchange"

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("identity", "exchange") != "exchange":
            raise IdentityError("no_credential")
        if req.get("phase") == "sign":
            raise IdentityError("unsatisfiable", "no_sign")
        if self._revoked:
            raise IdentityError("revoked")
        until = float(req["now"]) + self._ttl
        view = req.get("request", {})
        if req.get("phase") == "challenge":
            if self._pending_polls > 0:
                # a human step first (approve on a device): the first polls say so, a later one succeeds
                self._pending_polls -= 1
                raise InteractionRequired(handle="approval-1", hint="approve the request, then send again")
            submitted = self._submission(challenge_in(req.get("peer", {})), req["audience"]["target"])
            if "body_text" in view:
                # message-level evidence: the text, wrapped with the presentation, replaces the outgoing text
                return Evidence(payload=wrap_message(view["body_text"], submitted), valid_until=until, reusable=False)
            return Evidence(headers={PRESENTATION_HEADER: json.dumps(submitted)}, valid_until=until, reusable=False)
        return Evidence(
            meta={"presentationexchange/declaration": {"subject_kind": "holder"}},
            advertise=_advertise(req["audience"]["protocol"]),
            valid_until=until,
            reusable=False,
        )

    async def handshake(self, ctx: HandshakeContext) -> Evidence:
        if self._revoked:
            raise IdentityError("revoked")
        audience = ctx["audience"]["target"]
        submitted = self._submission(challenge_in(ctx["peer"]), audience)
        reply = await ctx["channel"].request(SUBMIT_METHOD, submitted)
        session = reply.get("session")
        if not isinstance(session, str) or not session:
            raise IdentityError("refused", "not_admitted")
        return Evidence(meta={META_SESSION: session}, valid_until=float(ctx["now"]) + self._ttl, reusable=False)

    # --- verifier -------------------------------------------------------------------------

    def _issue(self, caller: str, binder: str, now: float) -> ChallengeRequired:
        self._counter += 1
        nonce = f"pe-nonce-{self._counter}"
        expires = now + self._ttl
        slot = self._live.setdefault(caller, OrderedDict())
        while len(slot) >= MAX_LIVE_NONCES:
            slot.popitem(last=False)  # eviction stays inside this caller's own slot
        slot[nonce] = (binder, expires)
        challenge = {
            "nonce": nonce,
            "audience": self._audience,
            "requirements": list(self._requirements),
            "expires_at": expires,
        }
        return ChallengeRequired(challenge, carry=CARRY)

    async def verify(self, inbound: Inbound) -> Verified:
        if not self._audience or not self._requirements:
            raise IdentityError("refused", "not_a_verifier")
        now = inbound.get("now")
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise IdentityError("unavailable", "no_clock")
        caller = str(inbound.get("session", ""))
        # a retry after a challenge is a new message on the same task: bind to the task when there is one
        binder = str(inbound.get("task_id", "")) or str(inbound.get("request_id", ""))
        raw = inbound.get("headers", {}).get(PRESENTATION_HEADER)
        text: str | None = None
        if raw is None:
            payload = inbound.get("payload")
            envelope = unwrap_message(payload) if isinstance(payload, str) else None
            if envelope is None:
                raise self._issue(caller, binder, float(now))
            raw, text = envelope
        if len(raw) > MAX_PRESENTATION_CHARS:
            raise IdentityError("refused", "too_large")
        try:
            submitted = json.loads(raw)
        except ValueError:
            raise IdentityError("refused", "malformed") from None
        if not isinstance(submitted, dict):
            raise IdentityError("refused", "malformed")
        nonce = submitted.get("nonce")
        if not isinstance(nonce, str):
            raise IdentityError("refused", REFUSED_CHALLENGE)
        # spend the nonce before any signature work, whatever follows
        entry = self._live.get(caller, OrderedDict()).pop(nonce, None)
        if entry is None or float(now) >= entry[1] or entry[0] != binder:
            raise IdentityError("refused", REFUSED_CHALLENGE)
        if submitted.get("audience") != self._audience:  # the verifier's own record decides, not the token
            raise IdentityError("refused", REFUSED_CHALLENGE)
        if submitted.get("alg") not in ALGORITHMS:
            raise IdentityError("refused", "algorithm_not_allowed")
        claims = submitted.get("claims")
        if not isinstance(claims, dict) or len(claims) > MAX_CLAIMS:
            raise IdentityError("refused", "malformed")
        proof = submitted.get("proof")
        expected = sign_presentation(self._reveal_key(), nonce, self._audience, claims)
        if not isinstance(proof, str) or not hmac.compare_digest(expected, proof):
            raise IdentityError("refused", "bad_proof")
        if any(name not in claims for name in self._requirements):
            raise IdentityError("refused", "requirements_unmet")
        if any(claims.get(name) != value for name, value in self._require_values.items()):
            raise IdentityError("refused", "requirements_unmet")
        subject = submitted.get("subject")
        if not isinstance(subject, str) or not subject:
            raise IdentityError("refused", "malformed")
        verified = Verified(subject=subject, claims=claims, source="badge")
        if text is not None:
            verified["payload"] = text  # the message text with the identity layer removed
        return verified

    async def describe(self, ref: str) -> PublicView:
        return PublicView(
            facts={
                **self._claims,
                "subject": self._subject,
                "claim_names": sorted(self._claims),
                "single_use": True,
                **({"revoked": True} if self._revoked else {}),
            },
            advertise={"presentationexchange": {"requirements": list(self._requirements)}},
        )

    async def revoke(self, ref: str) -> None:
        """No issuer to tell in this fake: the holder stops presenting, and its public view says so."""
        self._revoked = True


def wrap_message(text: str, submitted: Mapping[str, Any]) -> str:
    """The message text wrapped with a presentation, as the holder sends it on A2A."""
    return json.dumps({"presentation": dict(submitted), "text": text}, separators=(",", ":"))


def unwrap_message(payload: str) -> tuple[str, str] | None:
    """``(presentation json, text)`` from a wrapped message, or ``None`` when the text carries no presentation."""
    try:
        envelope = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("presentation"), dict):
        return None
    text = envelope.get("text")
    if not isinstance(text, str):
        return None
    return json.dumps(envelope["presentation"], separators=(",", ":")), text


def submit_channel(
    verifier: PresentationExchange, audience: str, *, session: str = "", now: float = 1_000_000.0
) -> _SubmitChannel:
    """A channel that hands a holder's submission to ``verifier`` as an inbound request."""
    return _SubmitChannel(verifier, audience, session, now)


class _SubmitChannel:
    def __init__(self, verifier: PresentationExchange, audience: str, session: str, now: float) -> None:
        self._verifier = verifier
        self._audience = audience
        self._session = session
        self._now = now

    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]:
        inbound = Inbound(
            direction="request",
            headers={PRESENTATION_HEADER: json.dumps(dict(params))},
            audience={"protocol": "mcp", "target": self._audience},
            session=self._session,
            now=self._now,
        )
        try:
            verified = await self._verifier.verify(inbound)
        except IdentityError as exc:
            return {"reason": exc.reason or exc.code}
        return {"session": f"session-for-{verified['subject']}"}

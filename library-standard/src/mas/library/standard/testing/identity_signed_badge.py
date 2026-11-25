#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A reference signed-badge scheme: an in-process fake of a verifiable presentation carried in one header.

A real verifiable-presentation exchange asks the holder for proof with a one-time number and checks the signed
answer once. That needs a reply on the same task, which a link that carries only headers (A2A over SLIM) cannot
give. This fake keeps the part that fits a header: the holder presents ``subject``, the claims and an HMAC over
both, made with an issuer key; the verifier recomputes the HMAC offline and checks that every required claim is
there. It also answers ``describe`` (the claims are public facts in this fake), can be ``revoke``-d, and can ask
for a human step first (``pending_polls``), so a gate and a human-in-the-loop have something to exercise.

This is a fake: the signature and the header exist to exercise the interfaces, not to interoperate with anything.
A real implementation is a separate plugin that passes the same conformance kit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from mas.library.standard.lib.identity.contract import (
    Describer,
    Evidence,
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

BADGE_HEADER = "x-badge"
DEFAULT_TTL = 300.0


def _canonical(subject: str, claims: Mapping[str, Any]) -> bytes:
    return json.dumps([subject, dict(claims)], sort_keys=True, separators=(",", ":")).encode()


def sign_badge(key: str, subject: str, claims: Mapping[str, Any]) -> str:
    """The header value: ``base64(json[subject, claims]) . hmac-sha256``."""
    body = _canonical(subject, claims)
    return base64.urlsafe_b64encode(body).decode() + "." + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


class SignedBadge(IdentityScheme, Verifier, Describer, Revoker):
    """Holder (``present``) and verifier (``verify``) of a signed badge in the ``x-badge`` header."""

    scheme: ClassVar[str] = "signed_badge"
    capabilities: ClassVar[frozenset[str]] = frozenset({"verify", "describe", "revoke"})
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
            "audience": {"type": "string", "minLength": 1, "maxLength": 256},
            "ttl": {"type": "number", "minimum": 30},
            "pending_polls": {"type": "integer", "minimum": 0, "maximum": 10},
        },
    }

    def __init__(
        self,
        key: str,
        subject: str,
        claims: Mapping[str, Any],
        requirements: list[str],
        ttl: float,
        pending_polls: int,
        require_values: Mapping[str, Any] | None = None,
        audience: str = "",
    ) -> None:
        self._audience = audience
        self._require_values = dict(require_values or {})
        self._key_ref = key  # a SecretRef: revealed only where it signs or checks
        self._subject = subject
        self._claims = dict(claims)
        self._requirements = list(requirements)
        self._ttl = ttl
        self._pending_polls = pending_polls
        self._revoked = False

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> SignedBadge:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls(
            params["key"],
            str(params["subject"]),
            params.get("claims", {}),
            list(params.get("requirements", [])),
            float(params.get("ttl", DEFAULT_TTL)),
            int(params.get("pending_polls", 0)),
            params.get("require_values", {}),
            str(params.get("audience", "")),
        )

    def __repr__(self) -> str:
        return f"SignedBadge(subject={self._subject!r})"

    def _reveal_key(self) -> str:
        return current_resolver().reveal(self._key_ref)

    def identities(self) -> Sequence[IdentityInfo]:
        status = "revoked" if self._revoked else "active"
        return [IdentityInfo(id="badge", kind="badge", subject=self._subject, labels=[], status=status)]

    def select(self, req: PresentRequest) -> str | None:
        return None if self._revoked else "badge"

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("identity", "badge") != "badge":
            raise IdentityError("no_credential")
        if req.get("phase") == "sign":
            raise IdentityError("unsatisfiable", "no_sign")
        if self._revoked:
            raise IdentityError("revoked")
        if self._pending_polls > 0:
            # a human step (approve the request in a wallet): the first asks wait, the next one succeeds
            self._pending_polls -= 1
            raise InteractionRequired(f"badge-approval-{self._pending_polls}", "approve the request in your wallet")
        return Evidence(
            headers={BADGE_HEADER: sign_badge(self._reveal_key(), self._subject, self._claims)},
            valid_until=float(req["now"]) + self._ttl,
            reusable=True,
        )

    async def verify(self, inbound: Inbound) -> Verified:
        raw = inbound.get("headers", {}).get(BADGE_HEADER)
        if raw is None:
            raise IdentityError("refused", "no_badge")
        if self._audience and inbound.get("audience", {}).get("target") != self._audience:
            raise IdentityError("refused", "wrong_audience")  # the verifier's own record decides, not the badge
        body_b64, _, proof = str(raw).partition(".")
        try:
            body = base64.urlsafe_b64decode(body_b64.encode())
            subject, claims = json.loads(body)
        except (ValueError, TypeError):
            raise IdentityError("refused", "malformed") from None
        expected = hmac.new(self._reveal_key().encode(), body, hashlib.sha256).hexdigest()
        if not isinstance(subject, str) or not isinstance(claims, dict) or not hmac.compare_digest(expected, proof):
            raise IdentityError("refused", "bad_proof")
        if any(name not in claims for name in self._requirements):
            raise IdentityError("refused", "requirements_unmet")
        if any(claims.get(name) != value for name, value in self._require_values.items()):
            raise IdentityError("refused", "requirements_unmet")
        return Verified(subject=subject, claims=claims, source="badge")

    async def describe(self, ref: str) -> PublicView:
        facts = {
            **self._claims,
            "subject": self._subject,
            "claim_names": sorted(self._claims),
            "revoked": self._revoked,
        }
        return PublicView(facts=facts, advertise={"signedbadge": {"requirements": list(self._requirements)}})

    async def revoke(self, ref: str) -> None:
        self._revoked = True

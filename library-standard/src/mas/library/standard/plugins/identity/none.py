#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The explicit "no identity" scheme: a wallet of one identity that accepts everyone.

Using it is a declared choice, not an accident: the record says ``scheme: none``, a call presents nothing, and an
inbound caller is verified as ``anonymous``. A policy can read that from ``describe`` and decide. It exercises the
contract, the host's binding and the events of an identity layer without any credential.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from mas.library.standard.lib.identity.contract import (
    CAP_DESCRIBE,
    CAP_VERIFY,
    Describer,
    Evidence,
    IdentityError,
    IdentityInfo,
    IdentityScheme,
    Inbound,
    PresentRequest,
    PublicView,
    Verified,
    Verifier,
)
from mas.library.standard.lib.identity.rules import validate

ANONYMOUS = "anonymous"
DEFAULT_VALID_FOR = 300.0


class NoIdentity(IdentityScheme, Verifier, Describer):
    """Presents nothing; verifies everyone as anonymous."""

    scheme: ClassVar[str] = "none"
    ANONYMOUS_OK: ClassVar[bool] = True
    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_VERIFY, CAP_DESCRIBE})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> NoIdentity:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls()

    def identities(self) -> Sequence[IdentityInfo]:
        return [IdentityInfo(id=ANONYMOUS, kind="none", subject="", labels=[], status="active")]

    def select(self, req: PresentRequest) -> str | None:
        return ANONYMOUS

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("phase") == "sign":
            raise IdentityError("unsatisfiable", "no_sign")
        if req.get("identity", ANONYMOUS) != ANONYMOUS:
            raise IdentityError("no_credential")
        return Evidence(headers={}, valid_until=float(req["now"]) + DEFAULT_VALID_FOR, reusable=True)

    async def verify(self, inbound: Inbound) -> Verified:
        return Verified(subject=ANONYMOUS, claims={"anonymous": True}, source="other")

    async def describe(self, ref: str) -> PublicView:
        return PublicView(facts={"scheme": self.scheme, "anonymous": True})

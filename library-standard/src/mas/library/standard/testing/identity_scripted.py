#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A scripted scheme: a queue of evidence and errors, a record of every request.

Deterministic and offline, so a run that uses it replays. It declares every capability but ``delegate`` (a double has
no authority of its own to narrow) and answers each from its own script. Its wallet holds one identity (``scripted``).
An empty queue raises ``no_credential``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, TypeAlias

from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAPABILITIES,
    Describer,
    Evidence,
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
from mas.library.standard.lib.identity.rules import validate

ScriptedItem: TypeAlias = Evidence | Exception

IDENTITY_ID = "scripted"


class Scripted(IdentityScheme, Handshaker, Verifier, Describer, Revoker):
    """Replays what it was given.

    * ``evidence``: consumed one per ``present``; ``repeat_last`` keeps the last one for ever.
    * ``handshakes`` and ``signs``: the same, for ``handshake`` and for ``present`` on phase ``sign``.
    * ``verified``: what ``verify`` returns, or an exception to raise.
    * ``view``: what ``describe`` returns.
    * ``requests``, ``handshake_contexts``, ``sign_requests``, ``inbounds``, ``revoked``: what it was asked.
    """

    scheme: ClassVar[str] = "scripted"
    ANONYMOUS_OK: ClassVar[bool] = True  # a double returns what it was given, whoever calls
    capabilities: ClassVar[frozenset[str]] = CAPABILITIES - {CAP_DELEGATE}
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evidence": {"type": "array", "items": {"type": "object"}},
            "repeat_last": {"type": "boolean"},
        },
    }

    def __init__(
        self,
        evidence: Sequence[ScriptedItem] = (),
        *,
        handshakes: Sequence[ScriptedItem] = (),
        signs: Sequence[ScriptedItem] = (),
        verified: Verified | Exception | None = None,
        view: PublicView | None = None,
        repeat_last: bool = False,
    ) -> None:
        self._queue = list(evidence)
        self._handshakes = list(handshakes)
        self._signs = list(signs)
        self._repeat_last = repeat_last
        self._verified = verified
        self._view = view if view is not None else PublicView(facts={"scheme": "scripted"})
        self.requests: list[PresentRequest] = []
        self.handshake_contexts: list[HandshakeContext] = []
        self.sign_requests: list[PresentRequest] = []
        self.inbounds: list[Inbound] = []
        self.revoked: list[str] = []
        self._is_revoked = False

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> Scripted:
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls(
            [Evidence(**p) for p in params.get("evidence", [])],
            repeat_last=bool(params.get("repeat_last", False)),
        )

    def identities(self) -> Sequence[IdentityInfo]:
        status = "revoked" if self._is_revoked else "active"
        return [IdentityInfo(id=IDENTITY_ID, kind="scripted", subject="", labels=[], status=status)]

    def select(self, req: PresentRequest) -> str | None:
        return None if self._is_revoked else IDENTITY_ID

    def _next(self, queue: list[ScriptedItem]) -> Evidence:
        if self._is_revoked:
            raise IdentityError("revoked")
        if not queue:
            raise IdentityError("no_credential")
        item = queue[0] if (self._repeat_last and len(queue) == 1) else queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)

    async def present(self, req: PresentRequest) -> Evidence:
        if req.get("identity", IDENTITY_ID) != IDENTITY_ID:
            raise IdentityError("no_credential")
        if req.get("phase") == "sign":
            self.sign_requests.append(copy.deepcopy(req))
            return self._next(self._signs)
        self.requests.append(copy.deepcopy(req))
        return self._next(self._queue)

    async def handshake(self, ctx: HandshakeContext) -> Evidence:
        self.handshake_contexts.append(ctx)
        return self._next(self._handshakes)

    async def verify(self, inbound: Inbound) -> Verified:
        self.inbounds.append(copy.deepcopy(inbound))
        if isinstance(self._verified, Exception):
            raise self._verified
        if self._verified is None:
            raise IdentityError("refused", "no_script")
        return copy.deepcopy(self._verified)

    async def describe(self, ref: str) -> PublicView:
        return copy.deepcopy(self._view)

    async def revoke(self, ref: str) -> None:
        self.revoked.append(ref)
        self._is_revoked = True

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A base class for resource-server schemes that only verify.

A server that checks callers but never calls anyone has no identity to present. Subclass :class:`VerifierOnly`,
implement ``verify`` (and ``describe`` or ``revoke`` if you offer them) and list the capabilities you add:

    class MyServerScheme(VerifierOnly):
        scheme = "my_server"
        capabilities = frozenset({"verify", "describe"})
        PARAMS_SCHEMA = {...}

        @classmethod
        def from_spec(cls, params): ...
        async def verify(self, inbound): ...

Its wallet is empty (``identities`` is ``[]``, ``select`` answers ``None``) and ``present`` refuses with
``unsatisfiable:no_present``. The conformance kit recognises a ``VerifierOnly`` and does not require a successful
present.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from mas.library.standard.lib.identity.contract import (
    CAP_VERIFY,
    Evidence,
    IdentityError,
    IdentityInfo,
    IdentityScheme,
    PresentRequest,
    Verifier,
)


class VerifierOnly(IdentityScheme, Verifier):
    """A scheme that verifies and never presents."""

    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_VERIFY})

    def identities(self) -> Sequence[IdentityInfo]:
        return []

    def select(self, req: PresentRequest) -> str | None:
        return None

    async def present(self, req: PresentRequest) -> Evidence:
        raise IdentityError("unsatisfiable", "no_present")

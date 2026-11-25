#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The agent's own view of its wallets: list, describe, delegate and revoke, on handles only.

A :class:`Surface` is what a plugin, a host or the model's tools use to manage the identities an agent holds. It
reaches the wallets the identity layer registered for the agent (``holders``) and keeps nothing of its own, so there is
no state to share and nothing to persist.

Everything that comes out is value-free: identity ids, kinds, opaque subject references, labels, status, a grant
summary and the public facts of ``describe``. Evidence, headers, tokens, the attestation of a view and every secret
stay inside the caller. A refusal is an :class:`~mas.library.standard.lib.identity.contract.IdentityError`, whose text
is a code and never a value.

``delegate`` takes the scopes the new identity may use and an optional lifetime. The surface builds the rest of the
grant from the parent's (its audience and expiry, one more hop of depth), so the model never writes the parts that
decide whether a grant narrows. The wallet narrows it and the caller checks that it did.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from mas.library.standard.lib.identity import holders
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAP_DESCRIBE,
    CAP_REVOKE,
    Delegation,
    Grant,
    IdentityError,
    IdentityInfo,
)
from mas.library.standard.lib.identity.principal import current_principal

#: The wallet capabilities a listing reports, so a caller knows what to ask of an identity.
_REPORTED = (CAP_DELEGATE, CAP_DESCRIBE, CAP_REVOKE)


class Surface:
    """The identities one agent holds, as a value-free API. See the module text."""

    def __init__(self, agent_id: str, *, scope: str = "", clock: Callable[[], float] = time.time) -> None:
        self._agent = str(agent_id or "")
        self._scope = str(scope or "")
        self._clock = clock

    @property
    def agent(self) -> str:
        return self._agent

    @property
    def scope(self) -> str:
        return self._scope

    def __repr__(self) -> str:
        return f"Surface(agent={self._agent!r})"

    # --- which wallets -------------------------------------------------------------------------------

    def _wallets(self, record: str | None) -> list[tuple[str, Caller]]:
        """The ``(record, wallet)`` pairs of this agent: the record named, else every record it holds."""
        records = [record] if record else holders.records_of(self._agent, self._scope)
        out: list[tuple[str, Caller]] = []
        for name in records:
            held = holders.wallets(self._agent, name, self._scope)
            out.extend((name, caller) for caller in held)
        if record and not out:
            raise IdentityError("no_credential", "no_wallet")
        return out

    def _listed(self, record: str | None) -> dict[tuple[str, str], list[tuple[Caller, IdentityInfo]]]:
        """Each ``(record, id)`` the agent's wallets list, with every wallet that lists it (one target, not several)."""
        out: dict[tuple[str, str], list[tuple[Caller, IdentityInfo]]] = {}
        for name, caller in self._wallets(record):
            for info in caller.identities():
                out.setdefault((name, info["id"]), []).append((caller, info))
        return out

    def _find(self, identity: str, record: str | None) -> tuple[str, list[tuple[Caller, IdentityInfo]]]:
        """The wallets that list ``identity``. Two records listing the same id need the record named."""
        found = [(key[0], held) for key, held in self._listed(record).items() if key[1] == identity]
        if not found:
            raise IdentityError("no_credential", "unknown_identity")
        if len(found) > 1:
            raise IdentityError("invalid", "ambiguous_identity")
        return found[0]

    @staticmethod
    def _public(record: str, held: list[tuple[Caller, IdentityInfo]]) -> dict[str, Any]:
        """What may leave: the listed fields, the record, whether any wallet knows it revoked and what it can do."""
        info = held[0][1]
        out: dict[str, Any] = {"record": record, **info}
        if any(caller.is_revoked(info["id"]) or other["status"] == "revoked" for caller, other in held):
            out["status"] = "revoked"
        out["can"] = sorted(cap for cap in _REPORTED if all(cap in caller.capabilities for caller, _ in held))
        return out

    # --- the operations ------------------------------------------------------------------------------

    def identities(self, record: str | None = None) -> list[dict[str, Any]]:
        """Every identity of the agent's wallets (or of one ``record``), value-free."""
        return [self._public(key[0], held) for key, held in self._listed(record).items()]

    async def describe(self, identity: str, record: str | None = None) -> dict[str, Any]:
        """The public facts of ``identity``. The view's attestation and advertisement are not passed on."""
        name, held = self._find(identity, record)
        view = await held[0][0].describe(identity)
        return {"record": name, "identity": identity, "facts": dict(view.get("facts", {}))}

    async def delegate(
        self,
        identity: str,
        scopes: Sequence[str],
        *,
        record: str | None = None,
        ttl_s: float | None = None,
    ) -> dict[str, Any]:
        """Derive a narrower identity from ``identity``; return its value-free listing.

        ``scopes`` are what the new identity may use. ``ttl_s`` is its lifetime from now (absent: the parent's
        expiry, when it has one). A grant that widens the parent's is refused by the caller.
        """
        name, wallets = self._find(identity, record)
        caller, parent = wallets[0]
        held: Grant = parent.get("grant", Grant())
        grant = Grant(scopes=sorted(dict.fromkeys(scopes)), depth=int(held.get("depth", 0)) + 1)
        if "audience" in held:
            grant["audience"] = held["audience"]
        if ttl_s is not None:
            grant["valid_until"] = float(self._clock()) + float(ttl_s)
        elif "valid_until" in held:
            grant["valid_until"] = held["valid_until"]
        principal = dict(current_principal())
        principal.setdefault("actor", self._agent)
        child = await caller.delegate(Delegation(identity=identity, grant=grant, principal=principal))
        return self._public(name, [(caller, child)])

    async def revoke(self, identity: str, record: str | None = None) -> dict[str, Any]:
        """Revoke ``identity``: the wallet is told, nothing is presented for it again, held sessions close."""
        name, held = self._find(identity, record)
        for caller, _info in held:  # every wallet that lists it: one target, so all are told
            await caller.revoke(identity)
        return {"record": name, "identity": identity, "status": "revoked"}


__all__ = ["Surface"]

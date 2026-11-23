#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The caller: what a host puts between itself and a wallet, so a scheme can be trusted with a call.

A :class:`Caller` holds the wallet built from one record and is the only way a host asks it anything. Every answer is a
validated value or an :class:`~mas.library.standard.lib.identity.contract.IdentityError`:

* a scheme that raises anything but an ``IdentityError`` is ``unavailable`` (no text, no cause: a scheme's message may
  hold a value);
* every call to a scheme is bounded (``timeout`` seconds, ``unavailable:timeout``): a scheme that waits on a network
  never holds a call, a session setup or a request open;
* what a scheme returns is checked by the rules before the host sees it, and an evidence already past its
  ``valid_until`` (within the clock skew) is ``expired``;
* the clock is the caller's, given to the scheme as ``now``: a scheme never reads the wall clock;
* a wallet that says ``revoked`` from ``present``, ``handshake``, ``describe``, ``delegate`` or ``revoke`` is revoked
  from then on for that identity and everything derived from it: nothing is presented for it again, the cache forgets
  it and every listener (:meth:`Caller.on_revoked`) is told, so a host closes the sessions that were opened with it.

A host calls ``present`` and ``handshake`` from async code. :meth:`Caller.present_blocking` is the sync twin: it hands
the work to the runtime's event loop when the runtime called the sync method on a worker thread, and runs it on a loop
of its own when no loop runs in this thread. A sync call made from a thread that runs a loop is refused
(``unsatisfiable:sync_in_loop``): it would block the loop it waits for.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar, cast

from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAP_DESCRIBE,
    CAP_HANDSHAKE,
    CAP_REVOKE,
    CAP_VERIFY,
    MAX_PAYLOAD,
    ChallengeRequired,
    Delegation,
    Delegator,
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
from mas.library.standard.lib.identity.principal import normalize_principal
from mas.library.standard.lib.identity.refusal import code_of
from mas.library.standard.lib.identity.rules import (
    DEFAULT_SKEW,
    check_answerable,
    check_carry,
    check_challenge,
    check_evidence,
    check_identity_info,
    check_inbound_meta,
    check_narrowing,
    check_public_view,
    check_verified,
    check_wire,
    delegation_widens,
)
from mas.runtime.aio import await_on_loop

_T = TypeVar("_T")
_log = logging.getLogger(__name__)

#: Seconds a scheme call may take before the caller gives up on it.
DEFAULT_TIMEOUT = 30.0
#: Most evidences kept in the cache.
MAX_CACHE = 256
MAX_ID = 256

#: The wallet-operation errors that mean "this identity is revoked".
_REVOKED = "revoked"
_ALL = "*"


class Caller:
    """The host's one door to a wallet. See the module text."""

    def __init__(
        self,
        record: str,
        scheme: IdentityScheme,
        *,
        clock: Callable[[], float] = time.time,
        skew: float = DEFAULT_SKEW,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.record = record
        self.scheme = scheme
        self._clock = clock
        self._skew = skew
        self._timeout = timeout
        self._lock = threading.Lock()
        self._revoked: set[str] = set()
        self._listeners: list[Callable[[str], None]] = []
        self._cache: dict[tuple[Any, ...], Evidence] = {}

    # --- what the wallet is ----------------------------------------------------------------------

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self.scheme.capabilities)

    def identities(self) -> list[IdentityInfo]:
        """The wallet's identities, value-free and checked. A wallet that fails to list is ``unavailable``."""
        try:
            return [check_identity_info(info) for info in self.scheme.identities()]
        except IdentityError:
            raise
        except Exception as exc:
            _log.warning("identity scheme %s failed: %s", self.scheme.scheme, type(exc).__name__)
            raise IdentityError("unavailable") from None

    def _require(self, capability: str) -> Any:
        if capability not in self.scheme.capabilities:
            raise IdentityError("unsatisfiable", f"no_{capability}")
        return self.scheme

    def _target(self, identity: str | None, req: PresentRequest | None = None) -> str:
        """The identity a call is for: the one named, else the one the wallet selects, else the whole wallet."""
        if identity:
            return identity
        if req is None:
            return _ALL
        try:
            return self.scheme.select(req) or _ALL
        except Exception as exc:  # a wallet that cannot choose refuses; it is never read as "the whole wallet"
            _log.warning("identity scheme %s failed to select: %s", self.scheme.scheme, type(exc).__name__)
            raise IdentityError("unavailable") from None

    # --- revocation ------------------------------------------------------------------------------

    def is_revoked(self, identity: str | None = None) -> bool:
        with self._lock:
            return _ALL in self._revoked or (identity is not None and identity in self._revoked)

    def on_revoked(self, callback: Callable[[str], None]) -> Callable[[], None]:
        """Call ``callback(identity)`` when an identity is revoked; the returned function unregisters it."""
        with self._lock:
            self._listeners.append(callback)

        def forget() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return forget

    def notify_revoked(self, identity: str, *, announce: bool = False) -> None:
        """An identity was revoked elsewhere (the issuer, a deny list): refuse it from now on and tell the listeners.

        One ``identity.revoke`` event and one listener call per identity that becomes revoked. With ``announce``
        (an explicit revocation) the identity named gets its event even when it was revoked already.
        """
        derived: set[str] = {identity}
        if identity != _ALL:
            try:
                infos = self.identities()
            except IdentityError:
                infos = []
            grew = True
            while grew:
                grew = False
                for info in infos:
                    if info.get("parent") in derived and info["id"] not in derived:
                        derived.add(info["id"])
                        grew = True
        with self._lock:
            fresh = derived - self._revoked
            self._revoked |= derived
            self._cache = {
                k: v for k, v in self._cache.items() if k[0] not in derived and k[0] != _ALL and _ALL not in derived
            }
            listeners = list(self._listeners)
        for found in sorted(fresh | ({identity} if announce else set())):
            observe.emit(
                "identity.revoke",
                record=self.record,
                identity="" if found == _ALL else found,
                scheme=self.scheme.scheme,
                outcome="ok",
            )
            if found not in fresh:
                continue
            for callback in listeners:
                try:
                    callback(found)
                except Exception:
                    _log.warning("a revocation listener failed")

    # --- the guarded call ------------------------------------------------------------------------

    async def _guard(self, call: Callable[[], Awaitable[_T]], *, identity: str, revokes: bool = True) -> _T:
        """Run one scheme call: bounded, typed errors pass, anything else is ``unavailable``."""
        failure: IdentityError | None = None
        try:
            async with asyncio.timeout(self._timeout) as limit:
                try:
                    return await call()
                except IdentityError:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if limit.expired():
                        raise IdentityError("unavailable", "timeout") from None
                    _log.warning("identity scheme %s failed: %s", self.scheme.scheme, type(exc).__name__)
                    failure = IdentityError("unavailable")
        except TimeoutError:
            failure = IdentityError("unavailable", "timeout")
        except IdentityError as exc:
            if revokes and exc.code == _REVOKED and identity != _ALL:  # one identity's answer never revokes the wallet
                self.notify_revoked(identity)
            raise
        assert failure is not None
        raise failure

    def _seen(self, audience: Mapping[str, str] | None, **fields: Any) -> dict[str, Any]:
        """The fields every operation event carries: who asked, for which record, scheme, protocol and host."""
        audience = audience or {"protocol": "", "target": ""}
        return {
            "record": self.record,
            "scheme": self.scheme.scheme,
            "protocol": audience.get("protocol", ""),
            "host": observe.host_of(audience.get("target", "")),
            **fields,
        }

    def _report(
        self, kind: str, started: float, fields: Mapping[str, Any], exc: BaseException | None = None, **extra: Any
    ) -> None:
        """One event for a finished scheme operation: ``ok`` (with ``extra``), or ``denied`` with the error's code."""
        latency = {"latency_ms": round((time.monotonic() - started) * 1000, 2)}
        if exc is None:
            observe.emit(kind, outcome="ok", **extra, **latency, **fields)
        else:
            observe.emit(kind, outcome="denied", code=code_of(exc), **latency, **fields)

    # --- present ---------------------------------------------------------------------------------

    def _stamp(self, req: Any) -> Any:
        """A copy of ``req`` with the caller's own record, the normalized principal and the caller's clock."""
        out = dict(req)
        out["record"] = self.record
        try:
            out["principal"] = normalize_principal(req.get("principal"))
        except ValueError:
            raise IdentityError("invalid", "bad_principal") from None
        out["now"] = float(self._clock())
        return out

    def _prepare(self, req: PresentRequest) -> tuple[PresentRequest, str]:
        out = cast(PresentRequest, self._stamp(req))
        out.setdefault("phase", "initiate")
        if out["phase"] == "challenge":
            check_answerable(out.get("peer"), out["audience"]["target"])
        target = self._target(req.get("identity"), out)
        if self.is_revoked(target):
            raise IdentityError("revoked")
        return out, target

    def _cache_key(self, target: str, req: PresentRequest) -> tuple[Any, ...]:
        who = req.get("principal", {})
        audience = req["audience"]
        return (
            target,
            audience["protocol"],
            audience["target"],
            req.get("task", ""),
            who.get("actor", ""),
            who.get("subject", ""),
            who.get("session", ""),
        )

    async def present(
        self,
        req: PresentRequest,
        *,
        sections: Mapping[str, str] | None = None,
        text_protocols: frozenset[str] = frozenset(),
    ) -> Evidence:
        """What the wallet shows for ``req``: checked, not expired, cached when the scheme says it is reusable.

        ``sections`` are the ``advertise`` sections the protocol adapter carries (name to ``"map"`` or ``"list"``);
        ``text_protocols`` the protocols whose message text a scheme may wrap with ``payload``.
        """
        started = time.monotonic()
        who = req.get("principal") or {}
        seen = self._seen(
            req.get("audience"),
            actor=who.get("actor", ""),
            phase=req.get("phase", "initiate"),
            operation=req.get("task", ""),
        )
        try:
            evidence = await self._present(req, sections, text_protocols)
        except BaseException as exc:
            self._report("identity.present", started, seen, exc)
            raise
        self._report("identity.present", started, seen, grant=evidence.get("grant"))
        return evidence

    async def _present(
        self, req: PresentRequest, sections: Mapping[str, str] | None, text_protocols: frozenset[str]
    ) -> Evidence:
        request, target = self._prepare(req)
        phase = request["phase"]
        key = self._cache_key(target, request) if phase == "initiate" else None
        if key is not None:
            with self._lock:
                held = self._cache.get(key)
            if held is not None and self.is_fresh(held):
                return copy.deepcopy(held)
        result = await self._guard(lambda: self.scheme.present(request), identity=target)
        evidence = check_evidence(
            result,
            sections=sections,
            text_protocols=text_protocols,
            phase=phase,
            protocol=request["audience"]["protocol"],
        )
        if not self.is_fresh(evidence):
            raise IdentityError("expired")
        if key is not None and evidence.get("reusable") and "valid_until" in evidence:
            with self._lock:
                self._cache[key] = copy.deepcopy(evidence)
                while len(self._cache) > MAX_CACHE:
                    del self._cache[next(iter(self._cache))]
        return evidence

    def is_fresh(self, evidence: Evidence) -> bool:
        """Is ``evidence`` still inside its ``valid_until`` (with the clock skew)?"""
        until = evidence.get("valid_until")
        return until is None or float(self._clock()) + self._skew < until

    # --- the sync twin ---------------------------------------------------------------------------

    def _run(self, make: Callable[[], Awaitable[_T]]) -> _T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise IdentityError("unsatisfiable", "sync_in_loop")
        try:
            return cast(_T, await_on_loop(make()))
        except RuntimeError:
            # no runtime loop to hand it to: this thread runs no loop, so it may run one of its own
            return asyncio.run(cast("Any", make()))

    def present_blocking(
        self,
        req: PresentRequest,
        *,
        sections: Mapping[str, str] | None = None,
        text_protocols: frozenset[str] = frozenset(),
    ) -> Evidence:
        """:meth:`present` for a sync caller (see the module text). Signing works the same way."""
        return self._run(lambda: self.present(req, sections=sections, text_protocols=text_protocols))

    # --- handshake, verify, describe, revoke, delegate -------------------------------------------

    async def handshake(self, ctx: HandshakeContext) -> Evidence:
        """Run the wallet's in-session handshake. Never cached; the result is checked like any evidence."""
        scheme = cast(Handshaker, self._require(CAP_HANDSHAKE))
        full = cast(HandshakeContext, self._stamp(ctx))
        full.setdefault("audience", {"protocol": "", "target": ""})
        if "challenge" in (full.get("peer") or {}):  # an auth-required peer asks: checked like any peer challenge
            check_answerable(full["peer"], full["audience"]["target"], native=True)
        target = self._target(
            ctx.get("identity"),
            cast(PresentRequest, {k: v for k, v in full.items() if k != "channel"}),
        )
        if self.is_revoked(target):
            raise IdentityError("revoked")
        audience = full["audience"]
        started = time.monotonic()
        seen = self._seen(audience, actor=full["principal"].get("actor", ""), operation="handshake")
        try:
            result = await self._guard(lambda: scheme.handshake(full), identity=target)
            evidence = check_evidence(result, phase="handshake", protocol=audience.get("protocol", ""))
            if "headers" in evidence:  # a handshake runs on the session, which has no header to put them on
                raise IdentityError("invalid", "handshake_headers")
        except BaseException as exc:
            self._report("identity.handshake", started, seen, exc)
            raise
        self._report("identity.handshake", started, seen, grant=evidence.get("grant"))
        return evidence

    async def verify(self, inbound: Inbound, *, carry_keys: frozenset[str] = frozenset()) -> Verified:
        """Check an inbound credential. Any failure, an exception included, is a denial.

        ``carry_keys`` are the challenge ``carry`` keys the protocol adapter can carry. A ``revoked`` from here is
        about the caller's credential, not this wallet's identity: it does not revoke anything here.
        """
        scheme = cast(Verifier, self._require(CAP_VERIFY))
        if inbound.get("direction") not in ("request", "response"):
            raise IdentityError("invalid", "direction")
        seen = cast(Inbound, copy.deepcopy(dict(inbound)))
        if "payload" in seen and not (isinstance(seen["payload"], str) and len(seen["payload"]) <= MAX_PAYLOAD):
            raise IdentityError("invalid", "inbound_payload")
        if "meta" in seen:
            seen["meta"] = check_inbound_meta(seen["meta"])
        for name in ("session", "request_id", "task_id"):
            if name in seen and not (isinstance(seen[name], str) and len(seen[name]) <= MAX_ID):  # type: ignore[literal-required]
                raise IdentityError("invalid", f"inbound_{name}")
        seen["now"] = float(self._clock())  # the caller's clock; a peer cannot set it
        audience = seen.get("audience", {"protocol": "", "target": ""})
        base = {
            "record": self.record,
            "scheme": self.scheme.scheme,
            "protocol": seen.get("protocol", audience.get("protocol", "")),
            "host": observe.host_of(audience.get("target", "")),
        }
        started = time.monotonic()
        try:
            result = await self._guard(lambda: scheme.verify(seen), identity=_ALL, revokes=False)
            verified = check_verified(result)
        except ChallengeRequired as asked:
            try:
                check_challenge(asked.challenge)
                check_carry(asked.carry, keys=carry_keys)
                check_wire(asked.wire)
            except IdentityError as bad:
                self._report("identity.verify", started, base, bad)
                raise
            observe.emit(
                "identity.verify",
                outcome="challenged",
                code=str(asked),
                latency_ms=round((time.monotonic() - started) * 1000, 2),
                **base,
            )
            raise
        except BaseException as exc:
            self._report("identity.verify", started, base, exc)
            raise
        self._report("identity.verify", started, base, grant=verified.get("grant"))
        return verified

    async def describe(self, ref: str) -> PublicView:
        """What a policy may know about identity ``ref`` without a proof."""
        scheme = cast(Describer, self._require(CAP_DESCRIBE))
        if self.is_revoked(ref):
            raise IdentityError("revoked")
        return check_public_view(await self._guard(lambda: scheme.describe(ref), identity=ref))

    async def revoke(self, ref: str) -> None:
        """Revoke identity ``ref`` through the wallet, then refuse it (and what derives from it) from now on.

        Every call that ends with ``ref`` revoked publishes exactly one ``identity.revoke`` event for it, also when the
        wallet answers ``revoked`` because it held the identity as revoked already (that answer is raised as it is).
        """
        scheme = cast(Revoker, self._require(CAP_REVOKE))
        try:
            await self._guard(lambda: scheme.revoke(ref), identity=ref, revokes=False)
        except IdentityError as exc:
            if exc.code == _REVOKED:
                self.notify_revoked(ref, announce=True)
            raise
        self.notify_revoked(ref, announce=True)

    async def delegate(self, delegation: Delegation) -> IdentityInfo:
        """Derive a narrower identity. The wallet narrows; the caller checks that it did.

        The result's grant must be inside the parent's: the grant of ``parent_evidence`` when the parent is outside the
        wallet, else the grant the wallet lists for the parent. A result with no grant under a parent that has one
        cannot be shown to be inside it and is refused (``refused:delegation_widens``).
        """
        scheme = cast(Delegator, self._require(CAP_DELEGATE))
        full = cast(Delegation, dict(delegation))
        full["now"] = float(self._clock())
        asked = cast(
            PresentRequest,
            {
                "record": self.record,
                "audience": full.get("audience") or {"protocol": "", "target": ""},
                "task": "",
                "now": full["now"],
            },
        )
        target = self._target(full.get("identity"), asked)
        if target == _ALL:  # nothing selected: there is no parent to derive from
            raise IdentityError("no_credential", "no_parent")
        if self.is_revoked(target):
            raise IdentityError("revoked")
        held = next((i for i in self.identities() if i["id"] == target), None)
        if held is None and "parent_evidence" not in full:  # a parent outside the wallet shows its own evidence
            raise IdentityError("no_credential", "unknown_identity")
        result = await self._guard(lambda: scheme.delegate(full), identity=target)
        child = check_identity_info(result)
        if child.get("parent") != target:  # the result must say it derives from the identity it was asked about
            raise IdentityError("refused", "delegation_parent")
        if "parent_evidence" in full:  # a parent outside the wallet: its own evidence is the only bound
            parent_grant = full["parent_evidence"].get("grant")
            if parent_grant is None:
                raise delegation_widens()
        else:
            parent_grant = held.get("grant") if held else None
        if parent_grant:
            if "grant" not in child:
                raise delegation_widens()
            check_narrowing(parent_grant, child["grant"], target=full.get("audience", {}).get("target", ""))
        return child


__all__ = ["DEFAULT_TIMEOUT", "MAX_CACHE", "Caller"]

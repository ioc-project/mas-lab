#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``identity-gate``: a governance plugin that decides a tool call from the public view of an identity.

The gate is **advisory**. The identity layer is the enforcement point: a call whose identity cannot be presented is
never sent, whatever the gate said. The gate adds policy on top: it refuses a call before it starts when the identity
the agent holds does not cover what the tool needs, and says why with a value-free reason.

How it decides
==============

``evaluate_egress`` is synchronous and runs inside the async turn, so the gate never does I/O there. A worker thread
runs the ``describe`` calls and fills a cache of the wallet's public views:

* every egress of the agent (a model call as well as a tool call) starts a refresh of the views its requirements read,
  at most every ``refresh_after_s``, so the view is there before the tool call that needs it;
* a missing view, or one older than ``max_age_s``, is ``gate_stale``: the call is blocked (fail closed). A revocation
  the wallet reports is known at once and never stale;
* ``first_view_wait_s`` (default 0: off) makes a decision with no view yet wait for the refresh already under way, at
  most that long. The wait blocks the thread that runs the turn, so it is for hosts that make a tool call first.

Which wallet
============

The identity layer registers the wallet it built for an agent and a record (``holders``). A requirement names the
record (``identity``); without a name it reads the records the agent holds. A wallet that holds several identities
covers a requirement when any one of its identities does; a requirement names one identity with ``task`` or
``audience``. An agent that holds no wallet for the record is ``no_identity``.

Outcomes
========

ALLOW; BLOCK with ``no_identity``, ``no_credential`` (the wallet has nothing to show for the requirement),
``credential_revoked``, ``scope_uncovered:<name>``, ``gate_stale`` or ``gate_error``; HITL ``interaction_required`` when
the scheme waits for a human step and the kernel has a human-in-the-loop (``hitl_on_tool``). A call with no
requirement, and anything that is not a tool call, is ALLOW (the chain contract: a plugin that does not apply passes).

Every decision is one ``identity.gate`` event (``events: all``, the default), only those about tool calls
(``events: tools``) or none (``events: off``). The event names the actor, the record, the operation, the call, the
outcome and a value-free code, and nothing the wallet or the facts hold.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import logging
import re
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mas.library.standard.lib.identity import holders, observe
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import IdentityError, InteractionRequired, PublicView
from mas.library.standard.lib.identity.holders import scope_of
from mas.library.standard.lib.identity.principal import current_principal
from mas.library.standard.lib.identity.requirements import (
    GateConfigError,
    Requirement,
    parse_requirements,
    principal_facts,
    unmet,
)
from mas.runtime.aio import new_thread
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "gov_identity_gate"
DEFAULT_MAX_AGE_S = 30.0
DEFAULT_FIRST_VIEW_WAIT_S = 0.0
#: What ``events`` may be: every decision, the decisions about tool calls, none.
EVENT_MODES = ("all", "tools", "off")

_log = logging.getLogger(__name__)
_ALLOW_CODES = {
    "not a tool call": "not_tool_call",
    "no requirement for this tool": "no_requirement",
    "an identity is held": "identity_held",
    "the identity covers the requirement": "covered",
}
_OUTCOMES = {GovDecision.ALLOW: "ok", GovDecision.BLOCK: "denied", GovDecision.HITL: "hold"}


@dataclass(frozen=True)
class _Entry:
    state: str  # views | revoked | no_credential | interaction | error
    views: tuple[PublicView, ...]
    at: float


class _Worker:
    """One event loop on its own thread: the gate's ``describe`` calls run here, never on the turn's loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, coro: Any) -> concurrent.futures.Future[Any]:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                new_thread(run, name="identity-gate", daemon=True).start()
                ready.wait(5.0)
                self._loop = loop
            loop = self._loop
        return asyncio.run_coroutine_threadsafe(coro, loop)


def _relay(done: concurrent.futures.Future[Any], mine: concurrent.futures.Future[Any]) -> None:
    """Complete ``mine`` the way the worker's future ended (a cancelled ``mine`` stays cancelled)."""
    if mine.done():
        return
    if done.cancelled():
        mine.cancel()
    elif done.exception() is not None:
        mine.set_exception(done.exception())  # type: ignore[arg-type]
    else:
        mine.set_result(done.result())


_WORKER = _Worker()
_Key = tuple[str, str, int]  # record, reference, the serial of the wallet (never its position among the agent's)


def _positive(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise GateConfigError(f"{name} must be a positive number")
    return float(value)


class IdentityGate:
    """Decide a tool call from the public view of the identities its agent holds. See the module text.

    Config (a Control entry): ``requirements`` (see ``lib.identity.requirements.parse_requirements``),
    ``max_age_s`` (a cached view older than this blocks, default 30), ``refresh_after_s`` (a decision refreshes a view
    older than this, default a third of ``max_age_s``; ``0`` refreshes on every decision), ``first_view_wait_s`` (how
    long a decision waits for a view that is not there yet, default 0: it does not wait) and ``events`` (``all``,
    ``tools`` or ``off``).
    """

    plugin_id = "gov_identity_gate@v1"

    def __init__(
        self,
        requirements: Mapping[str, Any] | None = None,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        refresh_after_s: float | None = None,
        events: str = "all",
        first_view_wait_s: float = DEFAULT_FIRST_VIEW_WAIT_S,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        bound_principal: Callable[[], Mapping[str, Any]] = current_principal,
    ) -> None:
        if events not in EVENT_MODES:
            raise GateConfigError(f"events must be one of {', '.join(EVENT_MODES)}")
        self._events = events
        self._requirements = parse_requirements(requirements)
        self._max_age = _positive(max_age_s, "max_age_s")
        refresh = self._max_age / 3 if refresh_after_s is None else refresh_after_s
        if not isinstance(refresh, (int, float)) or isinstance(refresh, bool) or refresh < 0:
            raise GateConfigError("refresh_after_s must be a number >= 0")
        self._refresh_after = float(refresh)
        if (
            not isinstance(first_view_wait_s, (int, float))
            or isinstance(first_view_wait_s, bool)
            or first_view_wait_s < 0
        ):
            raise GateConfigError("first_view_wait_s must be a number >= 0")
        self._first_wait = float(first_view_wait_s)
        self._clock = clock
        self._wall_clock = wall_clock
        self._bound_principal = bound_principal
        self._lock = threading.Lock()
        self._serials: weakref.WeakKeyDictionary[Caller, int] = weakref.WeakKeyDictionary()
        self._counter = itertools.count(1)
        self._cache: dict[_Key, _Entry] = {}
        self._inflight: dict[_Key, tuple[concurrent.futures.Future[Any], float]] = {}
        self._agent = ""
        self._scope = ""

    def __repr__(self) -> str:
        return f"IdentityGate(requirements={sorted(self._requirements)!r})"

    # --- the wallets -------------------------------------------------------------------------------

    def _wallets(self, agent: str, requirement: Requirement) -> list[tuple[str, int, Caller]]:
        """The ``(record, n, wallet)`` triples a requirement reads for ``agent``: the named record, else all it can see.

        An agent sees the wallets it holds and the wallets no agent in particular holds (a stack shared by every
        agent). The gate only reads them: managing a shared wallet is not the agent's.
        """
        shared = holders.records_of("", self._scope) if agent else []
        records = (
            [requirement.identity]
            if requirement.identity
            else sorted({*holders.records_of(agent, self._scope), *shared})
        )
        out: list[tuple[str, int, Caller]] = []
        for record in records:
            held = holders.wallets(agent, record, self._scope)
            if agent:
                held += [w for w in holders.wallets("", record, self._scope) if w not in held]
            out.extend((record, self._serial(caller), caller) for caller in held)
        return out

    # --- the view cache ----------------------------------------------------------------------------

    def _serial(self, caller: Caller) -> int:
        """A number that names this wallet for as long as it lives: a cached view is never read for another wallet,
        however the wallets of an agent come and go (a position among them would be reused)."""
        with self._lock:
            found = self._serials.get(caller)
            if found is None:
                found = self._serials[caller] = next(self._counter)
            return found

    def _kick(self, key: _Key, caller: Caller) -> None:
        abandoned: concurrent.futures.Future[Any] | None = None
        with self._lock:
            now = self._clock()
            running = self._inflight.get(key)
            if running is not None:
                if now - running[1] <= self._max_age:
                    return
                abandoned = running[0]  # a describe that hangs must not stop every later refresh
            entry = self._cache.get(key)
            if entry is not None and now - entry.at < self._refresh_after:
                return
            # reserved under the lock; the worker (whose first use starts a thread) is asked outside it
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            self._inflight[key] = (future, now)
        if abandoned is not None:
            abandoned.cancel()  # outside the lock: cancelling runs the done callback, which takes it
        future.add_done_callback(lambda done, k=key: self._done(k, done))
        try:
            real = _WORKER.submit(self._describe(key, caller))
        except BaseException as exc:
            future.set_exception(exc)
            raise
        real.add_done_callback(lambda done, mine=future: _relay(done, mine))

    def _done(self, key: _Key, future: concurrent.futures.Future[Any]) -> None:
        with self._lock:
            running = self._inflight.get(key)
            if running is not None and running[0] is future:  # never the entry of a newer describe
                del self._inflight[key]

    async def _describe(self, key: _Key, caller: Caller) -> None:
        _record, ref, _n = key
        state, views = "error", ()
        try:
            if caller.is_revoked():
                raise IdentityError("revoked")
            ids = [ref] if ref else [info["id"] for info in caller.identities() if info["status"] == "active"]
            if not ids:
                raise IdentityError("revoked")
            views = tuple([await caller.describe(identity) for identity in ids])
            state = "views"
        except InteractionRequired:
            state = "interaction"
        except IdentityError as exc:
            # a wallet with nothing to show for the requirement is its own closed answer, not an error of the gate
            state = {"revoked": "revoked", "no_credential": "no_credential"}.get(exc.code, "error")
        except Exception as exc:  # a describe never raises into the worker; the decision fails closed
            _log.warning("identity gate: describe of a record failed: %s", type(exc).__name__)
        with self._lock:
            self._cache[key] = _Entry(state, views, self._clock())

    def _refresh(self, agent: str) -> None:
        for requirement in self._requirements.values():
            if requirement.needs_facts:
                for record, n, caller in self._wallets(agent, requirement):
                    self._kick((record, requirement.ref, n), caller)

    def prefetch(self, agent_id: str) -> None:
        """Make ``agent_id`` this gate's agent and start filling the views for it, without waiting."""
        self._agent = agent_id
        self._refresh(agent_id)

    def settle(self, timeout: float = 5.0) -> None:
        """Wait until the refreshes in flight are done. For tests and demos, never for the turn's loop."""
        with self._lock:
            pending = [future for future, _started in self._inflight.values()]
        concurrent.futures.wait(pending, timeout=timeout)

    def _entry(self, key: _Key) -> _Entry | None:
        """The cached view. With ``first_view_wait_s`` set, a view that is not there yet is waited for."""
        with self._lock:
            entry = self._cache.get(key)
            running = self._inflight.get(key)
        if entry is None and running is not None and self._first_wait > 0:
            concurrent.futures.wait([running[0]], timeout=self._first_wait)
            with self._lock:
                entry = self._cache.get(key)
        return entry

    # --- governance hook ---------------------------------------------------------------------------

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig) -> tuple[GovDecision, str, str]:
        record = ""
        try:
            self._agent = str(config.agent_id or "")
            self._scope = scope_of(config.agent_spec)
            self._refresh(self._agent)  # every egress, not only a tool call: the model call before it warms the views
            decision, record = self._decide(intent, config)
        except Exception as exc:
            _log.warning("identity gate: decision failed: %s", type(exc).__name__)
            decision = GovDecision.BLOCK, PLUGIN_ID, "gate_error"
        return self._published(intent, decision, record)

    def _published(
        self, intent: EgressIntentView, decision: tuple[GovDecision, str, str], record: str
    ) -> tuple[GovDecision, str, str]:
        verdict, _plugin, reason = decision
        try:
            tool = intent.tool_name.strip() if intent.op == "TOOL_CALL" and isinstance(intent.tool_name, str) else ""
            operation = f"{observe.TOOL_OPERATION}{tool}" if tool else str(intent.op).lower()
            call = str(intent.correlation_id)
        except AttributeError:  # not an intent: still a decision, so still an event
            tool, operation, call = "", "unknown", ""
        if self._events == "all" or (self._events == "tools" and tool):
            code = _ALLOW_CODES.get(reason) or re.sub(r"[^A-Za-z0-9_.:-]", "_", reason)[:64]
            observe.emit(
                "identity.gate",
                actor=self._agent,
                record=record,
                operation=operation,
                call=call,
                outcome=_OUTCOMES.get(verdict, "denied"),
                code=code,
            )
        return decision

    def _principal(self) -> dict[str, Any]:
        bound = dict(self._bound_principal())
        if self._agent and not bound.get("actor"):
            bound["actor"] = self._agent
        return principal_facts(bound)

    def _decide(self, intent: EgressIntentView, config: KernelConfig) -> tuple[tuple[GovDecision, str, str], str]:
        """The decision and the record that made it (``""`` when none was asked)."""
        tool = (intent.tool_name or "").strip()
        if intent.op != "TOOL_CALL" or not tool:
            return (GovDecision.ALLOW, PLUGIN_ID, "not a tool call"), ""
        requirement = self._requirements.get(tool)
        if requirement is None:
            return (GovDecision.ALLOW, PLUGIN_ID, "no requirement for this tool"), ""
        wallets = self._wallets(self._agent, requirement)
        if not wallets:
            return (GovDecision.BLOCK, PLUGIN_ID, "no_identity"), requirement.identity
        outcomes = [(self._outcome(record, n, caller, requirement, config), record) for record, n, caller in wallets]
        for outcome in outcomes:  # a revoked wallet of the record denies, however many other wallets would allow
            if outcome[0][2] == "credential_revoked":
                return outcome
        for outcome in outcomes:
            if outcome[0][0] is GovDecision.ALLOW:
                return outcome
        return outcomes[0]

    @staticmethod
    def _revoked(caller: Caller, ref: str) -> bool:
        """Has the caller been told the wallet (or the identity a requirement names) is revoked?

        A wallet is revoked when it lists identities and every one of them reports ``revoked`` or has been told so.
        """
        if caller.is_revoked() or (ref and caller.is_revoked(ref)):
            return True
        try:
            listed = caller.identities()
        except IdentityError:
            return False  # the refresh reads the failure; this is only the answer that needs no I/O
        return bool(listed) and all(info["status"] == "revoked" or caller.is_revoked(info["id"]) for info in listed)

    def _outcome(
        self, record: str, n: int, caller: Caller, requirement: Requirement, config: KernelConfig
    ) -> tuple[GovDecision, str, str]:
        if self._revoked(caller, requirement.ref):
            return GovDecision.BLOCK, PLUGIN_ID, "credential_revoked"  # known at once, never stale
        if not requirement.require:
            return GovDecision.ALLOW, PLUGIN_ID, "an identity is held"
        now = float(self._wall_clock())
        if not requirement.needs_facts:  # it reads the principal only: no view, nothing to be stale
            missing = unmet(requirement, PublicView(facts={"principal": self._principal()}), now=now)
            if missing:
                return GovDecision.BLOCK, PLUGIN_ID, f"scope_uncovered:{missing}"[:64]
            return GovDecision.ALLOW, PLUGIN_ID, "the identity covers the requirement"
        entry = self._entry((record, requirement.ref, n))
        if entry is None or self._clock() - entry.at > self._max_age:
            return GovDecision.BLOCK, PLUGIN_ID, "gate_stale"
        if entry.state == "revoked":
            return GovDecision.BLOCK, PLUGIN_ID, "credential_revoked"
        if entry.state == "no_credential":
            return GovDecision.BLOCK, PLUGIN_ID, "no_credential"
        if entry.state == "interaction":
            if config.hitl_on_tool or config.hitl_on_tool_result:
                return GovDecision.HITL, PLUGIN_ID, "interaction_required"
            return GovDecision.BLOCK, PLUGIN_ID, "no_identity"
        if entry.state != "views" or not entry.views:
            return GovDecision.BLOCK, PLUGIN_ID, "gate_error"
        principal = self._principal()
        missing = ""
        for view in entry.views:  # a wallet covers the requirement when any one of its identities does
            missing = unmet(requirement, PublicView(facts={**view.get("facts", {}), "principal": principal}), now=now)
            if not missing:
                return GovDecision.ALLOW, PLUGIN_ID, "the identity covers the requirement"
        return GovDecision.BLOCK, PLUGIN_ID, f"scope_uncovered:{missing}"[:64]


__all__ = ["DEFAULT_MAX_AGE_S", "EVENT_MODES", "PLUGIN_ID", "GateConfigError", "IdentityGate", "parse_requirements"]

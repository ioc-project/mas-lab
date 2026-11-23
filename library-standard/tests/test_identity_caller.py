#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The caller: a wallet is trusted with a call only through it. Fails closed, bounded, revocation closes."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import pytest
from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAP_DESCRIBE,
    CAP_REVOKE,
    CAP_VERIFY,
    ChallengeRequired,
    Delegation,
    Delegator,
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
from mas.library.standard.testing.identity_scripted import IDENTITY_ID, Scripted
from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream

AUD = {"protocol": "mcp", "target": "http://127.0.0.1:9"}
FAR = 4102444800.0


def _req(**extra: Any) -> PresentRequest:
    return PresentRequest(principal={"actor": "a"}, audience=AUD, task="t", **extra)


@pytest.fixture(autouse=True)
def events():
    """Every event published in a test, and the rule that each one follows the vocabulary."""
    stream, seen = EventStream(), []
    stream.subscribe(seen.append)
    token = set_event_stream(stream)
    yield seen
    reset_event_stream(token)
    for event in seen:
        observe.validate_event(event)


class _Wallet(IdentityScheme, Describer, Revoker, Delegator, Verifier):
    """Two identities, ``main`` and ``side``; what each call does is set by the test."""

    scheme: ClassVar[str] = "caller_wallet"
    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_DESCRIBE, CAP_REVOKE, CAP_DELEGATE, CAP_VERIFY})
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]] = {"type": "object"}

    def __init__(self) -> None:
        self.present_error: BaseException | None = None
        self.delay = 0.0
        self.evidence: Evidence = Evidence(headers={"x-ev": "1"}, valid_until=FAR, reusable=True)
        self.calls = 0
        self.children: list[IdentityInfo] = []
        self.delegated: IdentityInfo | None = None
        self.verify_error: BaseException | None = None

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> _Wallet:
        return cls()

    def identities(self) -> Sequence[IdentityInfo]:
        base = [
            IdentityInfo(id="main", kind="agent", subject="", labels=[], status="active", grant={"scopes": ["a", "b"]}),
            IdentityInfo(id="side", kind="agent", subject="", labels=[], status="active"),
        ]
        return [*base, *self.children]

    def select(self, req: PresentRequest) -> str | None:
        return req.get("identity") or "main"

    async def present(self, req: PresentRequest) -> Evidence:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.present_error is not None:
            raise self.present_error
        return dict(self.evidence)  # type: ignore[return-value]

    async def describe(self, ref: str) -> PublicView:
        return PublicView(facts={"id": ref})

    async def revoke(self, ref: str) -> None:
        return None

    async def delegate(self, delegation: Delegation) -> IdentityInfo:
        assert self.delegated is not None
        self.children.append(self.delegated)
        return self.delegated

    async def verify(self, inbound: Inbound) -> Verified:
        if self.verify_error is not None:
            raise self.verify_error
        return Verified(subject="u", claims={}, source="other")


# --- a total function --------------------------------------------------------------------------


async def test_a_scheme_that_raises_anything_else_is_unavailable_with_no_text() -> None:
    wallet = _Wallet()
    wallet.present_error = RuntimeError("the secret is s3cr3t-value")
    caller = Caller("r", wallet)
    with pytest.raises(IdentityError) as caught:
        await caller.present(_req())
    assert str(caught.value) == "unavailable" and caught.value.__cause__ is None
    assert "s3cr3t" not in repr(caught.value) and "s3cr3t" not in json.dumps(caller.identities())


async def test_a_typed_error_passes_through_untouched() -> None:
    wallet = _Wallet()
    wallet.present_error = IdentityError("refused", "wrong_audience")
    with pytest.raises(IdentityError, match="refused:wrong_audience"):
        await Caller("r", wallet).present(_req())


async def test_an_interaction_required_reaches_the_host_as_it_is() -> None:
    wallet = _Wallet()
    wallet.present_error = InteractionRequired("h1", "open the issuer page")
    with pytest.raises(InteractionRequired) as caught:
        await Caller("r", wallet).present(_req())
    assert (caught.value.handle, str(caught.value)) == ("h1", "no_credential")


async def test_evidence_that_breaks_the_rules_is_refused_before_the_host_sees_it() -> None:
    wallet = _Wallet()
    wallet.evidence = Evidence(headers={"x\r\nbad": "v"})
    with pytest.raises(IdentityError, match="invalid"):
        await Caller("r", wallet).present(_req())


# --- bounded ----------------------------------------------------------------------------------


async def test_a_scheme_that_waits_is_given_up_on_and_the_call_fails_closed() -> None:
    wallet = _Wallet()
    wallet.delay = 5.0
    with pytest.raises(IdentityError, match="unavailable:timeout"):
        await Caller("r", wallet, timeout=0.05).present(_req())


async def test_a_handshake_that_waits_is_bounded_too() -> None:
    scripted = Scripted()

    async def slow(ctx: Any) -> Evidence:
        await asyncio.sleep(5)
        return Evidence()

    scripted.handshake = slow  # type: ignore[method-assign]
    from mas.library.standard.testing.identity_fakes import FakeChannel

    ctx = {
        "record": "r",
        "principal": {},
        "audience": AUD,
        "task": "",
        "peer": {},
        "channel": FakeChannel(),
        "now": 0.0,
    }
    with pytest.raises(IdentityError, match="unavailable:timeout"):
        await Caller("r", scripted, timeout=0.05).handshake(ctx)  # type: ignore[arg-type]


# --- time -------------------------------------------------------------------------------------


async def test_evidence_already_past_its_end_within_the_skew_is_expired() -> None:
    wallet = _Wallet()
    wallet.evidence = Evidence(headers={"x-ev": "1"}, valid_until=1003.0)
    with pytest.raises(IdentityError, match="^expired$"):
        await Caller("r", wallet, clock=lambda: 1000.0, skew=5.0).present(_req())


async def test_the_scheme_is_given_the_callers_clock_not_the_wall_clock() -> None:
    scripted = Scripted([Evidence(headers={"x-a": "1"})])
    await Caller("r", scripted, clock=lambda: 1234.5).present(_req())
    assert scripted.requests[0]["now"] == 1234.5 and scripted.requests[0]["record"] == "r"


# --- cache --------------------------------------------------------------------------------------


async def test_a_reusable_evidence_is_asked_once_and_a_one_shot_every_time() -> None:
    wallet = _Wallet()
    caller = Caller("r", wallet)
    await caller.present(_req())
    await caller.present(_req())
    assert wallet.calls == 1
    wallet.evidence = Evidence(headers={"x-ev": "2"})
    other = Caller("r", wallet)
    await other.present(_req())
    await other.present(_req())
    assert wallet.calls == 3


async def test_a_sign_is_never_cached() -> None:
    wallet = _Wallet()
    caller = Caller("r", wallet)
    for _ in range(2):
        await caller.present({**_req(), "phase": "sign", "request": {"method": "POST", "url": "http://x"}})
    assert wallet.calls == 2


async def test_the_cache_is_per_principal_and_per_identity() -> None:
    wallet = _Wallet()
    caller = Caller("r", wallet)
    await caller.present(PresentRequest(principal={"actor": "a"}, audience=AUD, task="t"))
    await caller.present(PresentRequest(principal={"actor": "b"}, audience=AUD, task="t"))
    await caller.present(PresentRequest(principal={"actor": "a"}, audience=AUD, task="t", identity="side"))
    assert wallet.calls == 3


# --- the wallet: several identities ----------------------------------------------------------


async def test_the_caller_names_the_identity_it_presents_or_leaves_the_choice_to_the_wallet() -> None:
    scripted = Scripted([Evidence(headers={"x-a": "1"}), Evidence(headers={"x-a": "2"})])
    caller = Caller("r", scripted)
    await caller.present(_req())
    await caller.present(_req(identity="scripted"))
    assert [r.get("identity") for r in scripted.requests] == [None, "scripted"]


# --- revocation -------------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["present", "handshake", "describe", "delegate", "revoke"])
async def test_a_revoked_from_any_wallet_operation_revokes_the_identity_and_tells_the_listeners(
    operation: str, events: list[dict[str, Any]]
) -> None:
    wallet = _Wallet()
    told: list[str] = []
    caller = Caller("r", wallet)
    caller.on_revoked(told.append)
    refusal = IdentityError("revoked")
    wallet.present_error = refusal
    wallet.describe = _raises(refusal)  # type: ignore[method-assign]
    wallet.delegate = _raises(refusal)  # type: ignore[method-assign]
    wallet.revoke = _raises(refusal)  # type: ignore[method-assign]
    if operation == "handshake":
        scripted = Scripted(handshakes=[refusal])
        caller = Caller("r", scripted)
        caller.on_revoked(told.append)
        from mas.library.standard.testing.identity_fakes import FakeChannel

        ctx = {
            "record": "r",
            "principal": {},
            "audience": AUD,
            "task": "",
            "peer": {},
            "channel": FakeChannel(),
            "now": 0.0,
        }
        with pytest.raises(IdentityError, match="revoked"):
            await caller.handshake(ctx)  # type: ignore[arg-type]
        assert caller.is_revoked(IDENTITY_ID)
    else:
        with pytest.raises(IdentityError, match="revoked"):
            if operation == "present":
                await caller.present(_req(identity="side"))
            elif operation == "describe":
                await caller.describe("side")
            elif operation == "delegate":
                await caller.delegate(Delegation(identity="side", grant={"scopes": ["a"]}))
            else:
                await caller.revoke("side")
        assert caller.is_revoked("side") and not caller.is_revoked("main")
    assert told and any(e["kind"] == "identity.revoke" for e in events)
    # nothing is asked of the wallet again for what was revoked
    before = wallet.calls
    with pytest.raises(IdentityError, match="revoked"):
        await caller.present(_req(identity="side") if operation != "handshake" else _req())
    assert wallet.calls == before


def _raises(error: BaseException):
    async def raiser(*_: Any, **__: Any) -> Any:
        raise error

    return raiser


async def test_revoking_an_identity_revokes_what_was_derived_from_it_and_forgets_its_cache() -> None:
    wallet = _Wallet()
    wallet.children.append(
        IdentityInfo(id="kid", kind="delegated", subject="", labels=[], status="active", parent="main")
    )
    caller = Caller("r", wallet)
    await caller.present(_req(identity="kid"))
    caller.notify_revoked("main")
    assert caller.is_revoked("kid") and caller.is_revoked("main") and not caller.is_revoked("side")
    with pytest.raises(IdentityError, match="revoked"):
        await caller.present(_req(identity="kid"))


async def test_a_revoked_from_verify_is_the_peers_credential_not_this_wallet() -> None:
    wallet = _Wallet()
    wallet.verify_error = IdentityError("revoked")
    caller = Caller("r", wallet)
    with pytest.raises(IdentityError, match="revoked"):
        await caller.verify(Inbound(direction="request", protocol="a2a"))
    assert not caller.is_revoked("main") and not caller.is_revoked("*")


# --- verify -------------------------------------------------------------------------------------


async def test_a_challenge_is_checked_and_its_carry_keys_are_the_adapters() -> None:
    wallet = _Wallet()
    wallet.verify_error = ChallengeRequired({"nonce": "n", "audience": "a"}, carry={"a2a.metadata_key": "k"})
    caller = Caller("r", wallet)
    with pytest.raises(ChallengeRequired):
        await caller.verify(Inbound(direction="request", protocol="a2a"), carry_keys=frozenset({"a2a.metadata_key"}))
    with pytest.raises(IdentityError, match="invalid"):
        await caller.verify(Inbound(direction="request", protocol="a2a"))  # a key no adapter declared


async def test_an_inbound_the_peer_set_the_time_of_gets_the_callers_time() -> None:
    scripted = Scripted(verified=Verified(subject="u", claims={}, source="other"))
    await Caller("r", scripted, clock=lambda: 7.0).verify(Inbound(direction="request", protocol="a2a", now=99.0))
    assert scripted.inbounds[0]["now"] == 7.0


# --- delegate -----------------------------------------------------------------------------------


async def test_a_delegation_that_widens_the_parents_grant_is_refused() -> None:
    wallet = _Wallet()
    wallet.delegated = IdentityInfo(
        id="kid", kind="delegated", subject="", labels=[], status="active", parent="main", grant={"scopes": ["a", "z"]}
    )
    with pytest.raises(IdentityError, match="refused:delegation_widens"):
        await Caller("r", wallet).delegate(Delegation(identity="main", grant={"scopes": ["a"]}))


async def test_a_delegation_with_no_grant_under_a_parent_that_has_one_is_refused() -> None:
    wallet = _Wallet()
    wallet.delegated = IdentityInfo(id="kid", kind="delegated", subject="", labels=[], status="active", parent="main")
    with pytest.raises(IdentityError, match="refused:delegation_widens"):
        await Caller("r", wallet).delegate(Delegation(identity="main"))


async def test_a_narrower_delegation_is_the_new_identity() -> None:
    wallet = _Wallet()
    wallet.delegated = IdentityInfo(
        id="kid", kind="delegated", subject="", labels=[], status="active", parent="main", grant={"scopes": ["a"]}
    )
    child = await Caller("r", wallet).delegate(Delegation(identity="main", grant={"scopes": ["a"]}))
    assert child["id"] == "kid" and child["parent"] == "main"


# --- the sync twin ----------------------------------------------------------------------------


def test_the_sync_twin_runs_on_a_loop_of_its_own_when_this_thread_has_none() -> None:
    wallet = _Wallet()
    evidence = Caller("r", wallet).present_blocking(_req())
    assert evidence["headers"] == {"x-ev": "1"}


def test_the_sync_twin_works_from_a_plain_worker_thread() -> None:
    wallet = _Wallet()
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(Caller("r", wallet).present_blocking(_req())))
    thread.start()
    thread.join(10)
    assert out and out[0]["headers"] == {"x-ev": "1"}


async def test_a_sync_call_from_a_thread_that_runs_a_loop_is_refused_not_blocked() -> None:
    with pytest.raises(IdentityError, match="unsatisfiable:sync_in_loop"):
        Caller("r", _Wallet()).present_blocking(_req())


# --- events and values ------------------------------------------------------------------------


async def test_events_say_what_happened_and_hold_no_value(events: list[dict[str, Any]]) -> None:
    wallet = _Wallet()
    wallet.evidence = Evidence(headers={"authorization": "Bearer CANARY-123"}, valid_until=FAR)
    caller = Caller("r", wallet)
    await caller.present(_req())
    wallet.present_error = IdentityError("refused", "no")
    with pytest.raises(IdentityError):
        await caller.present(_req(identity="side"))
    assert [(e["kind"], e["outcome"]) for e in events] == [("identity.present", "ok"), ("identity.present", "denied")]
    assert events[1]["code"] == "refused:no" and events[0]["actor"] == "a" and events[0]["record"] == "r"
    assert "CANARY-123" not in json.dumps(events) and "Bearer" not in json.dumps(events)
    assert "CANARY-123" not in repr(caller) and "CANARY-123" not in repr(caller.identities())


def test_the_rulebook_refuses_a_field_it_does_not_know_and_a_malformed_value() -> None:
    good = {"kind": "identity.present", "source": "identity", "ts": 1, "event_id": "abc", "outcome": "ok"}
    observe.validate_event(good)
    for bad in (
        {**good, "token": "x"},
        {**good, "kind": "identity.other"},
        {**good, "host": "a b"},
        {**good, "outcome": "maybe"},
        {**good, "grant": {"secret": 1}},
    ):
        with pytest.raises(observe.EventError):
            observe.validate_event(bad)


async def test_every_revoke_that_ends_revoked_publishes_exactly_one_event(events: list[dict[str, Any]]) -> None:
    """Kills: a repeated or already-revoked revoke that is answered but leaves no `identity.revoke` event behind."""
    caller = Caller("r", _Wallet())
    revokes = lambda: [e for e in events if e["kind"] == "identity.revoke"]  # noqa: E731
    await caller.revoke("side")
    assert len(revokes()) == 1
    await caller.revoke("side")  # already revoked here: one more answer, one more event
    assert len(revokes()) == 2
    told: list[str] = []
    caller.on_revoked(told.append)
    await caller.revoke("side")
    assert told == []  # a listener hears a transition, not a repeat
    assert len(revokes()) == 3


async def test_a_delegation_with_no_named_parent_is_checked_against_the_one_the_wallet_selects() -> None:
    """Kills: a delegation without `identity` that skips the grant or revocation check of the selected one."""
    wallet = _Wallet()
    wallet.delegated = IdentityInfo(
        id="d", kind="agent", subject="", labels=[], status="active", grant={"scopes": ["a", "z"]}, parent="main"
    )
    caller = Caller("r", wallet)
    with pytest.raises(IdentityError, match="delegation_widens"):  # `main` holds a, b: z widens it
        await caller.delegate(Delegation(grant={"scopes": ["a", "z"]}))
    caller.notify_revoked("main")
    with pytest.raises(IdentityError, match="revoked"):
        await caller.delegate(Delegation(grant={"scopes": ["a"]}))


async def test_a_parent_outside_the_wallet_bounds_the_child_by_its_own_evidence_only() -> None:
    """Kills: a fallback to the wallet's own grant when the parent's evidence carries none (a confused deputy)."""
    wallet = _Wallet()
    wallet.delegated = IdentityInfo(
        id="d", kind="agent", subject="", labels=[], status="active", grant={"scopes": ["a"]}, parent="main"
    )
    caller = Caller("r", wallet)
    with pytest.raises(IdentityError, match="delegation_widens"):
        await caller.delegate(Delegation(identity="main", grant={"scopes": ["a"]}, parent_evidence=Evidence()))
    child = await caller.delegate(
        Delegation(identity="main", grant={"scopes": ["a"]}, parent_evidence=Evidence(grant={"scopes": ["a", "b"]}))
    )
    assert child["id"] == "d"
    with pytest.raises(IdentityError, match="delegation_widens"):
        await caller.delegate(
            Delegation(identity="main", grant={"scopes": ["a"]}, parent_evidence=Evidence(grant={"scopes": ["c"]}))
        )


async def test_a_wallet_that_cannot_select_refuses_instead_of_acting_on_the_whole_wallet() -> None:
    """Kills: a select that raises being read as `_ALL`, so a revocation check passes for the wrong target."""
    wallet = _Wallet()

    def broken(req: Any) -> str:
        raise RuntimeError("boom")

    wallet.select = broken  # type: ignore[method-assign]
    caller = Caller("r", wallet)
    with pytest.raises(IdentityError, match="unavailable"):
        await caller.present(_req())
    assert wallet.calls == 0


async def test_a_challenge_for_another_audience_is_not_answered() -> None:
    """Kills: a holder that answers a challenge a peer bound to someone else (the credential would go astray)."""
    wallet = _Wallet()
    caller = Caller("r", wallet)
    ask = lambda challenge, wire=None: caller.present(  # noqa: E731
        _req(phase="challenge", peer={"challenge": challenge, "wire": wire})
    )
    with pytest.raises(IdentityError, match="wrong_audience"):
        await ask({"nonce": "n", "audience": "http://elsewhere.test"})
    with pytest.raises(IdentityError, match="wire_too_large"):
        await ask({"nonce": "n"}, "x" * 9000)
    with pytest.raises(IdentityError, match="challenge_key_not_namespaced"):
        await ask({"bogus": "x"})
    assert wallet.calls == 0
    await ask({"nonce": "n", "audience": AUD["target"]})
    assert wallet.calls == 1


async def test_a_handshake_asks_the_wallets_selected_identity_and_refuses_headers() -> None:
    """Kills: a handshake that checks only the whole-wallet revocation, or drops a scheme's headers silently."""
    from mas.library.standard.testing.identity_fakes import FakeChannel

    scripted = Scripted(handshakes=[Evidence(headers={"x": "1"})])
    caller = Caller("r", scripted)
    ctx: Any = {"record": "r", "principal": {}, "audience": AUD, "task": "", "peer": {}, "channel": FakeChannel()}
    with pytest.raises(IdentityError, match="handshake_headers"):
        await caller.handshake(ctx)
    other = Caller("r", Scripted(handshakes=[Evidence(meta={"a/b": 1})]))
    other.notify_revoked(IDENTITY_ID)
    with pytest.raises(IdentityError, match="revoked"):
        await other.handshake(ctx)
    named = Caller("r", Scripted(handshakes=[Evidence(meta={"a/b": 1})]))
    named.notify_revoked("badge")
    assert (await named.handshake({**ctx, "identity": "someone-else"}))["meta"] == {"a/b": 1}


async def test_a_malformed_challenge_from_a_verifier_is_denied_and_published(events: list[dict[str, Any]]) -> None:
    """Kills: a malformed ChallengeRequired that fails validation and leaves no verify event behind."""
    wallet = _Wallet()
    wallet.verify_error = ChallengeRequired({"bogus": "x"})
    with pytest.raises(IdentityError, match="challenge_key_not_namespaced"):
        await Caller("r", wallet).verify(Inbound(direction="request", protocol="mcp", audience=AUD))
    assert [e["outcome"] for e in events if e["kind"] == "identity.verify"] == ["denied"]


async def test_a_scheme_that_overruns_its_budget_with_its_own_error_is_still_a_timeout() -> None:
    """Kills: a scheme error raised after the deadline that reaches the host as the scheme's own error."""

    class Slow(_Wallet):
        async def present(self, req: PresentRequest) -> Evidence:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise RuntimeError("secret-in-message") from None
            return Evidence()

    with pytest.raises(IdentityError, match="unavailable:timeout"):
        await Caller("r", Slow(), timeout=0.05).present(_req())


async def test_any_revocation_forgets_what_was_cached_for_the_whole_wallet() -> None:
    """Kills: an evidence cached under "the wallet chose" that survives the revocation of one of its identities."""
    wallet = _Wallet()
    wallet.select = lambda req: None  # type: ignore[method-assign,assignment]
    caller = Caller("r", wallet)
    await caller.present(_req())
    await caller.present(_req())
    assert wallet.calls == 1
    caller.notify_revoked("side")
    await caller.present(_req())
    assert wallet.calls == 2


def test_an_event_cuts_what_a_peer_chose_to_the_rulebook_instead_of_publishing_it(events: list[dict[str, Any]]) -> None:
    """Kills: a peer-supplied id or operation (spaces, newlines, 10 kB) that breaks the event vocabulary."""
    event = observe.emit(
        "identity.present",
        outcome="ok",
        actor="ag\nent",
        operation="skill id\r\n" + "x" * 500,
        identity="éé",
        code="bad code!",
    )
    observe.validate_event(event)
    assert event["actor"] == "ag_ent" and len(event["operation"]) <= 192 and "\n" not in event["operation"]
    assert "identity" not in event and event["code"] == "bad_code_"


async def test_an_inbound_meta_a_peer_chose_is_bounded_before_a_verifier_reads_it() -> None:
    """Kills: a 1 MB or non-JSON `meta` from a peer reaching the scheme's verify."""
    caller = Caller("r", _Wallet())
    ask = lambda meta: caller.verify(Inbound(direction="request", protocol="a2a", meta=meta))  # noqa: E731
    with pytest.raises(IdentityError, match="inbound_meta_too_large"):
        await ask({"k": "x" * 20000})
    with pytest.raises(IdentityError, match="inbound_meta"):
        await ask({"k": object()})
    assert (await ask({"k": {"nested": [1, 2]}}))["subject"] == "u"


async def test_a_handshake_does_not_answer_a_challenge_for_another_audience_or_a_malformed_one() -> None:
    """Kills: a handshake (A2A auth-required, MCP) that hands a peer's unchecked challenge to the scheme."""
    from mas.library.standard.testing.identity_fakes import FakeChannel

    scripted = Scripted(handshakes=[Evidence(meta={"a/b": 1})] * 3)
    caller = Caller("r", scripted)

    def ctx(challenge: Any, wire: Any = None) -> Any:
        return {
            "record": "r",
            "principal": {},
            "audience": AUD,
            "task": "",
            "peer": {"challenge": challenge, "wire": wire},
            "channel": FakeChannel(),
        }

    with pytest.raises(IdentityError, match="wrong_audience"):
        await caller.handshake(ctx({"nonce": "n", "audience": "http://elsewhere.test"}))
    with pytest.raises(IdentityError, match="challenge_not_mapping"):
        await caller.handshake(ctx(["not", "a", "mapping"]))
    with pytest.raises(IdentityError, match="wire_too_large"):
        await caller.handshake(ctx({"nonce": "n"}, "x" * 9000))
    assert scripted.handshake_contexts == []  # the scheme was never asked
    assert (await caller.handshake(ctx({"nonce": "n", "audience": AUD["target"]})))["meta"] == {"a/b": 1}
    # the challenge a peer carries in the session is the scheme's own shape: its keys are not the contract's grammar
    assert (await caller.handshake(ctx({"request": {"claims": ["x"]}, "policy": "p"})))["meta"] == {"a/b": 1}


async def test_a_delegation_needs_a_parent_that_exists_and_a_child_that_says_it_derives_from_it() -> None:
    """Kills: a delegation with nothing selected, an unknown parent, or a child of some other identity, accepted."""
    wallet = _Wallet()
    caller = Caller("r", wallet)
    wallet.delegated = IdentityInfo(id="d", kind="agent", subject="", labels=[], status="active", parent="side")
    with pytest.raises(IdentityError, match="delegation_parent"):  # asked about `main`, the child names `side`
        await caller.delegate(Delegation(identity="main", grant={"scopes": ["a"]}))
    with pytest.raises(IdentityError, match="unknown_identity"):
        await caller.delegate(Delegation(identity="nobody", grant={"scopes": ["a"]}))
    wallet.children.clear()
    wallet.select = lambda req: None  # type: ignore[method-assign,assignment]
    with pytest.raises(IdentityError, match="no_parent"):
        await caller.delegate(Delegation(grant={"scopes": ["a"]}))
    assert wallet.children == []  # the wallet was never asked


async def test_a_revoked_answer_with_no_identity_named_does_not_revoke_the_whole_wallet(events: list) -> None:
    """Kills: a per-identity `revoked` from a wallet that selected nothing escalating to every identity it holds."""
    wallet = _Wallet()
    wallet.select = lambda req: None  # type: ignore[method-assign,assignment]
    wallet.present_error = IdentityError("revoked")
    caller = Caller("r", wallet)
    told: list[str] = []
    caller.on_revoked(told.append)
    with pytest.raises(IdentityError, match="revoked"):
        await caller.present(_req())
    assert not caller.is_revoked("main") and not caller.is_revoked("side") and told == []
    assert [e for e in events if e["kind"] == "identity.revoke"] == []

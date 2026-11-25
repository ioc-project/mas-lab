#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity gate: every outcome, the stale view, the wallets it reads, and that it never blocks the event loop."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from mas.library.standard.lib.identity import holders, observe
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import (
    IdentityError,
    IdentityInfo,
    InteractionRequired,
    PublicView,
)
from mas.library.standard.lib.identity.requirements import GateConfigError
from mas.library.standard.plugins.identity.gate import IdentityGate
from mas.library.standard.testing.identity_scripted import Scripted
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

REQ = {"get_fares": {"identity": "desk-badge", "require": [{"fact": "group", "equals": "infraadmin"}]}}


class Wallet(Scripted):
    """A scripted wallet whose public views (or error) a test sets, with an optional delay; it holds one or two ids."""

    def __init__(
        self,
        facts: Mapping[str, Any] | None = None,
        *,
        delay: float = 0.0,
        error: Exception | None = None,
        ids: tuple[str, ...] = ("badge",),
        facts_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.facts = dict(facts or {})
        self.facts_by_id = {k: dict(v) for k, v in (facts_by_id or {}).items()}
        self.delay, self.error, self.ids, self.calls = delay, error, ids, 0
        self.status = "active"  # what the wallet lists for every identity; a test sets it to say it was revoked

    def identities(self) -> list[IdentityInfo]:
        return [IdentityInfo(id=i, kind="agent", subject="", labels=[], status=self.status) for i in self.ids]

    async def describe(self, ref: str) -> PublicView:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return PublicView(facts=self.facts_by_id.get(ref, self.facts))


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _no_holders() -> Iterator[None]:
    holders.reset()
    yield
    holders.reset()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observe, "publish_event", seen.append)
    monkeypatch.setattr(observe, "emit_to_channel", lambda event: None)
    return seen


def hold(wallet: Wallet, agent: str = "desk", record: str = "desk-badge") -> Caller:
    caller = Caller(record, wallet)
    holders.register(agent, record, caller)
    return caller


def rig(wallet: Wallet | None, requirements: Any = REQ, **kw: Any) -> tuple[IdentityGate, Clock, Caller | None]:
    clock = Clock()
    caller = hold(wallet) if wallet is not None else None
    gate = IdentityGate(requirements, clock=clock, wall_clock=lambda: 1_000.0, **kw)
    return gate, clock, caller


def warm(gate: IdentityGate, agent: str = "desk") -> None:
    gate.prefetch(agent)
    gate.settle()


def decide(gate: IdentityGate, tool: str = "get_fares", agent: str = "desk", **cfg: Any):
    intent = EgressIntentView(op="TOOL_CALL", destructive=False, correlation_id=1, tool_name=tool)
    return gate.evaluate_egress(intent, config=KernelConfig(agent_id=agent, **cfg))


def test_allow_when_the_view_covers_the_requirement() -> None:
    """Kills: a requirement check that always fails, or one that ignores the held facts."""
    gate, *_ = rig(Wallet({"group": "infraadmin"}), refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW


def test_scope_uncovered_names_the_requirement_and_no_value() -> None:
    """Kills: a gate that allows a view missing the group; a reason that leaks a held value."""
    gate, *_ = rig(Wallet({"group": "dev"}), refresh_after_s=0)
    warm(gate)
    decision, _, reason = decide(gate)
    assert (decision, reason) == (GovDecision.BLOCK, "scope_uncovered:group")


def test_a_wallet_covers_a_requirement_when_any_one_of_its_identities_does() -> None:
    """Kills: a gate that reads only the wallet's first identity."""
    wallet = Wallet(ids=("low", "high"), facts_by_id={"low": {"group": "dev"}, "high": {"group": "infraadmin"}})
    gate, *_ = rig(wallet, refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW
    assert wallet.calls == 2


def test_a_requirement_can_name_one_identity_of_the_wallet() -> None:
    """Kills: `task` or `audience` ignored: the whole wallet is read instead of the one identity."""
    wallet = Wallet(ids=("low", "high"), facts_by_id={"low": {"group": "dev"}, "high": {"group": "infraadmin"}})
    req = {"get_fares": {**REQ["get_fares"], "audience": "low"}}
    gate, *_ = rig(wallet, req, refresh_after_s=0)
    warm(gate)
    assert decide(gate)[2] == "scope_uncovered:group"
    assert wallet.calls == 1


def test_no_identity_when_the_agent_holds_no_wallet_for_the_record() -> None:
    """Kills: a gate that allows an agent that holds nothing."""
    gate, *_ = rig(None)
    assert decide(gate)[2] == "no_identity"
    other = Caller("desk-badge", Wallet({"group": "infraadmin"}))
    holders.register("someone-else", "desk-badge", other)
    assert decide(gate)[2] == "no_identity"  # a wallet of another agent is not this agent's


def test_a_wallet_held_by_no_agent_in_particular_serves_every_agent() -> None:
    """Kills: a shared tool-server stack whose wallet no gate can find."""
    shared = Caller("desk-badge", Wallet({"group": "infraadmin"}))  # held weakly: the layer that built it keeps it
    holders.register("", "desk-badge", shared)
    gate = IdentityGate(REQ, refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW


def test_a_requirement_without_a_record_reads_the_records_the_agent_holds() -> None:
    """Kills: a requirement with no `identity` that matches nothing."""
    req = {"get_fares": {"require": [{"fact": "group", "equals": "infraadmin"}]}}
    gate, *_ = rig(Wallet({"group": "infraadmin"}), req, refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW


def test_a_revoked_fact_is_data_not_a_revocation_and_a_describe_error_is() -> None:
    """Kills: a gate that reads a `revoked` fact key (facts are opaque), or ignores a `revoked` error."""
    gate, *_ = rig(Wallet({"group": "infraadmin", "revoked": True}), refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW
    gate2 = IdentityGate(REQ, refresh_after_s=0)
    holders.reset()
    keep = hold(Wallet(error=IdentityError("revoked")))  # held weakly: keep it
    warm(gate2)
    assert decide(gate2)[2] == "credential_revoked"
    assert keep is not None


def test_a_revocation_the_caller_knows_is_seen_at_once() -> None:
    """Kills: a gate that reads only the cached view: stale, not revoked."""
    gate, _, caller = rig(Wallet({"group": "infraadmin"}), refresh_after_s=1000)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW
    assert caller is not None
    caller.notify_revoked("badge")
    assert decide(gate)[2] == "credential_revoked"


def test_a_wallet_whose_every_identity_lists_revoked_is_revoked_at_once() -> None:
    """Kills: a gate that looks only at active identities, so a wallet that lists none active is not revoked."""
    wallet = Wallet({"group": "infraadmin"}, ids=("badge", "spare"))
    gate, *_ = rig(wallet, refresh_after_s=1000)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW
    wallet.status = "revoked"
    assert decide(gate)[2] == "credential_revoked"  # no refresh ran: the listing alone says so


def test_a_wallet_with_one_identity_left_is_not_revoked() -> None:
    """Kills: a gate that calls the wallet revoked when only some identities are."""
    wallet = Wallet({"group": "infraadmin"}, ids=("badge", "spare"))
    gate, _, caller = rig(wallet, refresh_after_s=1000)
    warm(gate)
    assert caller is not None
    caller.notify_revoked("badge")
    assert decide(gate)[0] is GovDecision.ALLOW


def test_a_revocation_mid_run_is_seen_by_the_next_refresh() -> None:
    """Kills: a cache that is never refreshed after the first fill."""
    wallet = Wallet({"group": "infraadmin"})
    gate, *_ = rig(wallet, refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW
    wallet.error = IdentityError("revoked")
    warm(gate)
    assert decide(gate)[2] == "credential_revoked"


def test_stale_and_missing_views_fail_closed() -> None:
    """Kills: an allow on an empty cache; a max age that is not enforced."""
    wallet = Wallet({"group": "infraadmin"})
    gate, clock, _ = rig(wallet, max_age_s=10, refresh_after_s=1000)
    assert decide(gate)[2] == "gate_stale"  # nothing was read yet: blocked, the refresh it kicked fills the view
    gate.settle()
    assert decide(gate)[0] is GovDecision.ALLOW
    clock.now += 11  # older than max_age_s, and not old enough to refresh
    assert decide(gate)[2] == "gate_stale"


def test_a_failing_describe_is_gate_error() -> None:
    """Kills: a describe failure that falls through to allow."""
    gate, *_ = rig(Wallet(error=RuntimeError("boom")), refresh_after_s=0)
    warm(gate)
    assert decide(gate)[2] == "gate_error"


def test_an_unexpected_exception_in_the_decision_is_gate_error() -> None:
    """Kills: an exception that escapes the gate (fails open in the chain)."""
    gate, *_ = rig(Wallet({"group": "infraadmin"}), refresh_after_s=0)
    bad = type("Bad", (), {"op": "TOOL_CALL", "tool_name": 5})()  # `.strip` on an int
    assert gate.evaluate_egress(bad, config=KernelConfig(agent_id="desk"))[2] == "gate_error"  # type: ignore[arg-type]


def test_not_applicable_is_allow() -> None:
    """Kills: a gate that blocks calls it has no requirement for (breaks the chain contract)."""
    gate, *_ = rig(Wallet(), refresh_after_s=0)
    assert decide(gate, "other_tool")[0] is GovDecision.ALLOW
    llm = EgressIntentView(op="LLM_CALL", destructive=False, correlation_id=1)
    assert gate.evaluate_egress(llm, config=KernelConfig(agent_id="desk"))[0] is GovDecision.ALLOW


def test_interaction_is_hitl_only_when_hitl_is_configured() -> None:
    """Kills: HITL without a human in the loop (a hold nobody answers); BLOCK where HITL is configured."""
    gate, *_ = rig(Wallet(error=InteractionRequired("login", "sign in")), refresh_after_s=0)
    warm(gate)
    assert decide(gate, hitl_on_tool=True)[0] is GovDecision.HITL
    assert decide(gate)[:1] == (GovDecision.BLOCK,)


def test_the_gate_never_blocks_the_event_loop() -> None:
    """Kills: a describe awaited on the turn's loop: a 0.4 s describe would stall the ticker."""
    gate, *_ = rig(Wallet({"group": "infraadmin"}, delay=0.4), refresh_after_s=0)

    async def main() -> float:
        worst, last = 0.0, time.perf_counter()
        done = asyncio.Event()

        async def ticker() -> None:
            nonlocal worst, last
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                worst, last = max(worst, now - last), now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        assert decide(gate)[2] == "gate_stale"  # not ready yet: blocked, not waited for
        await asyncio.sleep(0.5)
        done.set()
        await task
        return worst

    assert asyncio.run(main()) < 0.2


def test_first_view_wait_makes_the_first_decision_wait_for_the_view() -> None:
    """Kills: a wait that is not bounded, or an opt-in that does nothing."""
    gate, _, keep = rig(Wallet({"group": "infraadmin"}, delay=0.1), refresh_after_s=0, first_view_wait_s=2)
    assert decide(gate)[0] is GovDecision.ALLOW
    assert keep is not None
    holders.reset()
    slow, *_ = rig(Wallet({"group": "infraadmin"}, delay=1.0), refresh_after_s=0, first_view_wait_s=0.05)
    assert decide(slow)[2] == "gate_stale"


def test_a_chain_requirement_reads_the_bound_principal_and_needs_no_view() -> None:
    """Kills: a principal fact taken from the wallet's view; a chain that is read as direct when unknown."""
    wallet = Wallet({"principal": {"chain": ["evil"]}})
    req = {"get_fares": {"identity": "desk-badge", "require": [{"fact": "principal.chain_depth", "max": 1}]}}
    bound = {"chain": ["desk", "front-desk"]}
    gate, *_ = rig(wallet, req, bound_principal=lambda: bound)
    assert decide(gate)[2] == "scope_uncovered:principal.chain_depth"
    bound["chain"] = ["desk"]
    assert decide(gate)[0] is GovDecision.ALLOW
    assert wallet.calls == 0
    unknown, *_ = rig(wallet, req, bound_principal=dict)
    assert decide(unknown)[2] == "scope_uncovered:principal.chain_depth"  # unknown is not direct


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"t": []},
        {"t": {"require": {}}},
        {"t": {"require": [{"fact": "x"}]}},
        {"t": {"require": [{"equals": 1}]}},
        {"t": {"identity": ""}},
        {"t": {"nope": 1}},
    ],
)
def test_malformed_requirements_are_refused(bad: Any) -> None:
    """Kills: a malformed requirement that becomes a block at decision time instead of a configuration error."""
    with pytest.raises(GateConfigError):
        IdentityGate(bad)


@pytest.mark.parametrize(
    "kw", [{"events": "some"}, {"max_age_s": 0}, {"refresh_after_s": -1}, {"first_view_wait_s": -1}]
)
def test_bad_settings_are_refused(kw: dict[str, Any]) -> None:
    """Kills: a setting that is accepted and then misbehaves."""
    with pytest.raises(GateConfigError):
        IdentityGate(REQ, **kw)


def test_every_decision_is_an_event_with_a_code_and_no_value(events: list[dict[str, Any]]) -> None:
    """Kills: a decision that is not published, or an event that carries a held value."""
    gate, *_ = rig(Wallet({"group": "dev", "secret_fact": "CANARY-VALUE"}), refresh_after_s=0)
    warm(gate)
    decide(gate)
    decide(gate, "other_tool")
    decide(gate, "")
    gated = [e for e in events if e["kind"] == "identity.gate"]
    assert len(gated) == 3
    first = gated[0]
    assert (first["outcome"], first["code"], first["record"], first["actor"]) == (
        "denied",
        "scope_uncovered:group",
        "desk-badge",
        "desk",
    )
    assert first["operation"] == "tools/call:get_fares"
    for event in gated:
        observe.validate_event(event)
        assert "CANARY-VALUE" not in repr(event)


def test_events_tools_publishes_only_the_decisions_about_tool_calls(events: list[dict[str, Any]]) -> None:
    """Kills: an `events: tools` that still publishes a model call's allow."""
    gate, *_ = rig(Wallet({"group": "infraadmin"}), refresh_after_s=0, events="tools")
    warm(gate)
    decide(gate)
    gate.evaluate_egress(
        EgressIntentView(op="LLM_CALL", destructive=False, correlation_id=2), config=KernelConfig(agent_id="desk")
    )
    assert [e["outcome"] for e in events if e["kind"] == "identity.gate"] == ["ok"]


def test_events_off_publishes_none_and_leaves_the_decision(events: list[dict[str, Any]]) -> None:
    """Kills: an `events: off` that changes a decision."""
    gate, *_ = rig(Wallet({"group": "dev"}), refresh_after_s=0, events="off")
    warm(gate)
    assert decide(gate)[2] == "scope_uncovered:group"
    assert not [e for e in events if e["kind"] == "identity.gate"]


def test_a_held_call_is_a_hold(events: list[dict[str, Any]]) -> None:
    """Kills: a HITL decision published as a block."""
    gate, *_ = rig(Wallet(error=InteractionRequired("login", "sign in")), refresh_after_s=0)
    warm(gate)
    decide(gate, hitl_on_tool=True)
    assert [e["outcome"] for e in events if e["kind"] == "identity.gate"] == ["hold"]


def test_two_runs_naming_the_same_agent_never_read_each_others_wallet() -> None:
    """Kills: wallets keyed by agent and record alone, so a concurrent run's wallet answers for this one.

    Two scopes hold the same agent and record; one wallet satisfies the requirement, the other is revoked. Each gate
    and each Surface sees only its own scope's wallet, and the unscoped lookup sees neither.
    """
    from mas.library.standard.lib.identity.surface import Surface

    good, bad = Caller("desk-badge", Wallet({"group": "infraadmin"})), Caller("desk-badge", Wallet({"group": "dev"}))
    holders.register("desk", "desk-badge", good, "run-a")
    holders.register("desk", "desk-badge", bad, "run-b")
    assert holders.wallets("desk", "desk-badge", "run-a") == [good]
    assert holders.wallets("desk", "desk-badge", "run-b") == [bad]
    assert holders.wallets("desk", "desk-badge") == []
    assert holders.records_of("desk", "run-c") == []

    for scope, allowed in (("run-a", True), ("run-b", False)):
        gate = IdentityGate(REQ, refresh_after_s=0)
        spec = {holders.SCOPE_KEY: scope}
        intent = EgressIntentView(op="TOOL_CALL", destructive=False, correlation_id=1, tool_name="get_fares")
        cfg = KernelConfig(agent_id="desk", agent_spec=spec)
        gate.evaluate_egress(intent, config=cfg)  # warms the views of this scope
        gate.settle()
        assert (gate.evaluate_egress(intent, config=cfg)[0] is GovDecision.ALLOW) is allowed
    other = Surface("desk", scope="run-b")
    assert {row["record"] for row in other.identities()} == {"desk-badge"}
    assert Surface("desk", scope="run-z").identities() == []


def test_a_wallet_shared_by_two_layers_is_one_wallet_to_the_gate_and_the_surface() -> None:
    """Kills: a caller registered once per layer that lists every identity twice and makes each of them ambiguous."""
    from mas.library.standard.lib.identity.surface import Surface

    shared = Caller("desk-badge", Wallet({"group": "infraadmin"}))
    release_one = holders.register("desk", "desk-badge", shared)
    release_two = holders.register("desk", "desk-badge", shared)
    assert holders.wallets("desk", "desk-badge") == [shared]
    assert [row["id"] for row in Surface("desk").identities()] == ["badge"]
    release_one()
    assert holders.wallets("desk", "desk-badge") == [shared]  # the other layer still holds it
    release_two()
    assert holders.wallets("desk", "desk-badge") == [] and holders.records_of("desk") == []


def test_two_distinct_wallets_listing_one_identity_are_one_target_and_a_revoke_reaches_both() -> None:
    """Kills: `ambiguous_identity` for the same id held by two wallets of a record, or a revoke that tells only one."""
    import asyncio

    from mas.library.standard.lib.identity.surface import Surface

    first, second = Caller("desk-badge", Wallet()), Caller("desk-badge", Wallet())
    holders.register("desk", "desk-badge", first)
    holders.register("desk", "desk-badge", second)
    surface = Surface("desk")
    assert len(surface.identities()) == 1
    asyncio.run(surface.revoke("badge"))
    assert first.is_revoked("badge") and second.is_revoked("badge")


def test_a_revoked_wallet_of_the_record_denies_however_many_others_would_allow() -> None:
    """Kills: a gate that allows when any wallet of the record allows, so a revoked one is outvoted."""
    good, revoked = Wallet({"group": "infraadmin"}), Wallet({"group": "infraadmin"})
    hold(good)
    dead = hold(revoked)
    gate = IdentityGate(REQ, refresh_after_s=0)
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW
    dead.notify_revoked("badge")
    assert decide(gate)[2] == "credential_revoked"


def test_an_identity_that_needs_no_facts_is_still_refused_when_revoked() -> None:
    """Kills: an empty `require:` that is answered "an identity is held" before the revoked check."""
    gate, _, caller = rig(Wallet(), {"get_fares": {"identity": "desk-badge"}})
    assert decide(gate)[0] is GovDecision.ALLOW
    assert caller is not None
    caller.notify_revoked("badge")
    assert decide(gate)[2] == "credential_revoked"


UNNAMED = {"get_fares": {"require": [{"fact": "group", "equals": "infraadmin"}]}}


def test_a_requirement_that_names_no_record_reads_the_agents_wallets_and_the_shared_ones() -> None:
    """Kills: a shared stack's wallet invisible to an unnamed requirement (every agent blocked as `no_identity`)."""
    gate, *_ = rig(None, UNNAMED, refresh_after_s=0)
    shared = hold(Wallet({"group": "infraadmin"}), agent="")  # held weakly: keep it
    warm(gate)
    assert decide(gate)[0] is GovDecision.ALLOW and shared is not None


def test_a_revoked_shared_wallet_denies_an_unnamed_requirement_even_when_the_agent_holds_another() -> None:
    """Kills: a shared wallet's revocation ignored because the agent also holds a wallet of its own."""
    gate, _clock, own = rig(Wallet({"group": "infraadmin"}), UNNAMED, refresh_after_s=0)
    shared = hold(Wallet({"group": "infraadmin"}), agent="", record="shared-badge")
    shared.notify_revoked("badge")
    warm(gate)
    assert decide(gate)[0] is GovDecision.BLOCK and own is not shared


def test_a_wallet_released_before_it_never_lends_its_cached_view_to_the_one_that_took_its_place() -> None:
    """Kills: a view cache keyed by the wallet's position, so the wallet that moves up reads its predecessor's view."""
    low, high = Wallet({"group": "dev"}), Wallet({"group": "infraadmin"})
    first = Caller("desk-badge", low)
    second = Caller("desk-badge", high)
    release_first = holders.register("desk", "desk-badge", first)
    holders.register("desk", "desk-badge", second)
    gate = IdentityGate(
        {"get_fares": {"identity": "desk-badge", "require": [{"fact": "group", "equals": "infraadmin"}]}}
    )
    gate.prefetch("desk")
    gate.settle()
    release_first()  # the second wallet is now the first and only one of the agent
    assert decide(gate)[0] is GovDecision.ALLOW


def test_asking_the_worker_never_happens_under_the_gates_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills: a `_kick` that holds the gate's lock while the worker (which may start a thread) is asked."""
    from mas.library.standard.plugins.identity import gate as gate_module

    gate, _clock, _caller = rig(Wallet({"group": "infraadmin"}), refresh_after_s=0)
    held: list[bool] = []
    real = gate_module._WORKER.submit

    def spying(coro: Any) -> Any:
        held.append(gate._lock.locked())
        return real(coro)

    monkeypatch.setattr(gate_module._WORKER, "submit", spying)
    warm(gate)
    assert held and not any(held)
    assert decide(gate)[0] is GovDecision.ALLOW


def test_a_wallet_with_no_credential_for_the_requirement_is_its_own_closed_answer(events: list[dict[str, Any]]) -> None:
    """Kills: `no_credential` from a wallet's describe ending in the gate's error path (`gate_error`)."""
    gate, *_ = rig(Wallet(error=IdentityError("no_credential")), refresh_after_s=0)
    warm(gate)
    decision, _plugin, reason = decide(gate)
    assert (decision, reason) == (GovDecision.BLOCK, "no_credential")
    gate_events = [e for e in events if e["kind"] == "identity.gate"]
    assert gate_events and gate_events[-1]["code"] == "no_credential"
    for event in events:
        observe.validate_event(event)

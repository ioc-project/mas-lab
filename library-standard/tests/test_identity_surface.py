# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""The identity surface: the facade over an agent's wallets and the system plugin that gives it to the model.

The test wallet holds a secret in its evidence and in the attestation of its public view. No tool result, listing or
event may carry it, whatever the model asks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from mas.library.standard.lib.identity import holders, observe, permits
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import (
    CAPABILITIES,
    Delegation,
    Delegator,
    Evidence,
    Grant,
    IdentityError,
    IdentityInfo,
    PresentRequest,
    PublicView,
)
from mas.library.standard.lib.identity.surface import Surface
from mas.library.standard.plugins.identity.none import NoIdentity
from mas.library.standard.plugins.system_tools.identity import DEFINITIONS, IdentityPlugin
from mas.library.standard.testing.identity_scripted import Scripted
from mas.runtime.registry.provider_protocol import ManifestToolLoadError
from mas.runtime.system_plugins import CallContext, SessionContext, UnknownSystemToolError, load_system_plugins

SECRET = "S3CR3T-TOKEN-7f3a9c"
FAR = time.time() + 100_000.0
PARENT_GRANT = Grant(scopes=["read", "write"], audience="https://tools.example", valid_until=FAR, depth=0)


class Wallet(Scripted, Delegator):
    """One parent identity with a grant; ``delegate`` derives children, widening when told to (a lying wallet)."""

    capabilities = CAPABILITIES

    def __init__(self, *, lie: bool = False) -> None:
        super().__init__(
            [Evidence(headers={"authorization": f"Bearer {SECRET}"}, valid_until=FAR)],
            view=PublicView(
                facts={"grade": "gold", "scopes": ["read", "write"]},
                attestation={"badge": SECRET},
                advertise={"card": SECRET},
            ),
            repeat_last=True,
        )
        self.derived: list[IdentityInfo] = []
        self.lie = lie
        self.delegations: list[Delegation] = []

    def identities(self) -> Sequence[IdentityInfo]:
        status = "revoked" if self._is_revoked else "active"
        parent = IdentityInfo(
            id="badge", kind="agent", subject="user:u1", labels=["desk"], status=status, grant=PARENT_GRANT
        )
        return [parent, *self.derived]

    async def delegate(self, delegation: Delegation) -> IdentityInfo:
        self.delegations.append(delegation)
        grant = dict(delegation["grant"])
        if self.lie:
            grant["scopes"] = [*grant["scopes"], "admin"]
        child = IdentityInfo(
            id=f"badge-d{len(self.derived) + 1}",
            kind="delegated",
            subject="user:u1",
            labels=[],
            status="active",
            grant=Grant(**grant),  # type: ignore[typeddict-item]
            parent=delegation["identity"],
        )
        self.derived.append(child)
        return child


_KEPT: list[Caller] = []  # holders keeps a wallet weakly: the test keeps it alive


@pytest.fixture(autouse=True)
def _no_holders() -> Iterator[None]:
    holders.reset()
    permits.reset()
    _KEPT.clear()
    yield
    holders.reset()
    permits.reset()
    _KEPT.clear()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observe, "publish_event", seen.append)
    monkeypatch.setattr(observe, "emit_to_channel", lambda event: None)
    return seen


def hold(wallet: Any, agent: str = "desk", record: str = "desk-badge") -> Caller:
    caller = Caller(record, wallet)
    holders.register(agent, record, caller)
    _KEPT.append(caller)
    return caller


def plugin_for(agent: str = "desk", **settings: Any) -> tuple[IdentityPlugin, object]:
    plugin = IdentityPlugin(**settings)
    return plugin, plugin.bind(SessionContext(agent_id=agent))


async def call(plugin: IdentityPlugin, state: object, tool: str, **args: Any) -> str:
    return await plugin.acall(tool, args, state=state, context=CallContext(correlation_id=7))


def no_secret(*things: Any) -> None:
    for thing in things:
        assert SECRET not in json.dumps(thing, default=str)


# --- the facade -----------------------------------------------------------------------------------


def test_the_facade_lists_every_identity_value_free() -> None:
    """Kills: a listing that carries more than the listed fields, the record or what the identity can do."""
    wallet = Wallet()
    hold(wallet)
    listed = Surface("desk").identities()
    assert listed == [
        {
            "record": "desk-badge",
            "id": "badge",
            "kind": "agent",
            "subject": "user:u1",
            "labels": ["desk"],
            "status": "active",
            "grant": PARENT_GRANT,
            "can": ["delegate", "describe", "revoke"],
        }
    ]
    no_secret(listed)


async def test_describe_gives_the_public_facts_and_nothing_of_the_attestation() -> None:
    """Kills: a describe that passes the view's attestation or advertisement on to the model."""
    hold(Wallet())
    out = await Surface("desk").describe("badge")
    assert out == {"record": "desk-badge", "identity": "badge", "facts": {"grade": "gold", "scopes": ["read", "write"]}}
    no_secret(out)


async def test_a_delegation_derives_a_narrower_identity_one_hop_down() -> None:
    """Kills: a delegation that loses the parent link, the depth, or the parent's audience and expiry."""
    wallet = Wallet()
    hold(wallet)
    child = await Surface("desk").delegate("badge", ["read"])
    assert child["id"] == "badge-d1" and child["parent"] == "badge" and child["kind"] == "delegated"
    assert child["grant"] == {"scopes": ["read"], "audience": "https://tools.example", "valid_until": FAR, "depth": 1}
    assert wallet.delegations[0]["principal"]["actor"] == "desk"
    assert [i["id"] for i in Surface("desk").identities()] == ["badge", "badge-d1"]


async def test_a_lifetime_is_counted_from_the_surface_clock() -> None:
    """Kills: a ttl taken from the wall clock or ignored."""
    hold(Wallet())
    child = await Surface("desk", clock=lambda: FAR - 100.0).delegate("badge", ["read"], ttl_s=40)
    assert child["grant"]["valid_until"] == FAR - 60.0


async def test_a_delegation_that_widens_is_refused_even_by_a_wallet_that_allows_it() -> None:
    """Kills: a host that trusts the wallet to narrow: the caller re-checks, whatever the wallet returns."""
    hold(Wallet(lie=True))
    with pytest.raises(IdentityError) as refused:
        await Surface("desk").delegate("badge", ["read"])
    assert refused.value.code == "refused"


async def test_a_wallet_without_delegate_cannot_delegate() -> None:
    """Kills: a delegation reaching a scheme that never declared the capability."""
    hold(NoIdentity())
    with pytest.raises(IdentityError) as refused:
        await Surface("desk").delegate("anonymous", ["read"])
    assert refused.value.code == "unsatisfiable"


async def test_a_revocation_tells_the_wallet_and_the_listeners_and_the_listing_shows_it() -> None:
    """Kills: a revoke that leaves held sessions open, or a listing that still says active."""
    wallet = Wallet()
    caller = hold(wallet)
    told: list[str] = []
    caller.on_revoked(told.append)
    assert await Surface("desk").revoke("badge") == {"record": "desk-badge", "identity": "badge", "status": "revoked"}
    assert wallet.revoked == ["badge"] and told == ["badge"]
    assert Surface("desk").identities()[0]["status"] == "revoked"


async def test_an_unknown_identity_and_an_unknown_record_are_closed_refusals() -> None:
    """Kills: a lookup that falls back to another identity or another record."""
    hold(Wallet())
    with pytest.raises(IdentityError) as unknown:
        await Surface("desk").describe("nobody")
    assert str(unknown.value) == "no_credential:unknown_identity"
    with pytest.raises(IdentityError) as missing:
        Surface("desk").identities("other-record")
    assert str(missing.value) == "no_credential:no_wallet"


async def test_two_records_listing_one_id_need_the_record_named() -> None:
    """Kills: an ambiguous id silently resolved to the first record."""
    hold(Wallet(), record="one")
    hold(Wallet(), record="two")
    with pytest.raises(IdentityError) as unclear:
        await Surface("desk").describe("badge")
    assert str(unclear.value) == "invalid:ambiguous_identity"
    assert (await Surface("desk").describe("badge", "two"))["record"] == "two"


def test_an_agent_sees_only_its_own_wallets_and_a_shared_one_serves_every_agent() -> None:
    """Kills: one agent listing another agent's identities; a shared wallet that nobody finds."""
    hold(Wallet(), agent="a", record="mine")
    hold(NoIdentity(), agent="", record="shared")
    assert [i["record"] for i in Surface("a").identities()] == ["mine"]
    assert Surface("b").identities() == []  # a shared wallet is read by the gate, never managed by the model's tools


# --- the plugin -----------------------------------------------------------------------------------


def test_the_plugin_advertises_four_closed_tools_with_governance_hints() -> None:
    """Kills: a tool that accepts any argument, or a revoke that is not marked destructive."""
    plugin = IdentityPlugin()
    names = [t.name for t in plugin.tools()]
    assert names == ["identity_list", "identity_describe", "identity_delegate", "identity_revoke"]
    for definition in DEFINITIONS:
        assert definition.parameters["additionalProperties"] is False
    hints = {d.name: (d.is_read_only, d.is_destructive) for d in DEFINITIONS}
    assert hints["identity_list"] == (True, None) and hints["identity_describe"] == (True, None)
    assert hints["identity_delegate"] == (False, False) and hints["identity_revoke"] == (False, True)


def test_the_plugin_is_off_unless_named_and_a_setting_limits_its_tools() -> None:
    """Kills: an identity surface in an agent that never asked for one; a `tools` setting that is ignored."""
    assert load_system_plugins({}, session=SessionContext(agent_id="desk")).tool_names() == frozenset()
    named = load_system_plugins({"system_plugins": ["identity"]}, session=SessionContext(agent_id="desk"))
    assert named.tool_names() == {d.name for d in DEFINITIONS}
    narrow = load_system_plugins(
        {"system_plugins": [{"identity": {"tools": ["identity_list", "identity_describe"]}}]},
        session=SessionContext(agent_id="desk"),
    )
    assert narrow.tool_names() == {"identity_list", "identity_describe"}


@pytest.mark.parametrize(
    "settings", [{"colour": "red"}, {"tools": []}, {"tools": ["identity_list", "nope"]}, {"tools": "x"}]
)
def test_a_bad_setting_is_a_load_error(settings: dict[str, Any]) -> None:
    """Kills: a typo in the plugin's settings that turns the whole surface on, or off, silently."""
    with pytest.raises(ManifestToolLoadError):
        IdentityPlugin(**settings)


async def test_a_tool_outside_the_named_ones_is_not_claimed() -> None:
    """Kills: a plugin limited to read tools that still runs a revoke when asked for it by name."""
    plugin, state = plugin_for(tools=["identity_list"])
    with pytest.raises(UnknownSystemToolError):
        await call(plugin, state, "identity_revoke", identity="badge")


async def test_the_tools_run_the_surface_and_return_json_without_a_value(events: list[dict[str, Any]]) -> None:
    """Kills: a tool that returns anything but the value-free listing; an event that carries a value."""
    wallet = Wallet()
    hold(wallet)
    plugin, state = plugin_for()
    listed = json.loads(await call(plugin, state, "identity_list"))
    assert [i["id"] for i in listed] == ["badge"]
    described = json.loads(await call(plugin, state, "identity_describe", identity="badge"))
    assert described["facts"]["grade"] == "gold"
    permits.grant("", "desk", "identity_delegate", 7)
    child = json.loads(await call(plugin, state, "identity_delegate", identity="badge", scopes=["read"]))
    assert child["id"] == "badge-d1" and child["grant"]["depth"] == 1
    permits.grant("", "desk", "identity_revoke", 7)
    revoked = json.loads(await call(plugin, state, "identity_revoke", identity="badge-d1"))
    assert revoked["status"] == "revoked"
    for event in events:
        observe.validate_event(event)
    no_secret(listed, described, child, revoked, events)
    assert wallet.requests == []  # nothing was ever presented: the tools never ask the wallet for evidence


async def test_a_refusal_is_a_code_and_never_a_value() -> None:
    """Kills: an error text that echoes the model's input, the wallet's message or a cause."""
    hold(Wallet(lie=True))
    plugin, state = plugin_for()
    permits.grant("", "desk", "identity_delegate", 7)
    assert await call(plugin, state, "identity_delegate", identity="badge", scopes=["read"]) == (
        "identity: refused:delegation_widens"
    )
    assert (
        await call(plugin, state, "identity_describe", identity=f"{SECRET}")
        == "identity: no_credential:unknown_identity"
    )


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("identity_list", {"reveal": True}),
        ("identity_describe", {"identity": "badge", "token": True}),
        ("identity_describe", {"identity": ""}),
        ("identity_describe", {"identity": 7}),
        ("identity_describe", {}),
        ("identity_delegate", {"identity": "badge", "scopes": []}),
        ("identity_delegate", {"identity": "badge", "scopes": "read"}),
        ("identity_delegate", {"identity": "badge", "scopes": [1]}),
        ("identity_delegate", {"identity": "badge", "scopes": ["read"], "ttl_s": -1}),
        ("identity_delegate", {"identity": "badge", "scopes": ["read"], "ttl_s": True}),
        ("identity_delegate", {"identity": "badge", "scopes": ["read"], "depth": 0}),
        ("identity_revoke", {"identity": "badge", "record": ["x"]}),
    ],
)
async def test_a_bad_argument_is_refused_and_nothing_runs(tool: str, args: dict[str, Any]) -> None:
    """Kills: an argument the schema forbids that still reaches the wallet (a prompt that adds `token` or `depth`)."""
    wallet = Wallet()
    hold(wallet)
    plugin, state = plugin_for()
    assert (await plugin.acall(tool, args, state=state, context=CallContext())).startswith("identity: invalid:")
    assert wallet.derived == [] and wallet.revoked == []


async def test_a_prompt_that_asks_for_a_token_gets_no_tool_that_returns_one() -> None:
    """Kills: any path from the four tools to evidence, headers or the view's attestation."""
    wallet = Wallet()
    hold(wallet)
    plugin, state = plugin_for()
    seen: list[str] = []
    for tool, args in [
        ("identity_list", {}),
        ("identity_describe", {"identity": "badge"}),
        ("identity_describe", {"identity": "badge", "record": "desk-badge"}),
        ("identity_delegate", {"identity": "badge", "scopes": ["read"]}),
        ("identity_list", {"reveal": True}),
        ("identity_describe", {"identity": "badge", "include": "attestation"}),
    ]:
        seen.append(await plugin.acall(tool, args, state=state, context=CallContext()))
    no_secret(seen)
    assert wallet.requests == [] and wallet.handshake_contexts == []


async def test_two_sessions_are_two_states_each_over_its_own_agent() -> None:
    """Kills: a plugin state shared between sessions, so one agent reads or changes another's identities."""
    hold(Wallet(), agent="a", record="mine")
    hold(NoIdentity(), agent="b", record="theirs")
    plugin_a, state_a = plugin_for("a")
    plugin_b, state_b = plugin_for("b")
    assert state_a is not state_b
    assert [i["record"] for i in json.loads(await call(plugin_a, state_a, "identity_list"))] == ["mine"]
    assert [i["record"] for i in json.loads(await call(plugin_b, state_b, "identity_list"))] == ["theirs"]
    permits.grant("", "b", "identity_revoke", 7)
    assert (await call(plugin_b, state_b, "identity_revoke", identity="badge")).startswith("identity: no_credential")


def test_a_restore_returns_the_bound_state_and_needs_a_bind_first() -> None:
    """Kills: a restore that invents a state, or one that raises after a checkpoint of a stateless plugin."""
    plugin = IdentityPlugin()
    with pytest.raises(RuntimeError):
        plugin.restore({})
    state = plugin.bind(SessionContext(agent_id="desk"))
    assert plugin.snapshot(state) == {} and plugin.restore({}) is state


def test_the_plugin_is_a_registered_system_plugin_named_identity() -> None:
    """Kills: a library row that does not resolve, or one that turns the plugin on by default."""
    from mas.runtime.registry import get_registry

    rows = get_registry().entries_named("identity", "system_plugin")
    assert len(rows) == 1 and rows[0].attributes["default_enabled"] is False
    assert rows[0].resolve().load_class() is IdentityPlugin


def test_the_surface_reprs_hold_no_secret() -> None:
    """Kills: a repr that prints the wallet it reaches."""
    hold(Wallet())
    plugin, state = plugin_for()
    for thing in (Surface("desk"), plugin, state, Caller("r", Wallet())):
        assert SECRET not in repr(thing)


def test_a_present_request_type_is_not_needed_by_the_surface() -> None:
    """Kills: a surface that builds a PresentRequest, which would make it a second way to ask for evidence."""
    import mas.library.standard.lib.identity.surface as module

    assert "PresentRequest" not in vars(module) and PresentRequest is not None


async def test_the_changing_tools_refuse_to_run_when_no_guard_decided_about_the_call() -> None:
    """Kills: wallet tools that are open whenever no identity-guard is configured (the default is shut, in the tool)."""
    wallet = Wallet()
    hold(wallet)
    plugin, state = plugin_for()
    for tool, args in (
        ("identity_delegate", {"identity": "badge", "scopes": ["read"]}),
        ("identity_revoke", {"identity": "badge"}),
    ):
        assert await call(plugin, state, tool, **args) == "identity: refused:no_guard"
    assert wallet.delegations == [] and not wallet._is_revoked
    permits.grant("", "other-agent", "identity_revoke", 7)  # a permit of another agent opens nothing here
    assert await call(plugin, state, "identity_revoke", identity="badge") == "identity: refused:no_guard"
    permits.grant("", "desk", "identity_revoke", 7)
    assert json.loads(await call(plugin, state, "identity_revoke", identity="badge"))["status"] == "revoked"
    assert (
        await call(plugin, state, "identity_revoke", identity="badge") == "identity: refused:no_guard"
    )  # one permit, one call

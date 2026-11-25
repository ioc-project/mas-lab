# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""The identity guard: the wallet's own tools are shut until a policy opens them; each decision is a closed event."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.requirements import GateConfigError
from mas.library.standard.plugins.identity.guard import DEFAULT_PROTECT, KNOWN_TOOLS, IdentityGuard
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observe, "publish_event", seen.append)
    monkeypatch.setattr(observe, "emit_to_channel", lambda event: None)
    return seen


def decide(guard: IdentityGuard, tool: str, *, op: str = "TOOL_CALL", **cfg: Any) -> tuple[GovDecision, str, str]:
    intent = EgressIntentView(op=op, destructive=False, correlation_id=7, tool_name=tool)
    return guard.evaluate_egress(intent, config=KernelConfig(agent_id="desk", **cfg))


@pytest.mark.parametrize("tool", DEFAULT_PROTECT)
def test_the_wallets_changing_tools_are_blocked_by_default(tool: str) -> None:
    """Kills: a guard that passes a delegation or a revocation nobody allowed."""
    decision, _plugin, reason = decide(IdentityGuard(), tool)
    assert (decision, reason) == (GovDecision.BLOCK, "guard_denied")


@pytest.mark.parametrize("tool", ["identity_list", "identity_describe", "get_fares"])
def test_a_tool_that_changes_nothing_passes(tool: str) -> None:
    """Kills: a guard that blocks reads, or every tool it does not know."""
    assert decide(IdentityGuard(), tool)[0] is GovDecision.ALLOW


def test_a_tool_can_be_added_to_the_protected_set() -> None:
    """Kills: a `protect` list that is ignored."""
    guard = IdentityGuard(protect=["identity_describe"])
    assert decide(guard, "identity_describe")[0] is GovDecision.BLOCK
    assert decide(guard, "identity_revoke")[0] is GovDecision.ALLOW  # no longer covered


def test_a_policy_opens_one_tool_and_only_that_one() -> None:
    """Kills: an allow that opens every protected tool, or opens none."""
    guard = IdentityGuard(allow={"identity_delegate": True})
    assert decide(guard, "identity_delegate") == (GovDecision.ALLOW, "gov_identity_guard", "guard_allowed")
    assert decide(guard, "identity_revoke")[0] is GovDecision.BLOCK


def test_a_hold_asks_a_human_when_there_is_one_and_blocks_when_there_is_none() -> None:
    """Kills: a hold that allows the call when no human-in-the-loop is configured."""
    guard = IdentityGuard(allow={"identity_revoke": "hold"})
    assert decide(guard, "identity_revoke", hitl_on_tool=True)[::2] == (GovDecision.HITL, "guard_hold")
    assert decide(guard, "identity_revoke")[::2] == (GovDecision.BLOCK, "guard_hold_unavailable")


def test_what_is_not_a_tool_call_passes() -> None:
    """Kills: a guard that blocks a model call because a protected name is in it."""
    assert decide(IdentityGuard(), "identity_revoke", op="LLM_CALL")[0] is GovDecision.ALLOW


def test_a_guard_that_cannot_decide_blocks() -> None:
    """Kills: a failure that turns into an allow."""
    guard = IdentityGuard()
    assert guard.evaluate_egress(object(), config=KernelConfig(agent_id="desk"))[::2] == (  # type: ignore[arg-type]
        GovDecision.BLOCK,
        "gate_error",
    )


@pytest.mark.parametrize(
    "config",
    [
        {"allow": {"nope": True}},
        {"allow": {"identity_delegate": "yes"}},
        {"allow": {"identity_delegate": False}},
        {"protect": ["nope"]},
        {"protect": "identity_revoke"},
    ],
)
def test_a_bad_policy_is_a_config_error(config: dict[str, Any]) -> None:
    """Kills: a typo in the policy that leaves a tool open or shut without a word."""
    with pytest.raises(GateConfigError):
        IdentityGuard(**config)


def test_every_decision_about_a_covered_tool_is_one_closed_value_free_event(events: list[dict[str, Any]]) -> None:
    """Kills: a decision with no event, or an event outside the rulebook."""
    guard = IdentityGuard(allow={"identity_delegate": "hold"})
    decide(guard, "identity_delegate", hitl_on_tool=True)
    decide(guard, "identity_revoke")
    decide(guard, "identity_list")  # not covered: no event
    assert [(e["kind"], e["outcome"], e["code"], e["operation"], e["call"]) for e in events] == [
        ("identity.gate", "hold", "guard_hold", "tools/call:identity_delegate", "7"),
        ("identity.gate", "denied", "guard_denied", "tools/call:identity_revoke", "7"),
    ]
    for event in events:
        observe.validate_event(event)
        assert event["actor"] == "desk"


def test_the_guard_is_a_registered_governance_plugin_under_two_names() -> None:
    """Kills: a library row that does not resolve under its shortcut."""
    from mas.runtime.registry import get_registry

    for name in ("identity-guard", "identity_guard"):
        rows = get_registry().entries_named(name, "governance")
        assert len(rows) == 1 and rows[0].resolve().load_class() is IdentityGuard
    assert set(DEFAULT_PROTECT) <= set(KNOWN_TOOLS)


def test_only_the_call_the_guard_let_through_leaves_a_permit_and_only_for_that_call() -> None:
    """Kills: a permit left for a denied or held call, one that opens another call, or one usable twice."""
    from mas.library.standard.lib.identity import permits

    permits.reset()
    decide(IdentityGuard(), "identity_revoke")  # denied: no permit
    assert not permits.take("", "desk", "identity_revoke", 7)
    decide(IdentityGuard(allow={"identity_revoke": True}), "identity_revoke")  # correlation id 7
    assert not permits.take("", "desk", "identity_revoke", 8)  # another call of the same tool
    assert permits.take("", "desk", "identity_revoke", 7) and not permits.take("", "desk", "identity_revoke", 7)
    permits.reset()


def test_a_hold_leaves_a_permit_only_once_a_human_approved_that_call(events: list[dict[str, Any]]) -> None:
    """Kills: a permit for a hold nobody answered; a hold published twice and never as an ok; a denied hold's permit."""
    from mas.library.standard.lib.identity import permits

    permits.reset()
    guard = IdentityGuard(allow={"identity_delegate": "hold"})
    first = decide(guard, "identity_delegate", hitl_on_tool=True)
    assert first[0] is GovDecision.HITL and not permits.take("", "desk", "identity_delegate", 7)
    again = decide(guard, "identity_delegate", hitl_on_tool=True)  # the kernel asks again once the human said yes
    assert (again[0], again[2]) == (GovDecision.ALLOW, "guard_hold_approved")
    assert permits.take("", "desk", "identity_delegate", 7)
    assert [(e["outcome"], e["code"]) for e in events] == [("hold", "guard_hold"), ("ok", "guard_hold_approved")]
    denied = IdentityGuard(allow={"identity_delegate": "hold"})
    assert decide(denied, "identity_delegate", hitl_on_tool=True)[0] is GovDecision.HITL  # asked, and never approved
    assert not permits.take("", "desk", "identity_delegate", 7)
    permits.reset()


def test_a_hold_is_blocked_when_a_humans_yes_is_remembered_for_the_turn() -> None:
    """Kills: `hitl_once_per_turn` turning a guarded delegation into a silent allow after any earlier approval."""
    guard = IdentityGuard(allow={"identity_delegate": "hold"})
    decision = decide(guard, "identity_delegate", hitl_on_tool=True, hitl_once_per_turn=True)
    assert (decision[0], decision[2]) == (GovDecision.BLOCK, "guard_hold_unavailable")

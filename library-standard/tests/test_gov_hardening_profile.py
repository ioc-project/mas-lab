#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``gov_hardening_profile``: record the report once, block a tool call to an unlisted host."""

from __future__ import annotations

import pytest
from mas.library.standard.plugins.governance.hardening_profile import (
    HardeningProfilePlugin,
    host_allowed,
    hosts_in,
)
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

CONFIG = KernelConfig()


def _tool(args: dict | None, op: str = "TOOL_CALL") -> EgressIntentView:
    return EgressIntentView(op=op, destructive=False, correlation_id=1, tool_name="fetch", tool_arguments=args)


def _plugin(**kw) -> HardeningProfilePlugin:
    plugin = HardeningProfilePlugin(**kw)
    plugin.evaluate_egress(_tool(None), config=CONFIG)  # the one-time record
    return plugin


def test_first_egress_records_the_report_once() -> None:
    # kills: no record at session start, or a record on every call
    report = [
        {"check": "secret-store-env", "outcome": "passed"},
        {"check": "plain-http-remote", "outcome": "waived", "waiver_reason": "cluster"},
        {"check": "hitl-auto-approve", "outcome": "failed"},
    ]
    plugin = HardeningProfilePlugin(allowed_hosts=["a.example.com"], report=report)
    decision, name, reason = plugin.evaluate_egress(_tool(None), config=CONFIG)
    assert (decision, name) == (GovDecision.LOG, "gov_hardening_profile")
    assert (
        "failed hitl-auto-approve" in reason
        and "waived plain-http-remote" in reason
        and "passed secret-store-env" in reason
    )
    assert plugin.evaluate_egress(_tool(None), config=CONFIG)[0] == GovDecision.ALLOW


def test_production_blocks_an_unlisted_host_and_allows_a_listed_one() -> None:
    # kills: never blocking; blocking a listed host; an allow-list read as a substring match
    plugin = _plugin(allowed_hosts=["api.example.com", "*.internal.example.com"])
    assert plugin.evaluate_egress(_tool({"url": "https://api.example.com/x"}), config=CONFIG)[0] == GovDecision.ALLOW
    assert (
        plugin.evaluate_egress(_tool({"a": ["http://svc.internal.example.com"]}), config=CONFIG)[0] == GovDecision.ALLOW
    )
    for bad in (
        "https://evil.test/x",
        "https://api.example.com.evil.test/",
        "https://api.example.com@evil.test/",
        "https://internal.example.com.evil.test/",
        "https://example.com/",
    ):
        decision, _, reason = plugin.evaluate_egress(_tool({"nested": {"u": bad}}), config=CONFIG)
        assert decision == GovDecision.BLOCK, bad
        assert "evil" not in reason  # the reason never repeats the url


def test_no_allow_list_judges_no_host_and_the_default_posture_only_records() -> None:
    # kills: blocking when nothing is configured; the default posture blocking
    assert _plugin().evaluate_egress(_tool({"url": "https://evil.test"}), config=CONFIG)[0] == GovDecision.ALLOW
    soft = _plugin(profile="default", allowed_hosts=["ok.example.com"])
    assert soft.evaluate_egress(_tool({"url": "https://evil.test"}), config=CONFIG)[0] == GovDecision.LOG


def test_a_waiver_turns_the_block_into_a_recorded_allow() -> None:
    # kills: a waiver that is ignored, or one that leaves no reason in the record
    plugin = _plugin(
        allowed_hosts=["ok.example.com"], waive=[{"check": "egress-host-allowlist", "reason": "partner API"}]
    )
    decision, _, reason = plugin.evaluate_egress(_tool({"url": "https://evil.test"}), config=CONFIG)
    assert decision == GovDecision.LOG and "partner API" in reason


def test_only_tool_calls_are_judged() -> None:
    plugin = _plugin(allowed_hosts=["ok.example.com"])
    assert (
        plugin.evaluate_egress(_tool({"url": "https://evil.test"}, op="LLM_CALL"), config=CONFIG)[0]
        == GovDecision.ALLOW
    )


@pytest.mark.parametrize("bad", [[], ["*"], ["https://a.example.com"], ["a.example.com:443"], ["a/b"], [""]])
def test_an_empty_or_loose_allow_list_is_refused_at_start(bad) -> None:
    # kills: an empty list read as "no restriction" (or "block all"), a bare wildcard, a url as a host
    with pytest.raises(ValueError):
        HardeningProfilePlugin(allowed_hosts=bad)


def test_argument_walk_is_bounded() -> None:
    # kills: an unbounded walk over a hostile argument tree
    deep: dict = {"u": "https://evil.test"}
    for _ in range(50):
        deep = {"d": deep}
    assert hosts_in(deep) == []
    many = {"k": ["x"] * 1000 + ["https://late.test"]}
    assert hosts_in(many) == []
    assert hosts_in({"a": "https://Ok.Example.com:8443/p?q=1"}) == ["ok.example.com"]
    assert host_allowed("a.b.example.com", ("*.example.com",)) and not host_allowed("example.com", ("*.example.com",))


def test_unknown_profile_is_refused() -> None:
    with pytest.raises(ValueError):
        HardeningProfilePlugin(profile="strict")


def test_the_first_egress_is_judged_as_well_as_recorded() -> None:
    # kills: a tool call to an unlisted host allowed because it is the first egress seen
    plugin = HardeningProfilePlugin(allowed_hosts=["a.example.com"], report=[])
    decision, _, reason = plugin.evaluate_egress(_tool({"url": "https://evil.example.org/x"}), config=CONFIG)
    assert decision == GovDecision.BLOCK
    assert "hardening production" in reason

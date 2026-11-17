#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Peers are entries of the agent's own spec, and the running spec can take one away."""

from __future__ import annotations

import pytest
from mas.runtime.boundary.delegation.policy import (
    communication_peers,
    delegation_targets,
    openai_delegation_tools,
    unavailable_peers,
)
from mas.runtime.session.spec_revision import SpecDelta, _apply_delta

SPEC = {
    "communication": {"peers": ["billing", {"id": "outage"}, {"id": "notes", "enabled": False}]},
}


def test_an_agent_reads_its_next_hops_from_its_own_spec_not_from_the_mas_workflow() -> None:
    manifest = {"metadata": {"name": "triage"}, "spec": {**SPEC, "workflow": {"nodes": [{"id": "triage"}]}}}
    assert delegation_targets(manifest, agent_id="triage") == ["billing", "outage", "notes"]


def test_a_spec_without_compiled_peers_has_no_peer_list() -> None:
    assert communication_peers({"spec": {}}) is None
    assert communication_peers({"spec": {"communication": {"peers": "x"}}}) is None


def test_a_peer_is_declared_whether_or_not_it_is_available() -> None:
    assert unavailable_peers(SPEC) == frozenset({"notes"})
    tools = openai_delegation_tools({"metadata": {"name": "triage"}, "spec": SPEC}, agent_id="triage")
    assert [t["function"]["name"] for t in tools] == ["delegate_to_billing", "delegate_to_outage"]


def test_disable_and_enable_peer_edit_the_running_spec() -> None:
    spec = {"communication": {"peers": ["billing"]}}
    _apply_delta(spec, SpecDelta("disable_peer", {"name": "billing", "by": "circuit_breaker"}))
    assert unavailable_peers(spec) == frozenset({"billing"})
    assert spec["communication"]["peers"][0]["disabled_by"] == "circuit_breaker"
    _apply_delta(spec, SpecDelta("enable_peer", {"name": "billing"}))
    assert unavailable_peers(spec) == frozenset()
    assert spec["communication"]["peers"] == [{"id": "billing"}]


def test_a_peer_that_was_never_declared_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="not declared"):
        _apply_delta({"communication": {"peers": ["billing"]}}, SpecDelta("disable_peer", {"name": "stranger"}))

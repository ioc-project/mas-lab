#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for delegation policy capability-based contract.

Delegation policy follows a capability + topology contract:
- behavior.delegation_style determines if delegation tools are exposed
- workflow.nodes[].delegates_to determines the peer targets
"""

from mas.runtime.boundary.delegation.policy import delegation_targets, uses_llm_peer_delegation


def _manifest(delegation_style: str = "typed") -> dict:
    """Build test manifest with configurable delegation style."""
    return {
        "metadata": {"name": "moderator"},
        "spec": {
            "behavior": {"delegation_style": delegation_style},
            "workflow": {
                "entry": "moderator",
                "nodes": [
                    {"id": "moderator", "delegates_to": ["a", "b"]},
                    {"id": "a"},
                    {"id": "b"},
                ],
            },
        },
    }


def test_typed_delegation_enabled_from_topology():
    """Delegation works from topology when delegation_style=typed."""
    m = _manifest(delegation_style="typed")
    assert uses_llm_peer_delegation(m) is True
    assert delegation_targets(m, agent_id="moderator") == ["a", "b"]


def test_non_typed_delegation_style_disables_peer_tools():
    """Non-typed delegation styles hide the tools; the topology (next hop, send wiring) stays."""
    from mas.runtime.boundary.delegation.policy import advertise_delegate_tools, openai_delegation_tools

    m = _manifest(delegation_style="none")
    assert uses_llm_peer_delegation(m) is False
    assert advertise_delegate_tools(m) is False
    assert openai_delegation_tools(m, agent_id="moderator") == []
    assert delegation_targets(m, agent_id="moderator") != []


def test_delegation_targets_returns_empty_for_agent_without_delegates_to():
    """Agents without delegates_to field get no delegation targets."""
    m = _manifest()
    assert delegation_targets(m, agent_id="a") == []
    assert delegation_targets(m, agent_id="b") == []


def test_workflow_entry_does_not_choose_this_agents_leftover_tools():
    m = {
        "metadata": {"name": "flights"},
        "spec": {
            "workflow": {
                "entry": "planner",
                "nodes": [
                    {"id": "planner", "delegates_to": ["flights", "hotels"]},
                    {"id": "flights", "delegates_to": ["hotels"]},
                    {"id": "hotels"},
                ],
            }
        },
    }
    assert delegation_targets(m) == ["hotels"]
    assert delegation_targets(m, agent_id="planner") == ["flights", "hotels"]
    assert delegation_targets(m, agent_id="flights") == ["hotels"]


def test_empty_delegates_to_offers_no_leftover_tools():
    m = {
        "spec": {
            "workflow": {
                "entry": "mod",
                "nodes": [{"id": "mod"}, {"id": "a"}, {"id": "b"}],
                "participants": ["a", "b"],
            }
        }
    }
    assert delegation_targets(m, agent_id="mod") == []
    assert delegation_targets(m, agent_id="a") == []
    assert delegation_targets(m, agent_id="ghost") == []


def test_helper_node_agent_field_is_this_agent():
    m = {
        "spec": {
            "workflow": {
                "entry": "mod",
                "nodes": [
                    {"id": "mod"},
                    {"id": "n1", "agent": "flights", "delegates_to": ["hotels"]},
                    {"id": "hotels"},
                ],
            }
        }
    }
    assert delegation_targets(m, agent_id="flights") == ["hotels"]
    assert delegation_targets(m, agent_id="n1") == ["hotels"]
    assert delegation_targets(m, agent_id="hotels") == []


def test_delegation_targets_handles_missing_workflow_gracefully():
    """Missing workflow section returns empty targets."""
    m = {"metadata": {"name": "agent"}, "spec": {"behavior": {"delegation_style": "typed"}}}
    assert delegation_targets(m) == []


def test_delegation_style_defaults_to_typed_when_missing():
    """Default delegation_style is 'typed' when not specified."""
    m = {"metadata": {"name": "agent"}, "spec": {}}
    assert uses_llm_peer_delegation(m) is True


def test_delegation_targets_for_specific_agent_in_multi_agent_workflow():
    """Each agent gets only its own delegates_to targets."""
    m = {
        "spec": {
            "behavior": {"delegation_style": "typed"},
            "workflow": {
                "nodes": [
                    {"id": "orchestrator", "delegates_to": ["worker1", "worker2"]},
                    {"id": "worker1", "delegates_to": ["tool"]},
                    {"id": "worker2"},
                    {"id": "tool"},
                ],
            },
        },
    }
    assert delegation_targets(m, agent_id="orchestrator") == ["worker1", "worker2"]
    assert delegation_targets(m, agent_id="worker1") == ["tool"]
    assert delegation_targets(m, agent_id="worker2") == []
    assert delegation_targets(m, agent_id="tool") == []

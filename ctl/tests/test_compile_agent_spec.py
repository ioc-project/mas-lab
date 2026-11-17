#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl turns the MAS manifest into each agent's own next hops."""

from __future__ import annotations

from mas.ctl.manifest.mas_agent_merge import compile_agent_spec, enrich_entry_agent_for_delegation

WORKFLOW = {
    "entry": "triage",
    "nodes": [
        {"id": "triage", "role": "moderator", "delegates_to": ["billing", "outage"]},
        {"id": "billing", "role": "specialist"},
        {"id": "outage"},
    ],
}


def test_an_agent_gets_its_next_hops() -> None:
    manifest = {"metadata": {"name": "triage"}, "spec": {}}
    compile_agent_spec(manifest, WORKFLOW)
    assert manifest["spec"]["communication"]["peers"] == [{"id": "billing"}, {"id": "outage"}]
    assert "collaboration" not in manifest["spec"]


def test_a_leaf_agent_has_no_peers() -> None:
    manifest = {"metadata": {"name": "billing"}, "spec": {}}
    compile_agent_spec(manifest, WORKFLOW)
    assert manifest["spec"]["communication"]["peers"] == []


def test_peers_an_agent_already_declares_are_kept() -> None:
    manifest = {"metadata": {"name": "triage"}, "spec": {"communication": {"peers": [{"id": "only"}]}}}
    compile_agent_spec(manifest, WORKFLOW)
    assert manifest["spec"]["communication"]["peers"] == [{"id": "only"}]


def test_no_identity_leaves_the_manifest_as_it_was() -> None:
    manifest = {"spec": {}}
    compile_agent_spec(manifest, WORKFLOW)
    assert manifest == {"spec": {}}


def test_enriching_an_agent_compiles_it_from_the_mas_workflow() -> None:
    out = enrich_entry_agent_for_delegation(
        {"metadata": {"name": "x"}, "spec": {}}, {"spec": {"workflow": WORKFLOW}}, agent_id="triage"
    )
    assert [p["id"] for p in out["spec"]["communication"]["peers"]] == ["billing", "outage"]


def test_a_compiled_agent_still_validates_against_the_agent_schema() -> None:
    from mas.ctl.validate import validate_data

    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "triage"},
        "spec": {"description": "Triage the request."},
    }
    assert validate_data(manifest, kind="agent").ok
    compile_agent_spec(manifest, WORKFLOW)
    result = validate_data(manifest, kind="agent")
    assert result.ok, getattr(result, "errors", result)


def test_workflow_peers_reads_only_the_node_of_that_agent() -> None:
    from mas.ctl.infra.identities import workflow_peers

    mas = {
        "spec": {
            "workflow": {
                "entry": "planner",
                "nodes": [
                    {"id": "planner", "delegates_to": ["flights", "hotels", "planner"]},
                    {"agent": "flights", "id": "n2", "delegates_to": ["hotels", "hotels"]},
                    {"id": "hotels", "delegates_to": []},
                    {"id": "idle"},
                ],
            }
        }
    }
    assert workflow_peers(mas, "planner") == ["flights", "hotels"]  # never itself
    assert workflow_peers(mas, "flights") == ["hotels"]  # found by its agent name, no duplicates
    assert workflow_peers(mas, "n2") == ["hotels"]  # or by the node id
    assert workflow_peers(mas, "hotels") == [] and workflow_peers(mas, "idle") == []
    assert workflow_peers(mas, "stranger") == [] and workflow_peers(mas, "") == []
    assert workflow_peers(None, "planner") == []


def test_the_runtime_never_reads_the_mas_workflow() -> None:
    from mas.runtime.boundary.delegation.policy import delegation_targets

    manifest = {
        "metadata": {"name": "planner"},
        "spec": {"workflow": {"nodes": [{"id": "planner", "delegates_to": ["flights"]}]}},
    }
    assert delegation_targets(manifest, agent_id="planner") == []  # only spec.communication.peers counts

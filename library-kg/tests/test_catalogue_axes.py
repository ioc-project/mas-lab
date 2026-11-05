# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for Paper 3 §3.2 silent-failure catalogue axes."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from mas.library.kg.core.ontology_align import CONTAINMENT_EDGE_TYPES
from mas.library.kg.pipeline import build_kg_document, load_events_jsonl

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "events_full_ontology.jsonl"


@pytest.fixture(scope="module")
def full_kg() -> dict:
    if not _FIXTURE.exists():
        pytest.skip(f"fixture missing: {_FIXTURE}")
    events = load_events_jsonl(_FIXTURE)
    return build_kg_document(
        events,
        run_id="full-ontology-test",
        include_provenance=True,
        include_governance=True,
        include_annotation=True,
        include_trajectory=True,
        include_native_extensions=True,
        all_attributes=True,
    )


def _edge_types(kg: dict) -> set[str]:
    return {e["edge_type"] for e in kg.get("edges", [])}


def _node_types(kg: dict) -> set[str]:
    return {n["node_type"] for n in kg.get("nodes", [])}


class TestCatalogueProvenance:
    def test_context_contribution_nodes_exist(self, full_kg: dict):
        assert "ContextContribution" in _node_types(full_kg)

    def test_provenance_links_exist(self, full_kg: dict):
        edges = _edge_types(full_kg)
        assert "contributesTo" in edges or "derivedFrom" in edges


class TestCatalogueGovernance:
    def test_governance_annotations_exist(self, full_kg: dict):
        anns = [n for n in full_kg["nodes"] if n.get("node_type") == "GovernanceEvent"]
        assert anns, "expected GovernanceEvent nodes from governance_checked events"

    def test_delegation_containment(self, full_kg: dict):
        # canonicalize_edge_type renames the generic "contains"/"hasCall"
        # edge into a specific ontology predicate (hasMASCall for a MASCall
        # target) -- CONTAINMENT_EDGE_TYPES is the canonical set of names
        # that relationship can come back as.
        child_node = next(n for n in full_kg["nodes"] if n.get("callId") == "mas-f02")
        contains = [
            e
            for e in full_kg["edges"]
            if e.get("edge_type") in CONTAINMENT_EDGE_TYPES and e.get("to_id") == child_node["id"]
        ]
        assert contains, "expected orchestrator → child MASCall containment"

    def test_multiple_agent_calls(self, full_kg: dict):
        agents = [n for n in full_kg["nodes"] if n.get("node_type") == "AgentCall"]
        assert len(agents) >= 2


class TestCatalogueHierarchical:
    def test_contains_edges_exist(self, full_kg: dict):
        contains = _edge_types(full_kg) & CONTAINMENT_EDGE_TYPES
        assert contains

    def test_mas_and_agent_call_nodes(self, full_kg: dict):
        types = _node_types(full_kg)
        assert "MASCall" in types
        assert "AgentCall" in types


class TestCatalogueStructural:
    def test_tool_call_nodes_exist(self, full_kg: dict):
        assert "ToolCall" in _node_types(full_kg)


class TestCatalogueCoverage:
    def test_fixture_kinds_present(self):
        if not _FIXTURE.exists():
            pytest.skip(f"fixture missing: {_FIXTURE}")
        kinds = {json.loads(line)["kind"] for line in _FIXTURE.read_text().splitlines()}
        assert {"llm_call_start", "tool_call_start", "context_part_contributed"} <= kinds

    def test_rich_node_type_coverage(self, full_kg: dict):
        assert len(Counter(n["node_type"] for n in full_kg["nodes"])) >= 10

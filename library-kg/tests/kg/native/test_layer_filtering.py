"""Tests for layer on/off filtering in normalize_events.

Each test verifies that a specific layer's event kinds are included or excluded
from the normalized output based on the boolean layer parameter.

Note: These tests use a mock ontology index so they do not require rdflib or
the mas-ontology.ttl file to be present.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

from mas.library.kg.core.graph_builder import normalize_events

_MAS_NS = "https://outshift-open.github.io/oxp-ontology/mas#"


def _make_ontology() -> Any:
    """Build a mock _OntologyIndex that returns metadata for all known classes."""
    ontology = MagicMock()

    def _get(local_name: str):
        if local_name in ("ExecutionElement", None):
            return None
        return {
            "uri": _MAS_NS + local_name,
            "local_name": local_name,
            "label": local_name,
            "span_level": "call",
            "icon": "",
        }

    ontology.get.side_effect = _get
    return ontology


def _event(kind: str, agent_id: str = "agent-1", call_id: str = "call-1") -> Dict[str, Any]:
    """Build a minimal event dict."""
    return {
        "kind": kind,
        "agent_id": agent_id,
        "call_id": call_id,
        "timestamp": 0.0,
        "run_id": "test-run",
    }


def _kinds(normalized: List[Dict[str, Any]]) -> List[str]:
    return [e["kind"] for e in normalized]


ONTOLOGY = _make_ontology()


# ---------------------------------------------------------------------------
# Governance layer
# ---------------------------------------------------------------------------


def test_governance_off_by_default() -> None:
    """governance layer is off by default — gov events are excluded."""
    events = [
        _event("execution_start"),
        _event("execution_end"),
        _event("governance_checked"),
        _event("audit"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1")
    kinds = _kinds(result)
    assert "governance_checked" not in kinds
    assert "audit" not in kinds
    assert "execution_start" in kinds


def test_governance_on_includes_gov_events() -> None:
    """governance=True includes governance event kinds."""
    events = [
        _event("execution_start"),
        _event("governance_checked"),
        _event("policy_denial"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1", include_governance=True)
    kinds = _kinds(result)
    assert "governance_checked" in kinds
    assert "policy_denial" in kinds


# ---------------------------------------------------------------------------
# Infrastructure layer
# ---------------------------------------------------------------------------


def test_infrastructure_off_by_default() -> None:
    """infrastructure layer is off by default — Worker events are excluded."""
    events = [
        _event("execution_start"),
        _event("infrastructure_info"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1")
    kinds = _kinds(result)
    assert "infrastructure_info" not in kinds


def test_infrastructure_on_includes_worker_events() -> None:
    """include_infrastructure=True includes infrastructure_info events."""
    events = [
        _event("execution_start"),
        _event("infrastructure_info"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1", include_infrastructure=True)
    kinds = _kinds(result)
    assert "infrastructure_info" in kinds


# ---------------------------------------------------------------------------
# Trajectory layer
# ---------------------------------------------------------------------------


def test_trajectory_on_by_default_includes_routing() -> None:
    """trajectory layer is on by default — routing annotations are included."""
    events = [
        _event("execution_start"),
        _event("routing"),
        _event("parallel_group_start"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1")
    kinds = _kinds(result)
    assert "routing" in kinds
    assert "parallel_group_start" in kinds


def test_trajectory_false_drops_routing_annotations() -> None:
    """include_trajectory=False drops routing and parallel group annotations."""
    events = [
        _event("execution_start"),
        _event("routing"),
        _event("routing_result"),
        _event("parallel_group_start"),
        _event("parallel_group_end"),
        _event("branch_start"),
        _event("branch_end"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1", include_trajectory=False)
    kinds = _kinds(result)
    assert "routing" not in kinds
    assert "routing_result" not in kinds
    assert "parallel_group_start" not in kinds
    assert "parallel_group_end" not in kinds
    assert "branch_start" not in kinds
    assert "branch_end" not in kinds
    # Core events remain
    assert "execution_start" in kinds


# ---------------------------------------------------------------------------
# Provenance layer
# ---------------------------------------------------------------------------


def test_provenance_off_by_default() -> None:
    """provenance layer is off by default — context_part_contributed events excluded."""
    events = [
        _event("execution_start"),
        _event("context_part_contributed"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1")
    kinds = _kinds(result)
    assert "context_part_contributed" not in kinds


def test_provenance_on_includes_context_contributions() -> None:
    """include_provenance=True includes context_part_contributed events."""
    events = [
        _event("execution_start"),
        _event("context_part_contributed"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1", include_provenance=True)
    kinds = _kinds(result)
    assert "context_part_contributed" in kinds


# ---------------------------------------------------------------------------
# Annotation layer
# ---------------------------------------------------------------------------


def test_annotation_off_by_default() -> None:
    """annotation layer is off by default — CallAnnotation kinds are excluded."""
    events = [
        _event("execution_start"),
        _event("context_assembled"),
        _event("state_update_start"),
        _event("observability_pre_execute_start"),
        _event("client_response"),
        _event("tool_result_injected"),
        _event("user_input"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1")
    kinds = _kinds(result)
    assert "context_assembled" not in kinds
    assert "state_update_start" not in kinds
    assert "observability_pre_execute_start" not in kinds
    assert "client_response" not in kinds
    assert "tool_result_injected" not in kinds
    assert "user_input" not in kinds
    assert "execution_start" in kinds


def test_annotation_on_includes_call_annotation_kinds() -> None:
    """include_annotation=True includes point CallAnnotation kinds."""
    events = [
        _event("execution_start"),
        _event("context_assembled"),
        _event("state_update_end"),
        _event("wait_state_start"),
    ]
    result = normalize_events(events, ONTOLOGY, "run-1", include_annotation=True)
    kinds = _kinds(result)
    assert "context_assembled" in kinds
    assert "state_update_end" in kinds
    assert "wait_state_start" in kinds


# ---------------------------------------------------------------------------
# Structural events are always included regardless of layer flags
# ---------------------------------------------------------------------------


def test_structural_events_always_included() -> None:
    """Core structural events (execution, tool_call, llm_call) always appear."""
    events = [
        _event("execution_start"),
        _event("execution_end"),
        _event("tool_call_start"),
        _event("tool_call_end"),
        _event("llm_call_start"),
        _event("llm_call_end"),
    ]
    result = normalize_events(
        events,
        ONTOLOGY,
        "run-1",
        include_infrastructure=False,
        include_trajectory=False,
        include_provenance=False,
        include_governance=False,
        include_annotation=False,
    )
    kinds = _kinds(result)
    assert "execution_start" in kinds
    assert "execution_end" in kinds
    assert "tool_call_start" in kinds
    assert "llm_call_start" in kinds

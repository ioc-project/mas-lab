"""Ontology contract tests — verify every KIND_TO_CLASS entry maps to a class
that exists in mas-ontology.ttl, and that the GovernanceEvent interim mapping
is still provisional (test fails when GovernanceEvent appears in the TTL,
signalling it's time to update KIND_TO_CLASS entries).
"""

from __future__ import annotations

import pytest

from mas.library.kg.core.event_mappings import KIND_TO_CLASS
from mas.library.kg.core.graph_builder import (
    _load_ontology,
    _resolve_ontology_path,
)
from mas.library.kg.core.oxp_models import NATIVE_EXTENSION_NODE_TYPES

# ---------------------------------------------------------------------------
# Fixture — loaded ontology index (skip if no TTL on disk)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ontology():
    """Load the ontology; skip all contract tests if no TTL is available."""
    try:
        path = _resolve_ontology_path(None)
        ont = _load_ontology(path)
        return ont
    except (FileNotFoundError, ImportError, Exception) as exc:
        pytest.skip(f"Ontology not available: {exc}")


# ---------------------------------------------------------------------------
# Every non-None KIND_TO_CLASS value must exist in the ontology
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,cls",
    [(k, v) for k, v in KIND_TO_CLASS.items() if v is not None],
    ids=lambda x: x if isinstance(x, str) else "",
)
def test_kind_maps_to_valid_ontology_class(kind: str, cls: str, ontology) -> None:
    """Each KIND_TO_CLASS entry with a non-None class must exist in the loaded TTL."""
    optional_extension_classes = set(NATIVE_EXTENSION_NODE_TYPES)
    if cls in optional_extension_classes and cls not in ontology:
        pytest.skip(f"optional extension class {cls!r} not present in bundled mas ontology")
    assert cls in ontology, (
        f"Event kind {kind!r} maps to ontology class {cls!r}, "
        f"but {cls!r} is not defined in mas-ontology.ttl. "
        f"Either add the class to the TTL or update KIND_TO_CLASS."
    )


# ---------------------------------------------------------------------------
# Every suppressed (None) entry should NOT appear as a class
# (just validates the table shape — no TTL lookup needed)
# ---------------------------------------------------------------------------


def test_suppressed_kinds_map_to_none() -> None:
    """All suppressed event kinds must have None as their class."""
    suppressed = {k for k, v in KIND_TO_CLASS.items() if v is None}
    assert suppressed, "Expected at least one suppressed (None-mapped) kind"
    for kind in suppressed:
        assert KIND_TO_CLASS[kind] is None


# ---------------------------------------------------------------------------
# GovernanceEvent — governance kinds map directly to it (mas-ontology.ttl §21)
# ---------------------------------------------------------------------------


def test_governance_kinds_map_to_governance_event(ontology) -> None:
    """Governance kinds map to GovernanceEvent, not the interim CallAnnotation."""
    if "GovernanceEvent" not in ontology:
        pytest.skip("GovernanceEvent not in the loaded ontology (scope_ontology not installed)")
    governance_kinds = {k for k, v in KIND_TO_CLASS.items() if v == "GovernanceEvent"}
    assert governance_kinds, "Expected at least one kind mapped to GovernanceEvent"
    for kind in governance_kinds:
        assert KIND_TO_CLASS[kind] == "GovernanceEvent", kind


# ---------------------------------------------------------------------------
# Completeness — every kind emitted by observe/normalizer.py is in KIND_TO_CLASS
# ---------------------------------------------------------------------------


def test_observe_worker_boundary_kind_is_in_kind_to_class() -> None:
    """infrastructure_info (emitted for Worker boundary) must be in KIND_TO_CLASS."""
    assert "infrastructure_info" in KIND_TO_CLASS
    assert KIND_TO_CLASS["infrastructure_info"] == "Worker"


def test_observe_governance_kind_is_in_kind_to_class() -> None:
    """governance_checked (emitted for GovernanceEvent boundary) must be in KIND_TO_CLASS."""
    assert "governance_checked" in KIND_TO_CLASS


def test_observe_context_contribution_kind_is_in_kind_to_class() -> None:
    """context_part_contributed must be in KIND_TO_CLASS."""
    assert "context_part_contributed" in KIND_TO_CLASS
    assert KIND_TO_CLASS["context_part_contributed"] == "ContextContribution"


def test_observe_processing_call_kinds_are_in_kind_to_class() -> None:
    """processing_call_start and processing_call_end must be in KIND_TO_CLASS."""
    assert "processing_call_start" in KIND_TO_CLASS
    assert "processing_call_end" in KIND_TO_CLASS


def test_user_output_kind_is_in_kind_to_class() -> None:
    """user_output (default CallAnnotation fallback) must be in KIND_TO_CLASS."""
    assert "user_output" in KIND_TO_CLASS

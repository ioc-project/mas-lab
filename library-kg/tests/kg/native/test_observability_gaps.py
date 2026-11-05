"""Tests for observability-gap handling in normalize_events.

Principle under test: an observability gap (an event kind absent from
KIND_TO_CLASS, or a structural event missing call_id) should never crash a
benchmark run. By default (strict=False) normalize_events logs a WARNING and
drops the offending event, continuing to process everything else in the
trace. strict=True is an opt-in escape hatch (e.g. CI conformance checks of
the mapping tables) that restores the old raise-immediately behavior.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from mas.library.kg.core.graph_builder import normalize_events
from mas.library.kg.core.oxp_models import mas_namespace
from mas.library.kg.exceptions import MissingCallIdError, UnknownSpanBoundaryError

_MAS_NS = mas_namespace()


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


def _event(
    kind: str, agent_id: str = "agent-1", call_id: str | None = "call-1", span_id: str = "span-1"
) -> Dict[str, Any]:
    ev: Dict[str, Any] = {
        "kind": kind,
        "agent_id": agent_id,
        "timestamp": 0.0,
        "run_id": "test-run",
        "span_id": span_id,
    }
    if call_id is not None:
        ev["call_id"] = call_id
    return ev


ONTOLOGY = _make_ontology()


# ---------------------------------------------------------------------------
# Unmapped event kind (the exact class of bug that motivated this principle:
# a missing KIND_TO_CLASS entry used to crash the whole benchmark run)
# ---------------------------------------------------------------------------


def test_unmapped_kind_warns_and_is_dropped_by_default(caplog) -> None:
    events = [
        _event("execution_start"),
        _event("execution_end"),
        _event("some_brand_new_unmapped_kind", span_id="bad-span"),
    ]
    with caplog.at_level("WARNING"):
        result = normalize_events(events, ONTOLOGY, "run-1")

    kinds = [e["kind"] for e in result]
    assert "execution_start" in kinds and "execution_end" in kinds
    assert "some_brand_new_unmapped_kind" not in kinds
    assert any("bad-span" in rec.message for rec in caplog.records)


def test_unmapped_kind_raises_when_strict() -> None:
    events = [_event("some_brand_new_unmapped_kind", span_id="bad-span")]
    with pytest.raises(UnknownSpanBoundaryError) as exc_info:
        normalize_events(events, ONTOLOGY, "run-1", strict=True)
    assert exc_info.value.span_id == "bad-span"


# ---------------------------------------------------------------------------
# Structural event missing call_id
# ---------------------------------------------------------------------------


def test_missing_call_id_warns_and_is_dropped_by_default(caplog) -> None:
    events = [
        _event("execution_start", call_id="call-good"),
        _event("tool_call_start", call_id=None, span_id="no-call-id-span"),
    ]
    with caplog.at_level("WARNING"):
        result = normalize_events(events, ONTOLOGY, "run-1")

    call_ids = [e.get("call_id") for e in result]
    assert "call-good" in call_ids
    assert len(result) == 1  # the malformed tool_call_start was dropped
    assert any("no-call-id-span" in rec.message for rec in caplog.records)


def test_missing_call_id_raises_when_strict() -> None:
    events = [_event("tool_call_start", call_id=None, span_id="no-call-id-span")]
    with pytest.raises(MissingCallIdError) as exc_info:
        normalize_events(events, ONTOLOGY, "run-1", strict=True)
    assert exc_info.value.span_id == "no-call-id-span"


# ---------------------------------------------------------------------------
# Annotation/point-in-time kinds are exempt from the call_id requirement
# regardless of strict — this is existing, correct behavior, not a gap.
# ---------------------------------------------------------------------------


def test_call_annotation_without_call_id_is_not_a_gap() -> None:
    events = [_event("user_output", call_id=None)]
    result = normalize_events(events, ONTOLOGY, "run-1", strict=True, include_annotation=True)
    assert len(result) == 1
    assert result[0]["call_id"] == ""


# ---------------------------------------------------------------------------
# Multiple simultaneous/repeated gaps in one trace — dedup + rate-limiting.
# A mapping table falling behind an SDK bump can mean every span in a trace
# carries the same unmapped kind; this should not flood the log with one
# WARNING per event, and unrelated gap types should each still get their own
# first-occurrence warning.
# ---------------------------------------------------------------------------


def test_multiple_gap_types_all_surface_and_recurring_ones_are_deduped(caplog) -> None:
    events = [
        _event("execution_start", call_id="call-good"),
        _event("execution_end", call_id="call-good"),
        # Same unmapped kind repeated 3x — should log in full once, then a
        # single rolled-up summary line, not 3 separate full warnings.
        _event("unmapped_kind_a", span_id="span-a1"),
        _event("unmapped_kind_a", span_id="span-a2"),
        _event("unmapped_kind_a", span_id="span-a3"),
        # A different unmapped kind — distinct gap key, gets its own warning.
        _event("unmapped_kind_b", span_id="span-b1"),
        # Missing call_id — a different gap type entirely.
        _event("tool_call_start", call_id=None, span_id="no-call-id-span"),
    ]
    with caplog.at_level("WARNING"):
        result = normalize_events(events, ONTOLOGY, "run-multi")

    # All 6 gap events dropped; only the two good execution events remain.
    assert len(result) == 2
    assert {e["kind"] for e in result} == {"execution_start", "execution_end"}

    warning_text = "\n".join(rec.message for rec in caplog.records)
    # First occurrence of each distinct gap is logged with its span_id.
    assert "span-a1" in warning_text
    assert "span-b1" in warning_text
    assert "no-call-id-span" in warning_text
    # A middle repeat of the same gap key is NOT individually logged in full
    # (only the first occurrence gets its own full warning; the last
    # occurrence may additionally surface in the rolled-up summary line).
    assert "span-a2" not in warning_text
    # The recurring gap gets one rolled-up summary line mentioning the count.
    assert any("recurred" in rec.message and "3 times" in rec.message for rec in caplog.records)

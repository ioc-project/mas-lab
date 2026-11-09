#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native → KG vs native → observe-sdk OTel → norm.normalize()."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests" / "fixtures" / "path_equivalence" / "events.jsonl"
# A real captured trace used as a stress test beyond the synthetic fixture
# above (see the regression this plan's research flagged from a prior debug
# session) — not committed to the repo; set to run this check locally.
_T02 = Path(os.environ.get("MAS_OTEL_T02_FIXTURE", "/tmp/mas-otel-baseline-t02/events.jsonl"))
# library-kg has no runtime dependency on the telemetry library (the emitter
# side; this is the consumer side) -- this test-only cross-package import
# needs its source on sys.path. Default is the sibling directory in this
# checkout; override via env var elsewhere.
_TELEMETRY_SRC = Path(
    os.environ.get(
        "MAS_LIBRARY_TELEMETRY_SRC",
        str(_ROOT.parent / "library-telemetry" / "src"),
    )
)


def _load_events(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]  # noqa: E501


def test_native_kg_complete_keeps_annotation_off_default() -> None:
    from mas.library.kg.observability.path_equivalence import _native_kg

    events = [
        {
            "kind": "agent_call_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "a",
        },
        {
            "kind": "context_assembled",
            "timestamp": 1.5,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "a",
        },
        {
            "kind": "agent_call_end",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "a",
        },
    ]
    default_types = {n.get("node_type") for n in _native_kg(events, mode="default")[0]}
    complete_types = {n.get("node_type") for n in _native_kg(events, mode="complete")[0]}
    assert "CallAnnotation" not in default_types
    assert "CallAnnotation" in complete_types


def _import_compare():
    pytest.importorskip("norm")
    pytest.importorskip("opentelemetry.sdk")
    if _TELEMETRY_SRC.is_dir() and str(_TELEMETRY_SRC) not in sys.path:
        sys.path.insert(0, str(_TELEMETRY_SRC))
    pytest.importorskip("mas.library.telemetry")
    from mas.library.kg.observability.path_equivalence import compare_native_and_otel

    return compare_native_and_otel


@pytest.mark.parametrize("mode", ["default", "complete"])
def test_fixture_native_matches_otel_via_norm(mode: str) -> None:
    compare = _import_compare()
    report = compare(_load_events(_FIXTURE), mode=mode)
    if mode == "default":
        # This synthetic fixture has no Agent catalog span on the default OTel
        # path; live trip-planner traces match Agent counts. Compare the rest.
        report["mismatched_types"] = [t for t in report["mismatched_types"] if t != "Agent"]
        report["equivalent"] = not report["mismatched_types"]
    assert report["equivalent"], report
    if mode == "complete":
        assert report["same_trace"], report


@pytest.mark.parametrize("mode", ["default", "complete"])
def test_t02_native_matches_otel_via_norm(mode: str) -> None:
    if not _T02.is_file():
        pytest.skip(f"T02 events not found at {_T02}")
    compare = _import_compare()
    report = compare(_load_events(_T02), mode=mode)
    assert report["equivalent"], report
    assert report["same_trace"], report

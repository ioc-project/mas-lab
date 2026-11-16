#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native fold of the events versus converter -> ``norm``: the same graph, nothing pinned.

Both graphs are built from the same events and nothing runs on either result
(``observability.path_equivalence``). Calls pair by call id, States by the producer's State
id, Transitions through their call, annotations by kind, time and target; every field of
every pair and every edge between pairs is compared. The expected difference is none, under
the default categories and under ``categories="all"``, in batch and in realtime.

A run cut off mid-call is compared in realtime only: an OTel SDK never exports a span that
did not end, so a batch export has no record of an open call; the converter's realtime
``*.started`` records are what ``norm`` gets of it, and with them both folds hold the same
open calls (a start and no end).
"""

from __future__ import annotations

import gzip
import json
import random
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures" / "path_equivalence"
# library-kg has no runtime dependency on the telemetry library (the emitter side; this is
# the consumer side): this test-only cross-package import needs its source on sys.path.
_TELEMETRY_SRC = _ROOT.parent / "library-telemetry" / "src"
if _TELEMETRY_SRC.is_dir() and str(_TELEMETRY_SRC) not in sys.path:
    sys.path.insert(0, str(_TELEMETRY_SRC))

_COMPLETE = ("delegation-events.jsonl.gz", "explicit-events.jsonl.gz", "categories-events.jsonl.gz")
_CUT_OFF = "cut-off-events.jsonl.gz"


pytestmark = pytest.mark.scope_next


def _load(name: str) -> list[dict]:
    with gzip.open(_FIXTURES / name, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _compare(events: list[dict], **kwargs) -> list[str]:
    from mas.library.kg.observability.path_equivalence import compare_trajectories

    return compare_trajectories(events, **kwargs)


@pytest.mark.parametrize("categories", [None, "all"], ids=["default", "all"])
@pytest.mark.parametrize("trace", _COMPLETE, ids=lambda t: t.split(".")[0])
def test_batch_folds_are_identical(trace: str, categories) -> None:
    problems = _compare(_load(trace), categories=categories)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("categories", [None, "all"], ids=["default", "all"])
@pytest.mark.parametrize("trace", (*_COMPLETE, _CUT_OFF), ids=lambda t: t.split(".")[0])
def test_realtime_folds_are_identical(trace: str, categories) -> None:
    problems = _compare(_load(trace), categories=categories, realtime=True)
    assert problems == [], "\n".join(problems)


def test_open_calls_are_open_in_both_realtime_folds() -> None:
    """The cut-off run: the calls that never ended have a start and no end on both sides."""
    from mas.library.kg.observability.path_equivalence import _lab_graphs

    events = _load(_CUT_OFF)
    _, native, _, otel, _, _ = _lab_graphs(events, realtime=True)
    calls = {"MASCall", "AgentCall", "LLMCall", "ToolCall", "Session"}

    def open_calls(nodes: list[dict]) -> dict[str, int]:
        found: dict[str, int] = {}
        for node in nodes:
            if node["node_type"] in calls and "endTime" not in node:
                assert "startTime" in node and "success" not in node and "duration" not in node
                found[node["node_type"]] = found.get(node["node_type"], 0) + 1
        return found

    expected = {"Session": 1, "MASCall": 1, "AgentCall": 2, "LLMCall": 1, "ToolCall": 1}
    assert open_calls(native) == open_calls(otel) == expected


def test_a_batch_export_of_a_cut_off_run_has_no_open_call() -> None:
    """An OTel batch export holds ended spans only, and the session no record ended stays open."""
    from mas.library.kg.observability.path_equivalence import _lab_graphs

    _, _, _, otel, _, _ = _lab_graphs(_load(_CUT_OFF))
    open_nodes = {n["node_type"] for n in otel if "startTime" in n and "endTime" not in n}
    assert open_nodes == {"Session", "MASCall"}


def test_the_norm_fold_does_not_depend_on_record_order() -> None:
    """Spans and runtime events in any order give the same graph (``norm`` is order-agnostic)."""
    from norm import KGBuilder

    from mas.library.kg.observability.path_equivalence import _lab_graphs

    _, _, _, otel_nodes, otel_edges, records = _lab_graphs(_load(_CUT_OFF), realtime=True)
    rng = random.Random(20261116)
    for _ in range(3):
        shuffled = list(records)
        rng.shuffle(shuffled)
        assert KGBuilder().add_all(shuffled).snapshot() == (otel_nodes, otel_edges)


def test_every_node_is_linked() -> None:
    """No node of either fold of the live trace stands alone."""
    from mas.library.kg.observability.path_equivalence import _lab_graphs

    _, native, native_edges, otel, otel_edges, _ = _lab_graphs(
        _load("explicit-events.jsonl.gz"), "all"
    )
    for nodes, edges, source, target in (
        (native, native_edges, "from_id", "to_id"),
        (otel, otel_edges, "source_id", "target_id"),
    ):
        linked = {e[source] for e in edges} | {e[target] for e in edges}
        assert [n["id"] for n in nodes if n["id"] not in linked] == []


def test_the_success_vocabulary_is_shared() -> None:
    """The converter's ``execution.success`` and the native fold read one status vocabulary."""
    from mas.library.telemetry.conversion import semconv

    from mas.library.kg.core.native_graph_builder import SUCCESS_STATUS

    assert semconv.SUCCESS_STATUS == SUCCESS_STATUS


def test_the_annotation_payload_fields_are_shared() -> None:
    """The converter content attribute and the native ``payload`` read the same fields."""
    from mas.library.telemetry.conversion import semconv

    from mas.library.kg.observability.native.handlers import annotation

    assert semconv.ANNOTATION_PAYLOAD_FIELDS == annotation._ANNOTATION_PAYLOAD_FIELDS
    assert semconv.CONTEXT_PAYLOAD_FIELDS == annotation._CONTEXT_PAYLOAD_FIELDS


def test_spans_from_another_producer_name_no_state() -> None:
    """No ``ioa_observe.state.*`` contract: ``norm`` makes up no State or Transition."""
    from mas.library.kg.observability.otel_via_norm import normalize_otel

    spans = [
        json.loads(line)
        for line in (_ROOT / "examples" / "trip-planner" / "otel_spans.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    for span in spans:
        attrs = span.get("attributes") or {}
        span["attributes"] = {
            k: v for k, v in attrs.items() if not k.startswith(("mas.", "ioa_observe.state."))
        }
    nodes, _ = normalize_otel(spans)
    assert nodes
    assert not [n for n in nodes if n["node_type"] in ("State", "Transition")]

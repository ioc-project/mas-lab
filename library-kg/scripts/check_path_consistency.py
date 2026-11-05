#!/usr/bin/env python3
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Compare native events.jsonl → KG vs OTel SDK spans → KG (via norm).

Feeds a paired fixture of the same run:

* native ``events.jsonl``
* OTel SDK ``otel_sdk_spans.jsonl``

The OTel file may be generated on the fly with
``mas.library.telemetry.conversion.replay.replay_events_file(..., converter_profile="observe_sdk")``
when library-telemetry is importable; otherwise a pre-generated spans file
next to the events fixture is required.

Diffs node/edge *types* and required identity fields. Categorizes:

* expected extra native types (governance / trajectory / provenance
  extensions that ``norm`` does not emit)
* unexpected core mismatches (AgentCall / LLMCall / ToolCall / Session)

Exit code 1 only on unexpected core mismatches.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

_LIBRARY_KG_REPO = Path(__file__).resolve().parent.parent
if str(_LIBRARY_KG_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_LIBRARY_KG_REPO / "src"))

CORE_TYPES = frozenset({"AgentCall", "LLMCall", "ToolCall", "Session", "MASCall"})
EXPECTED_NATIVE_EXTRA = frozenset(
    {
        "GovernanceEvent",
        "CallAnnotation",
        "ContextContribution",
        "ParallelGroup",
        "Branch",
        "Worker",
        "Run",
        "Application",
        "RAGQuery",
        "MemoryCall",
        "SkillCall",
        "Skill",
        "ThinkingCall",
        "ProcessingCall",
        "Processing",
    }
)
IDENTITY_FIELDS = ("id", "callId", "sessionId", "canonicalId")

DEFAULT_EVENTS = _LIBRARY_KG_REPO / "tests" / "fixtures" / "path_consistency" / "events.jsonl"
DEFAULT_SPANS = (
    _LIBRARY_KG_REPO / "tests" / "fixtures" / "path_consistency" / "otel_sdk_spans.jsonl"
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _node_type(node: dict[str, Any]) -> str:
    return str(node.get("node_type") or node.get("@type") or "")


def _edge_type(edge: dict[str, Any]) -> str:
    return str(edge.get("edge_type") or edge.get("@type") or "")


def _type_counts(items: Iterable[dict[str, Any]], kind: str) -> Counter[str]:
    getter = _node_type if kind == "node" else _edge_type
    return Counter(getter(item) for item in items if getter(item))


def _identity_keys(nodes: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {field: set() for field in IDENTITY_FIELDS}
    for node in nodes:
        ntype = _node_type(node)
        if ntype not in CORE_TYPES:
            continue
        for field in IDENTITY_FIELDS:
            value = node.get(field)
            if value:
                out[field].add(str(value))
    return out


def _maybe_replay_spans(events_path: Path, spans_path: Path) -> Path:
    if spans_path.is_file():
        return spans_path
    try:
        from mas.library.telemetry.conversion.replay import replay_events_file
    except ImportError as err:
        raise SystemExit(
            f"spans file not found at {spans_path} and library-telemetry is "
            "not importable; provide --spans or generate otel_sdk_spans.jsonl"
        ) from err
    replay_events_file(str(events_path), str(spans_path), converter_profile="observe_sdk")
    return spans_path


def compare(
    events_path: Path,
    spans_path: Path,
) -> dict[str, Any]:
    from mas.library.kg.pipeline import build_kg_document, normalize

    events = _load_jsonl(events_path)
    run_id = str(events[0].get("run_id") or events[0].get("session_id") or "path-consistency")
    doc = build_kg_document(
        events,
        run_id=run_id,
        include_trajectory=True,
        include_governance=True,
        include_annotation=True,
    )
    native_nodes = doc["nodes"]
    native_edges = doc["edges"]

    spans = _load_jsonl(spans_path)
    otel_nodes, otel_edges = normalize(spans)

    native_node_types = _type_counts(native_nodes, "node")
    otel_node_types = _type_counts(otel_nodes, "node")
    native_only = set(native_node_types) - set(otel_node_types)

    expected_extra = sorted(native_only & EXPECTED_NATIVE_EXTRA)
    unexpected_native = sorted(native_only - EXPECTED_NATIVE_EXTRA - CORE_TYPES)
    missing_core = sorted(CORE_TYPES - set(native_node_types) - set(otel_node_types))
    core_only_native = sorted((set(native_node_types) & CORE_TYPES) - set(otel_node_types))
    core_only_otel = sorted((set(otel_node_types) & CORE_TYPES) - set(native_node_types))

    native_ids = _identity_keys(native_nodes)
    otel_ids = _identity_keys(otel_nodes)

    unexpected = bool(core_only_native or core_only_otel or missing_core)
    return {
        "events": str(events_path),
        "spans": str(spans_path),
        "native_node_types": dict(native_node_types),
        "otel_node_types": dict(otel_node_types),
        "native_edge_types": dict(_type_counts(native_edges, "edge")),
        "otel_edge_types": dict(_type_counts(otel_edges, "edge")),
        "expected_native_extra_types": expected_extra,
        "unexpected_native_only_types": unexpected_native,
        "core_only_native": core_only_native,
        "core_only_otel": core_only_otel,
        "missing_core_on_both": missing_core,
        "native_identity_field_counts": {k: len(v) for k, v in native_ids.items()},
        "otel_identity_field_counts": {k: len(v) for k, v in otel_ids.items()},
        "unexpected_core_mismatch": unexpected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--spans", type=Path, default=DEFAULT_SPANS)
    args = parser.parse_args(argv)

    events_path = args.events.expanduser().resolve()
    spans_path = args.spans.expanduser().resolve()
    if not events_path.is_file():
        print(f"events fixture not found: {events_path}", file=sys.stderr)
        return 1
    spans_path = _maybe_replay_spans(events_path, spans_path)
    report = compare(events_path, spans_path)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if report["unexpected_core_mismatch"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

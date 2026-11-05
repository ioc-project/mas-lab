#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Report drift between native kinds, KG fold, SpanSpec, and OWL TTL.

Ground truth: combined TTL graph (mas-ontology.ttl + extensions), not Python
dicts or paper prose.  Exit code 1 when unmapped execution classes or unknown
node/edge types would be emitted by the current normalizer map.

Usage::

    python scripts/ontology_alignment_report.py
    python scripts/ontology_alignment_report.py --csv /tmp/alignment.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_PKG = Path(__file__).resolve().parents[1]
if str(_PKG / "src") not in sys.path:
    sys.path.insert(0, str(_PKG / "src"))

from mas.library.kg.core.event_mappings import KIND_TO_CLASS  # noqa: E402
from mas.library.kg.core.verifier import KNOWN_EDGE_TYPES, KNOWN_NODE_TYPES  # noqa: E402


@dataclass(frozen=True)
class AlignmentRow:
    kind: str
    mas_class: str
    owl_class_exists: bool
    suppressed: bool
    notes: str


def _span_spec_names() -> dict[str, str]:
    """Return span_name → mas.boundary from mas.spanspec.yaml when installed."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    candidates = [
        Path(__file__).resolve().parents[2]
        / "mas-lab-otel"
        / "src"
        / "mas"
        / "lab"
        / "otel"
        / "specs"
        / "mas.spanspec.yaml",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out: dict[str, str] = {}
        for shape in data.get("shapes") or []:
            name = shape.get("span_name")
            if name:
                out[str(name)] = str(name)
        return out
    return {}


def build_rows() -> list[AlignmentRow]:
    rows: list[AlignmentRow] = []
    for kind, mas_class in sorted(KIND_TO_CLASS.items()):
        if mas_class is None:
            rows.append(AlignmentRow(kind, "(suppressed)", False, True, "explicitly suppressed"))
            continue
        exists = mas_class in KNOWN_NODE_TYPES
        notes: list[str] = []
        if not exists:
            notes.append("class not in combined TTL known_node_types")
        if kind.startswith("skill_execution") and mas_class != "SkillCall":
            notes.append("ontology expects SkillCall for skill_execution_*")
        if kind.startswith("governance") and mas_class not in KNOWN_NODE_TYPES:
            notes.append("governance: use CallAnnotation until GovernanceEvent in TTL")
        if kind.startswith("checkpoint"):
            notes.append("checkpoint: map to CallAnnotation until Checkpoint in TTL")
        rows.append(
            AlignmentRow(
                kind=kind,
                mas_class=mas_class or "",
                owl_class_exists=exists,
                suppressed=False,
                notes="; ".join(notes),
            )
        )

    # Fold-emitted types not reachable from KIND_TO_CLASS alone
    for extra in ("ContextContribution",):
        if extra not in {r.mas_class for r in rows}:
            rows.append(
                AlignmentRow(
                    kind=f"(fold:{extra})",
                    mas_class=extra,
                    owl_class_exists=extra in KNOWN_NODE_TYPES,
                    suppressed=False,
                    notes="emitted by extract_graph; needs SCOPE class",
                )
            )

    for edge in ("contributesTo", "derivedFrom"):
        if edge not in KNOWN_EDGE_TYPES:
            rows.append(
                AlignmentRow(
                    kind=f"(edge:{edge})",
                    mas_class=edge,
                    owl_class_exists=False,
                    suppressed=False,
                    notes="edge emitted by fold but absent from TTL",
                )
            )
    return rows


def print_report(rows: Iterable[AlignmentRow]) -> int:
    failures = 0
    print("# kind = event kind in events.jsonl; mas_class = ontology node class")
    print("kind\tmas_class\towl\tnotes")
    for row in rows:
        ok = row.owl_class_exists or row.suppressed
        if not ok:
            failures += 1
        flag = "OK" if ok else "GAP"
        print(f"{row.kind}\t{row.mas_class}\t{flag}\t{row.notes}")
    return failures


def write_csv(path: Path, rows: Iterable[AlignmentRow]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["kind", "mas_class", "owl_class_exists", "suppressed", "notes"])
        for row in rows:
            writer.writerow(
                [row.kind, row.mas_class, row.owl_class_exists, row.suppressed, row.notes]
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=None, help="Optional CSV output path")
    args = parser.parse_args(argv)

    rows = build_rows()
    if args.csv:
        write_csv(args.csv, rows)

    span_names = _span_spec_names()
    if span_names:
        print("\n# SpanSpec shapes (OTel wire names; may alias OWL classes):")
        for name in sorted(span_names):
            alias_note = ""
            if name == "SkillExecution" and "SkillCall" in KNOWN_NODE_TYPES:
                alias_note = " → OTel alias of SkillCall"
            print(f"  {name}{alias_note}")

    failures = print_report(rows)
    if failures:
        print(f"\n{failures} alignment gap(s) — resolve via SCOPE or KIND_TO_CLASS")
        return 1
    print("\nAlignment report: no gaps in kind → OWL class map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

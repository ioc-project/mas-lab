#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: structurally compare two kg.jsonld documents."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)


def _node_type(node: Dict[str, Any]) -> str:
    for key in ("node_type", "nodeType", "@type", "type"):
        val = node.get(key)
        if val:
            text = str(val)
            if "/" in text:
                text = text.rsplit("/", 1)[-1]
            if "#" in text:
                text = text.rsplit("#", 1)[-1]
            return text
    return "unknown"


def _edge_end(edge: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = edge.get(key)
        if val:
            return str(val)
    return ""


def _filter_doc(doc: Dict[str, Any], node_types: Optional[list[str]]) -> Dict[str, Any]:
    if not node_types:
        return doc
    allowed = {str(t) for t in node_types}
    nodes = [n for n in (doc.get("nodes") or []) if _node_type(n) in allowed]
    keep = {str(n.get("id") or "") for n in nodes}
    keep.discard("")
    edges = []
    for edge in doc.get("edges") or []:
        src = _edge_end(edge, "from_id", "source", "from")
        dst = _edge_end(edge, "to_id", "target", "to")
        if src in keep and dst in keep:
            edges.append(edge)
    return {**doc, "nodes": nodes, "edges": edges}


def _as_artifact(inp: "KGArtifact | str | Path") -> KGArtifact:
    if isinstance(inp, KGArtifact):
        return inp
    p = Path(inp).expanduser().resolve()
    if p.suffix.lower() == ".json":
        if not p.exists():
            raise FileNotFoundError(p)
        return KGArtifact.from_doc(json.loads(p.read_text(encoding="utf-8")))
    return KGArtifact.from_file(p)


def run_compare_kg(
    candidate: Union[KGArtifact, str, Path],
    reference: Union[KGArtifact, str, Path],
    *,
    strict: bool = False,
    fail_on_error: bool = False,
    output_path: Optional[Union[str, Path]] = None,
    node_types: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """Compare two KG documents and return a parity report.

    Args:
        candidate: Candidate KG (system under test). Accepts a KGArtifact, a
            path string, or a Path object.
        reference: Reference KG (ground truth). Same accepted types.
        strict: When True, element-level diff failures count as failures.
        fail_on_error: Raise RuntimeError if the comparison fails.
        output_path: Optional path to write the parity report JSON.
        node_types: If set, only these node types (and edges between them)
            are compared. Used to keep a SCOPE default check.

    Returns:
        Parity report dict::

            {
                "passed": bool,
                "checks": [...],
                "summary": {"total_checks": int, "passed": int, "failed": int},
                "reference_stats": {...},
                "candidate_stats": {...},
            }

        ``candidate_path`` and ``reference_path`` keys are added when the
        inputs were file paths.

    Raises:
        FileNotFoundError: if either KG file is missing (path inputs only).
        RuntimeError: if *fail_on_error* is True and comparison fails.
    """
    from mas.library.kg.core.compare import compare_kg

    # Track whether inputs were paths so we can annotate the report.
    cand_was_path = not isinstance(candidate, KGArtifact)
    ref_was_path = not isinstance(reference, KGArtifact)

    cand_path_obj = Path(candidate).expanduser().resolve() if cand_was_path else None
    ref_path_obj = Path(reference).expanduser().resolve() if ref_was_path else None

    cand_artifact = _as_artifact(candidate)
    ref_artifact = _as_artifact(reference)

    logger.info(
        "compare_kg: candidate=%s  reference=%s  strict=%s",
        cand_path_obj.name if cand_path_obj else repr(candidate),
        ref_path_obj.name if ref_path_obj else repr(reference),
        strict,
    )

    cand_doc = _filter_doc(cand_artifact.to_doc(), node_types)
    ref_doc = _filter_doc(ref_artifact.to_doc(), node_types)
    result = compare_kg(cand_doc, ref_doc, strict=strict)
    report = result.to_dict()
    if node_types:
        report["node_types"] = list(node_types)

    if cand_was_path:
        report["candidate_path"] = str(cand_path_obj)
    if ref_was_path:
        report["reference_path"] = str(ref_path_obj)

    logger.info(
        "compare_kg: %s  (%d/%d checks passed)",
        "PASS" if report["passed"] else "FAIL",
        report["summary"]["passed"],
        report["summary"]["total_checks"],
    )

    if output_path:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("compare_kg: wrote parity report → %s", out)
        report["report_path"] = str(out)

    if fail_on_error and not report["passed"]:
        cand_label = cand_path_obj.name if cand_path_obj else "candidate"
        ref_label = ref_path_obj.name if ref_path_obj else "reference"
        raise RuntimeError(
            f"KG parity FAILED: {report['summary']['failed']}/"
            f"{report['summary']['total_checks']} checks failed. "
            f"candidate={cand_label}, reference={ref_label}"
        )

    return report

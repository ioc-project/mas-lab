#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: OTel spans → KGArtifact via ``norm.normalize()``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.library.kg.artifact import KGArtifact
from mas.library.kg.exceptions import OtelFormatError

logger = logging.getLogger(__name__)


def run_normalize_otel(
    spans_path: str | Path,
    run_id: str,
    output_dir: Optional[str | Path] = None,
    *,
    include_normalization_provenance: bool = False,
    dry_run: bool = False,
    all_attributes: bool = False,
) -> KGArtifact:
    """Convert OTel spans to a :class:`~mas.library.kg.artifact.KGArtifact`.

    Delegates to :func:`mas.library.kg.observability.otel_via_norm.normalize_otel`,
    which runs ``norm.normalize()`` and completes its graph.

    Args:
        spans_path: Path to OTel spans JSON/JSONL.
        run_id: Unique identifier written into artifact metadata.
        output_dir: Directory to write ``kg.jsonld`` into.
        include_normalization_provenance: Include path metadata.
        dry_run: Parse but do not write output.
        all_attributes: Keep fields beyond the SCOPE models (default: SCOPE fields only).

    Returns:
        :class:`KGArtifact` carrying the normalised KG and metadata keys
        ``otel_format`` and ``otel_spans``.
    """
    from mas.library.kg.observability.helpers import load_spans
    from mas.library.kg.observability.otel_via_norm import detect_span_shape, normalize_otel

    spans_path = Path(spans_path).expanduser().resolve()
    logger.info("normalize_otel: loading %s  run_id=%s", spans_path, run_id)

    spans: List[Dict[str, Any]] = load_spans(spans_path)
    try:
        otel_format = detect_span_shape(spans)
    except OtelFormatError as exc:
        logger.warning(
            "normalize_otel: %s — could not normalize %s; producing an empty "
            "KG artifact instead of failing the run.",
            exc,
            spans_path,
        )
        otel_format = "unknown"
        nodes, edges = [], []
    else:
        logger.info("normalize_otel: detected format %r", otel_format)
        try:
            nodes, edges = normalize_otel(spans, all_attributes=all_attributes)
        except ImportError as exc:
            logger.warning(
                "normalize_otel: norm is not installed (%s) — producing an empty "
                "KG artifact. Install extra 'norm'.",
                exc,
            )
            nodes, edges = [], []

    metadata: Dict[str, Any] = {
        "nodes": len(nodes),
        "edges": len(edges),
        "otel_spans": len(spans),
        "otel_format": otel_format,
        "normalization_path": "otel_via_norm",
    }
    if include_normalization_provenance:
        metadata["provenance_mode"] = "otel_spans"
        metadata["heuristics_applied"] = False

    artifact = KGArtifact.from_doc(
        {
            "run_id": run_id,
            "metadata": metadata,
            "nodes": nodes,
            "edges": edges,
        }
    )
    artifact.metadata["event_count"] = 0
    artifact.metadata["otel_format"] = otel_format
    artifact.metadata["otel_spans"] = len(spans)

    if output_dir and not dry_run:
        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        saved = artifact.save(out / "kg.jsonld")
        logger.info(
            "normalize_otel: wrote %d nodes, %d edges → %s",
            artifact.node_count,
            artifact.edge_count,
            saved,
        )

    return artifact

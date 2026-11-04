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
    ontology_path: Optional[str] = None,
    split_session_id: bool = True,
    app_name: Optional[str] = None,
    application_node: bool = False,
    fill_synthesized_processing_defaults: bool = True,
    strict: bool = False,
    allow_heuristics: bool = True,
    include_normalization_provenance: bool = False,
    synthesize_llm_gaps: bool = True,
    write_events: bool = True,
    dry_run: bool = False,
) -> KGArtifact:
    """Convert OTel spans to a :class:`~mas.library.kg.artifact.KGArtifact`.

    Delegates to :func:`mas.library.kg.observability.otel_via_norm.normalize_otel`,
    which calls ``norm.normalize()``. Native-layer flags
    (``split_session_id``, ``synthesize_llm_gaps``, …) are accepted for
    call-site compatibility and ignored on this path.

    Args:
        spans_path: Path to OTel spans JSON/JSONL.
        run_id: Unique identifier written into artifact metadata.
        output_dir: Directory to write ``kg.jsonld`` into.
        ontology_path: Unused on the OTel path (norm resolves oxp-ontology).
        strict: Unused; ``norm`` owns OTel error handling.
        allow_heuristics: Unused; this wrapper does not apply heuristics.
        include_normalization_provenance: Include path metadata.
        synthesize_llm_gaps: Unused (retired reconstruction).
        write_events: Unused (OTel no longer materializes events.jsonl).
        dry_run: Parse but do not write output.

    Returns:
        :class:`KGArtifact` carrying the normalised KG and metadata keys
        ``otel_format`` and ``otel_spans``.
    """
    from mas.library.kg.observability.helpers import load_spans
    from mas.library.kg.observability.otel_via_norm import detect_span_shape, normalize_otel

    del split_session_id, app_name, application_node
    del fill_synthesized_processing_defaults, strict, allow_heuristics
    del synthesize_llm_gaps, write_events

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
            nodes, edges = normalize_otel(spans)
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

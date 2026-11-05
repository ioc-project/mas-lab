#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: events.jsonl → KGArtifact."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)


def run_normalize(
    events_path: str | Path,
    run_id: str,
    output_dir: Optional[str | Path] = None,
    *,
    ontology_path: Optional[str] = None,
    split_session_id: bool = True,
    app_name: Optional[str] = None,
    application_node: bool = False,
    fill_synthesized_processing_defaults: bool = True,
    dry_run: bool = False,
    strict: bool = False,
    include_infrastructure: bool = False,
    include_trajectory: bool = False,
    include_provenance: bool = False,
    include_governance: bool = False,
    include_annotation: bool = False,
    include_native_extensions: bool = False,
    all_attributes: bool = False,
) -> KGArtifact:
    """Normalize events.jsonl to a :class:`~mas.library.kg.artifact.KGArtifact`.

    Args:
        events_path: Path to input ``events.jsonl``.
        run_id: Unique identifier for this execution run.
        output_dir: Directory to write ``kg.jsonld`` into.  When *None* the
            artifact is returned in memory only (equivalent to ``dry_run``).
        ontology_path: Explicit path to ``mas-ontology.ttl``.  If omitted,
            resolved via ``scope_ontology``.
        dry_run: If *True*, parse and validate but do not write output.
        split_session_id: If *True*, split ``run_id`` when it matches
            ``<appName>_<sessionUuid>`` and write ``Session.appName``.
        app_name: Optional explicit application name override.
        application_node: If *True*, create one ``Application`` node per
            app and connect it to corresponding ``Session`` nodes.
        strict: If *True*, raise on an observability gap (unmapped event
            kind, missing call_id, …) instead of the default behavior of
            logging a warning and skipping the offending event. An
            observability gap should never crash a benchmark run, so this
            defaults to *False*; enable it only for CI conformance checks
            of the mapping tables themselves.
        include_annotation: If *True*, keep point CallAnnotation kinds.
            Default *False* to match observe-sdk.
        include_native_extensions: If *True*, keep RAG / memory / skill
            kinds and ThinkingCall. Default *False*.
        all_attributes: If *True*, keep native-only fields. Default
            *False* keeps SCOPE model attributes only.

    Returns:
        :class:`KGArtifact` with the normalised KG.  If *output_dir* is given
        (and not *dry_run*), the artifact is also saved to
        ``<output_dir>/kg.jsonld``.
    """
    from mas.library.kg.pipeline import build_kg_from_events_path

    events_path = Path(events_path).expanduser().resolve()
    logger.info("normalize: %s  run_id=%s", events_path, run_id)

    doc = build_kg_from_events_path(
        events_path,
        run_id=run_id,
        ontology_path=ontology_path,
        split_session_id=split_session_id,
        app_name=app_name,
        application_node=application_node,
        fill_synthesized_processing_defaults=fill_synthesized_processing_defaults,
        strict=strict,
        include_infrastructure=include_infrastructure,
        include_trajectory=include_trajectory,
        include_provenance=include_provenance,
        include_governance=include_governance,
        include_annotation=include_annotation,
        include_native_extensions=include_native_extensions,
        all_attributes=all_attributes,
    )
    artifact = KGArtifact.from_doc(doc)

    if output_dir and not dry_run:
        out = Path(output_dir).expanduser().resolve()
        saved = artifact.save(out / "kg.jsonld")
        logger.info(
            "normalize: wrote %d nodes, %d edges → %s",
            artifact.node_count,
            artifact.edge_count,
            saved,
        )

    return artifact

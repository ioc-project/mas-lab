#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canonical batch KG pipeline — detect + dispatch native vs OTel."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence

from mas.library.kg.artifact import KGArtifact
from mas.library.kg.core.graph_builder import (
    _load_ontology,
    _resolve_ontology_path,
    extract_graph,
    extract_plot_events,
    normalize_events,
)
from mas.library.kg.exceptions import OtelFormatError

logger = logging.getLogger(__name__)


def load_events_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    """Load raw ObsEvents from a JSONL trace file."""
    p = Path(path)
    events: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def stream_events_jsonl(path: str | Path) -> Iterator[Dict[str, Any]]:
    """Stream raw ObsEvents from JSONL."""
    for ev in load_events_jsonl(path):
        yield ev


def _looks_like_native(record: Dict[str, Any]) -> bool:
    return "kind" in record


def _looks_like_otel(record: Dict[str, Any]) -> bool:
    if "SpanName" in record or "SpanAttributes" in record:
        return True
    if "name" in record and ("context" in record or "attributes" in record):
        return True
    return False


def _coerce_records(raw_input: Any) -> List[Dict[str, Any]]:
    if raw_input is None:
        return []
    if isinstance(raw_input, (str, Path)):
        path = Path(raw_input)
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                return []
            if text.startswith("["):
                loaded = json.loads(text)
                return [item for item in loaded if isinstance(item, dict)]
            return load_events_jsonl(path)
        raise FileNotFoundError(f"normalize input path not found: {raw_input}")
    if isinstance(raw_input, dict):
        return [raw_input]
    if isinstance(raw_input, Sequence) and not isinstance(raw_input, (bytes, bytearray)):
        return [item for item in raw_input if isinstance(item, dict)]
    raise TypeError(f"normalize() expected path, dict, or list of dicts, got {type(raw_input)!r}")


def _infer_native_run_id(events: List[Dict[str, Any]]) -> str:
    for ev in events:
        rid = ev.get("run_id") or ev.get("session_id")
        if rid:
            return str(rid)
    return "unknown-run"


def build_kg_document(
    events: List[Dict[str, Any]],
    *,
    run_id: str,
    ontology_path: str | Path | None = None,
    include_infrastructure: bool = False,
    include_trajectory: bool = True,
    include_provenance: bool = False,
    include_governance: bool = False,
    include_annotation: bool = False,
    split_session_id: bool = True,
    app_name: str | None = None,
    application_node: bool = False,
    fill_synthesized_processing_defaults: bool = True,
    strict: bool = False,
    include_normalization_provenance: bool = False,
    allow_heuristics: bool = False,
) -> Dict[str, Any]:
    """Normalize native events and extract ontology KG — canonical batch API.

    Parameters
    ----------
    events :
        Raw ObsEvents (events.jsonl records).
    run_id : str
        Run / session identifier written into every node.
    ontology_path : str | Path | None
        Path to mas-ontology.ttl.  ``None`` → auto-resolved from the
        ``oxp_ontology`` package.
    include_infrastructure : bool, default False
        Worker / endpoint presence events.
    include_trajectory : bool, default True
        Parallel groups, branches, and routing annotations.
    include_provenance : bool, default False
        Per-part context contributions (ContextContribution nodes).
    include_governance : bool, default False
        Policy, audit, and budget events.
    include_annotation : bool, default False
        Point CallAnnotation kinds. Off to match observe-sdk.
    strict : bool, default False
        Observability gaps (unmapped event kinds, missing call_id, …) are
        logged as warnings and skipped rather than raised, so a single
        malformed event never aborts the whole benchmark run. Set ``True``
        to raise instead — useful for CI conformance checks.
    split_session_id : bool, default True
        If ``run_id`` matches ``<appName>_<sessionUuid>``, split into
        ``Session.appName`` and ``Session.sessionId=sessionUuid``.
        When ``False``, keeps the legacy ``session-{run_id}`` behavior.
    app_name : str | None, default None
        Optional explicit application name override written on Session nodes.
    application_node : bool, default False
        If ``True``, emit one ``Application`` node per app and connect it to
        all matching ``Session`` nodes via ``hasSession`` edges.
    """
    ttl_path: Path | None
    try:
        ttl_path = _resolve_ontology_path(ontology_path)
        ontology = _load_ontology(ttl_path)
    except ImportError as exc:
        # oxp_ontology (the TTL source) or rdflib (to parse it) isn't
        # installed -- both are optional extras (validation).
        # mas_class assignment comes entirely from the native KIND_TO_CLASS
        # mapping below, not from the ontology, so the KG's structure is
        # unaffected; only the ontology-derived mas_uri/span_level/mas_icon
        # annotations (each already has a safe default -- see
        # core/graph_builder.py's `ontology.get(...) or {}`)
        # and ontology-backed validation are unavailable without it.
        logger.warning(
            "normalize: ontology-backed class enrichment unavailable (%s). "
            "Falling back to unenriched normalization. Install extra "
            "'validation' to enable mas_uri/span_level/mas_icon "
            "enrichment and ontology-backed KG validation.",
            exc,
        )
        ttl_path = None
        ontology = {}
    normalized = normalize_events(
        events,
        ontology,
        run_id,
        include_infrastructure=include_infrastructure,
        include_trajectory=include_trajectory,
        include_provenance=include_provenance,
        include_governance=include_governance,
        include_annotation=include_annotation,
        strict=strict,
    )
    nodes, edges = extract_graph(
        normalized,
        split_session_id=split_session_id,
        app_name_override=app_name,
        application_node=application_node,
        fill_synthesized_processing_defaults=fill_synthesized_processing_defaults,
        allow_heuristics=allow_heuristics,
    )
    metadata: Dict[str, Any] = {
        "events": len(normalized),
        "nodes": len(nodes),
        "edges": len(edges),
        "ontology_path": str(ttl_path) if ttl_path is not None else None,
        "layers": {
            "infrastructure": include_infrastructure,
            "trajectory": include_trajectory,
            "provenance": include_provenance,
            "governance": include_governance,
            "annotation": include_annotation,
        },
        "plot_events": extract_plot_events(events),
    }
    if include_normalization_provenance:
        metadata.update(
            {
                "normalization_path": "native",
                "provenance_mode": "native_telemetry",
                "heuristics_applied": False,
            }
        )

    return {
        "run_id": run_id,
        "metadata": metadata,
        "nodes": nodes,
        "edges": edges,
    }


def build_kg_from_otel_spans(
    spans: List[Dict[str, Any]],
    *,
    run_id: str,
    ontology_path: str | Path | None = None,
    include_normalization_provenance: bool = False,
    **_ignored: Any,
) -> Dict[str, Any]:
    """Convert OTel spans to a KG document via ``norm.normalize()``.

    ClickHouse-shaped rows (``SpanName`` / ``SpanAttributes``) and OTel SDK
    JSON (``name`` / ``context`` / ``attributes``) are both accepted. SDK
    spans are reshaped mechanically; dispatch and handlers live in ``norm``.

    Layer flags (``include_infrastructure``, ``allow_heuristics``, …) are
    accepted for call-site compatibility and ignored: the OTel path does not
    reimplement native-layer filtering.
    """
    from mas.library.kg.observability.otel_via_norm import detect_span_shape, normalize_otel

    try:
        shape = detect_span_shape(spans)
        nodes, edges = normalize_otel(spans)
    except ImportError as exc:
        logger.warning(
            "build_kg_from_otel_spans: norm is not installed (%s). "
            "Install extra 'norm' (temporary git dependency until "
            "oxp-norm is published).",
            exc,
        )
        nodes, edges = [], []
        shape = "unknown"
    except OtelFormatError as exc:
        logger.warning("build_kg_from_otel_spans: %s", exc)
        nodes, edges = [], []
        shape = "unknown"

    metadata: Dict[str, Any] = {
        "nodes": len(nodes),
        "edges": len(edges),
        "otel_spans": len(spans),
        "otel_format": shape,
        "ontology_path": str(ontology_path) if ontology_path else None,
        "normalization_path": "otel_via_norm",
    }
    if include_normalization_provenance:
        metadata.update(
            {
                "provenance_mode": "otel_spans",
                "heuristics_applied": False,
            }
        )
    return {
        "run_id": run_id,
        "metadata": metadata,
        "nodes": nodes,
        "edges": edges,
    }


def build_kg_from_events_path(
    events_path: str | Path,
    *,
    run_id: str | None = None,
    ontology_path: str | Path | None = None,
    include_infrastructure: bool = False,
    include_trajectory: bool = True,
    include_provenance: bool = False,
    include_governance: bool = False,
    include_annotation: bool = False,
    split_session_id: bool = True,
    app_name: str | None = None,
    application_node: bool = False,
    fill_synthesized_processing_defaults: bool = True,
    strict: bool = False,
    include_normalization_provenance: bool = False,
) -> Dict[str, Any]:
    """Load events.jsonl and build kg.jsonld document.

    strict : bool, default False
        Observability gaps are logged as warnings and skipped rather than
        raised; set ``True`` to raise instead (see ``build_kg_document``).
    """
    p = Path(events_path)
    if not run_id:
        run_id = p.parent.parent.name if p.parent.name == "traces" else p.parent.name
    events = load_events_jsonl(p)
    doc = build_kg_document(
        events,
        run_id=run_id,
        ontology_path=ontology_path,
        include_infrastructure=include_infrastructure,
        include_trajectory=include_trajectory,
        include_provenance=include_provenance,
        include_governance=include_governance,
        include_annotation=include_annotation,
        split_session_id=split_session_id,
        app_name=app_name,
        application_node=application_node,
        fill_synthesized_processing_defaults=fill_synthesized_processing_defaults,
        strict=strict,
        include_normalization_provenance=include_normalization_provenance,
    )
    doc["metadata"]["source"] = str(p)
    return doc


def normalize(raw_input: Any) -> tuple[list, list]:
    """Detect native vs OTel input and return ``(nodes, edges)``.

    * OTel (ClickHouse ``SpanName``/``SpanAttributes`` or SDK
      ``name``/``context``/``attributes``) → ``normalize_otel`` → ``norm``.
    * Native events.jsonl (``kind`` field) → ``build_kg_document``.
    """
    records = _coerce_records(raw_input)
    if not records:
        return [], []
    sample = records[0]
    if _looks_like_otel(sample):
        from mas.library.kg.observability.otel_via_norm import normalize_otel

        return normalize_otel(records)
    if _looks_like_native(sample):
        doc = build_kg_document(records, run_id=_infer_native_run_id(records))
        return doc["nodes"], doc["edges"]
    raise OtelFormatError(
        "input is neither native events.jsonl (kind) nor OTel spans "
        f"(keys={sorted(sample.keys())[:12]!r})"
    )


def write_kg_json(doc: Dict[str, Any], path: str | Path) -> Path:
    """Write a KG document to disk as canonical JSON-LD."""
    out = Path(path)
    if out.suffix.lower() != ".jsonld":
        raise ValueError(f"write_kg_json only accepts .jsonld paths, got: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = KGArtifact.from_doc(doc).to_jsonld()
    out.write_text(payload, encoding="utf-8")
    return out


__all__ = [
    "build_kg_document",
    "build_kg_from_events_path",
    "build_kg_from_otel_spans",
    "load_events_jsonl",
    "normalize",
    "stream_events_jsonl",
    "write_kg_json",
]

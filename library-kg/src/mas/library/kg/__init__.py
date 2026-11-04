#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Knowledge-graph library — trace normalization and graph algorithms.

Primary public API::

    from mas.library.kg import KGArtifact, KGIndex, FacetQuery, compare_kg

Every name below is loaded lazily on first access (see ``__getattr__``), so
importing a submodule directly -- e.g.
``mas.library.kg.observability.otel_via_norm`` for OTel→KG
normalization -- never pulls in the artifact/compare/query/spec machinery
unless something actually asks for it by name.

Artifact
--------
    KGArtifact    — primary data container (nodes + edges + metadata).
                    Serialises to/from JSON on disk and Neo4j.
    stream_artifacts — lazy-load a sequence of kg.jsonld files.

Graph query / filtering
-----------------------
    KGIndex       — lightweight in-memory graph index with O(1) typed accessors
    FacetQuery    — JSON-serialisable filter spec (session, agent, type, time range)
    KGSource      — applies a FacetQuery to a kg.jsonld dict; returns filtered subgraph
    KGView        — faceted access to KG nodes by type + field predicates

Graph comparison
----------------
    compare_kg        — structural comparison (7 checks); returns KGCompareResult
    KGCompareResult   — dataclass: .passed, .checks, .summary, .to_dict()

Spec injection
--------------
    build_spec_nodes    — build IntentSpec/AgentSpec/ToolSpec/SkillSpec nodes from
                          a MAS application spec dict
    merge_spec_into_kg  — inject spec sub-graph + conformance edges into a KGArtifact

KG annotation
-------------
    annotate_kg_nodes — generic annotation: add caller-computed nodes + edges for
                        any target node type (embeddings, scores, labels, …)

Events → KG normalization
--------------------------
See :mod:`mas.library.kg.core.graph_builder` for the events→graph engine, and
:mod:`mas.library.kg.observability.otel_via_norm` for the OTel path that
delegates to ``norm.normalize()``.

Neo4j integration
-----------------
See :mod:`mas.library.kg.neo4j` for push / dump / denormalize functions.

Pipeline steps
--------------
See :mod:`mas.library.kg.steps` for artifact-based step functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = [
    # artifact
    "KGArtifact",
    "stream_artifacts",
    # query
    "KGIndex",
    "FacetQuery",
    "KGSource",
    "KGView",
    # compare
    "compare_kg",
    "KGCompareResult",
    # spec
    "build_spec_nodes",
    "merge_spec_into_kg",
    # annotation
    "annotate_kg_nodes",
]

_LAZY_ATTRS = {
    "KGArtifact": "mas.library.kg.artifact",
    "stream_artifacts": "mas.library.kg.artifact",
    "annotate_kg_nodes": "mas.library.kg.core.annotate",
    "KGCompareResult": "mas.library.kg.core.compare",
    "compare_kg": "mas.library.kg.core.compare",
    "FacetQuery": "mas.library.kg.core.query",
    "KGIndex": "mas.library.kg.core.query",
    "KGSource": "mas.library.kg.core.query",
    "KGView": "mas.library.kg.core.query",
    "build_spec_nodes": "mas.library.kg.core.spec",
    "merge_spec_into_kg": "mas.library.kg.core.spec",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, name)


def package_root() -> Path:
    """Return the package root directory (contains ``library.yaml`` when installed)."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "library.yaml").is_file():
            return parent
    # Fallback: four levels up from mas/library/kg/__init__.py → repo root
    return here.parents[3]

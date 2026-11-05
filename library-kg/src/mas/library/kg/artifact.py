#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""KGArtifact — the canonical data container for Knowledge Graph data.

A ``KGArtifact`` holds a KG as nodes + edges + metadata.  It is the primary
unit exchanged between library-kg steps: steps accept one or more artifacts as
input and return one or more artifacts as output.

Serialization
-------------
* **JSON-LD / disk** — :meth:`KGArtifact.save` writes ``kg.jsonld``;
  :meth:`KGArtifact.from_file` reads it back.
* **Neo4j** — :meth:`KGArtifact.push_to_neo4j` pushes nodes and edges;
  :meth:`KGArtifact.fetch_from_neo4j` reconstructs from Neo4j.

Streaming
---------
Use :func:`stream_artifacts` to lazily load many KG files without holding all
of them in memory at once.

Quick start::

    from mas.library.kg.artifact import KGArtifact

    # Load from disk
    art = KGArtifact.from_file("run_001/kg.jsonld")
    print(art.node_count, art.edge_count)

    # Push to Neo4j
    art.push_to_neo4j(uri="bolt://localhost:7687",
                      username="neo4j", password="pw")

    # Fetch back
    art2 = KGArtifact.fetch_from_neo4j(session_id=art.session_id())

    # Stream many files
    for a in stream_artifacts(["run1/kg.jsonld", "run2/kg.jsonld"]):
        print(a.run_id())
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from mas.library.kg.core.oxp_models import mas_namespace

logger = logging.getLogger(__name__)

__all__ = ["KGArtifact", "stream_artifacts"]

_MAS_NS = mas_namespace()
_MASKG_NS = "https://ioc-project.github.io/scope-ontology/kg#"
_KG_ARTIFACT_NS = "https://ioc-project.github.io/scope-ontology/kg-artifact#"


@dataclass
class KGArtifact:
    """A Knowledge Graph artifact: nodes, edges, and metadata.

    This is the primary data type exchanged between library-kg step functions.
    It serialises to/from ``kg.jsonld`` on disk and to/from Neo4j.

    Attributes:
        nodes:    List of KG node dicts.
        edges:    List of KG edge dicts.
        metadata: Free-form metadata dict (``run_id``, ``created_at``, etc.).
    """

    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_doc(cls, doc: Dict[str, Any]) -> "KGArtifact":
        """Build a KGArtifact from a Python KG payload.

        Expected shape: ``{"nodes": [...], "edges": [...], "metadata": {...}}``.
        """
        if "nodes" not in doc or "edges" not in doc:
            raise ValueError(
                "KGArtifact.from_doc expects a JSON-LD artifact payload with nodes and edges"
            )
        return cls(
            nodes=list(doc.get("nodes") or []),
            edges=list(doc.get("edges") or []),
            metadata=dict(doc.get("metadata") or {}),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "KGArtifact":
        """Load a ``KGArtifact`` from ``kg.jsonld`` on disk."""
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        if p.suffix.lower() != ".jsonld":
            raise ValueError(f"KGArtifact.from_file only accepts .jsonld files, got: {p}")
        doc = json.loads(p.read_text(encoding="utf-8"))
        art = cls.from_doc(doc)
        art.metadata.setdefault("_source_path", str(p))
        return art

    @classmethod
    def empty(cls) -> "KGArtifact":
        """Return an empty artifact (no nodes or edges)."""
        return cls()

    # ------------------------------------------------------------------
    # Serialisation — JSON / disk
    # ------------------------------------------------------------------

    def to_doc(self) -> Dict[str, Any]:
        """Return a plain ``{"nodes": ..., "edges": ..., "metadata": ...}`` dict."""
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "metadata": self.metadata,
        }

    def to_jsonld_doc(self) -> Dict[str, Any]:
        """Return a JSON-LD document with the canonical KG payload.

        The payload remains ``nodes``/``edges``/``metadata`` for compatibility,
        while ``@context`` makes it a JSON-LD artifact for ontology-backed
        processing.
        """
        doc = self.to_doc()
        return {
            "@context": {
                "@vocab": _KG_ARTIFACT_NS,
                "mas": _MAS_NS,
                "maskg": _MASKG_NS,
            },
            **doc,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise to canonical JSON-LD."""
        return self.to_jsonld(indent=indent)

    def to_jsonld(self, *, indent: int = 2) -> str:
        """Serialise to canonical JSON-LD."""
        return json.dumps(self.to_jsonld_doc(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> Path:
        """Write the artifact to disk.

        Only ``*.jsonld`` paths are accepted.

        Creates parent directories if needed.

        Args:
            path: Destination file path.

        Returns:
            The resolved ``Path`` that was written.
        """
        p = Path(path).expanduser().resolve()
        if p.suffix.lower() != ".jsonld":
            raise ValueError(f"KGArtifact.save only accepts .jsonld paths, got: {p}")
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_jsonld()
        p.write_text(payload, encoding="utf-8")
        logger.debug("KGArtifact saved: %d nodes, %d edges → %s",
                     self.node_count, self.edge_count, p)
        return p

    # ------------------------------------------------------------------
    # Serialisation — Neo4j
    # ------------------------------------------------------------------

    def push_to_neo4j(
        self,
        *,
        uri: str,
        username: str,
        password: str,
        database: str = "neo4j",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Push this artifact to Neo4j.

        Delegates to :func:`mas.library.kg.neo4j.push_kg_to_neo4j`.
        All extra *kwargs* are forwarded (``app_name``, ``source``,
        ``batch_size``, ``clear_session``, ``ensure_indexes``, ``dry_run``, …).

        Returns:
            Summary dict with ``nodes``, ``edges``, ``rows`` counts.
        """
        from mas.library.kg.neo4j.push import push_kg_to_neo4j

        return push_kg_to_neo4j(
            self.to_doc(),
            uri=uri,
            username=username,
            password=password,
            database=database,
            **kwargs,
        )

    @classmethod
    def fetch_from_neo4j(
        cls,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        uri: str,
        username: str,
        password: str,
        database: str = "neo4j",
    ) -> "KGArtifact":
        """Fetch a session KG from Neo4j and return it as a KGArtifact.

        Exactly one of *session_id* or *run_id* must be provided.

        Returns:
            A :class:`KGArtifact` built from the Neo4j data.
        """
        from mas.library.kg.neo4j.dump import fetch_kg_from_neo4j

        doc = fetch_kg_from_neo4j(
            session_id=session_id,
            run_id=run_id,
            uri=uri,
            username=username,
            password=password,
            database=database,
        )
        return cls.from_doc(doc)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        """Number of nodes in this artifact."""
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        """Number of edges in this artifact."""
        return len(self.edges)

    def run_id(self) -> Optional[str]:
        """Return the run_id from metadata, or None."""
        return self.metadata.get("run_id") or self.metadata.get("runId") or None

    def session_id(self) -> Optional[str]:
        """Return the sessionId from the Session node, or None."""
        from mas.library.kg.neo4j.denormalize import session_id_from_nodes

        return session_id_from_nodes(self.nodes)

    def __repr__(self) -> str:
        return (
            f"KGArtifact(nodes={self.node_count}, edges={self.edge_count}, "
            f"run_id={self.run_id()!r})"
        )

    def __bool__(self) -> bool:
        """True if the artifact contains at least one node."""
        return bool(self.nodes)


# ------------------------------------------------------------------
# Streaming helpers
# ------------------------------------------------------------------


def stream_artifacts(paths: Iterable[str | Path]) -> Iterator[KGArtifact]:
    """Lazily load a sequence of ``kg.jsonld`` files as :class:`KGArtifact` objects.

    Files are loaded one at a time — no more than one artifact is held in
    memory at once.

    Args:
        paths: Iterable of file paths.

    Yields:
        :class:`KGArtifact` for each path.

    Example::

        for art in stream_artifacts(run_dir.glob("*/kg.jsonld")):
            print(art.run_id(), art.node_count)
    """
    for p in paths:
        yield KGArtifact.from_file(p)

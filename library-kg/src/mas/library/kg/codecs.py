#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""KG codecs — (artifact_kind, store_type) serialize/deserialize pairs.

A **codec** is the library's independent, registered converter between a KG
artifact and a store, invoked implicitly at step/infra boundaries (e.g. when a
``kg`` artifact is serialized to a ``neo4j`` infra target and back). It is the
counterpart of the *artifact* the library provides.

Registered codec:

* ``("kg", "neo4j")`` — :class:`Neo4jKGCodec`. ``encode`` pushes a KG document to
  Neo4j; ``decode`` fetches it back. All graph logic lives in
  :mod:`mas.library.kg.neo4j`; this codec is a thin boundary adapter.

Discovery is via this library's ``library.yaml`` manifest (a ``plugins:`` entry
of ``type: codec``), resolved lazily by ``mas.lab.benchmark.codecs.get_codec``.
Requires the bench framework (``[bench]`` extra); imports at module top so any
import problem surfaces when the codec is resolved.
"""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.codecs.base import Codec

from mas.library.kg.infra import Neo4jTarget
from mas.library.kg.neo4j import fetch_kg_from_neo4j, push_kg_to_neo4j

__all__ = ["Neo4jKGCodec"]


def _conn(store: Any) -> dict:
    """Resolve Neo4j connection kwargs from a DatastoreSpec (password from env).

    Delegates to ``Neo4jTarget``'s own env-var-fallback resolution (shared with
    the ``--infra`` manifest path elsewhere in this package) rather than
    reimplementing the same ``NEO4J_URI``/``NEO4J_USER``/``NEO4J_DB_AGENT``/
    ``NEO4J_PASSWORD`` fallback names and defaults by hand.
    """
    target = Neo4jTarget(
        uri=str(getattr(store, "uri", None) or ""),
        username=str(getattr(store, "user", None) or ""),
        database=str(getattr(store, "database", None) or ""),
        password=getattr(store, "password", None) or None,
    )
    return {
        "uri": target.resolved_uri(),
        "username": target.resolved_username(),
        "password": target.password_value(),
        "database": target.resolved_database(),
    }


class Neo4jKGCodec(Codec):
    """Serialize/deserialize a ``kg`` artifact to/from a ``neo4j`` store."""

    artifact_kind: str = "kg"
    store_type: str = "neo4j"

    def encode(self, artifact: Any, **opts: Any) -> None:
        doc = artifact.to_doc() if hasattr(artifact, "to_doc") else artifact
        if not isinstance(doc, dict):
            raise TypeError(
                f"Neo4jKGCodec.encode: expected a KG document/artifact, got {type(artifact)!r}"
            )
        push_kg_to_neo4j(
            doc,
            **_conn(self.store),
            batch_size=int(opts.get("batch_size", 200)),
            app_name=str(opts.get("app_name", "")),
            source=str(opts.get("source", "mas-lab")),
            annotations=opts.get("annotations"),
            clear_session=bool(opts.get("clear_run", False)),
            ensure_indexes=bool(opts.get("ensure_indexes", True)),
        )

    def decode(self, **opts: Any) -> dict[str, Any]:
        session_id = opts.get("session_id")
        run_id = opts.get("run_id")
        if not session_id and not run_id:
            raise ValueError("Neo4jKGCodec.decode: provide session_id or run_id in opts.")
        return fetch_kg_from_neo4j(session_id=session_id, run_id=run_id, **_conn(self.store))

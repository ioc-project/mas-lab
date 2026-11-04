#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Neo4j integration for the knowledge-graph library.

Requires the ``neo4j`` Python driver (optional dep)::

    this library's ``neo4j`` extra

Public API
----------
``push_kg_to_neo4j``          — push a kg.jsonld document to Neo4j
``push_annotations_to_neo4j`` — push an annotation KG (dangling-node refs OK)
``execute_merge``             — push pre-parsed node/edge lists to Neo4j
``fetch_kg_from_neo4j``       — fetch a KG by session_id or run_id
``denormalize``               — propagate session-scoped attributes before push
``session_id_from_nodes``     — extract sessionId from a list of KG nodes
``build_merge_statements``    — dry-run Cypher display (no driver needed)
"""

from mas.library.kg.neo4j.denormalize import denormalize, session_id_from_nodes
from mas.library.kg.neo4j.dump import fetch_kg_from_neo4j
from mas.library.kg.neo4j.push import (
    build_merge_statements,
    execute_merge,
    push_annotations_to_neo4j,
    push_kg_to_neo4j,
)

__all__ = [
    "push_kg_to_neo4j",
    "push_annotations_to_neo4j",
    "execute_merge",
    "fetch_kg_from_neo4j",
    "build_merge_statements",
    "denormalize",
    "session_id_from_nodes",
]

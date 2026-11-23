#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: fetch a KG from Neo4j and return a KGArtifact."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)


def run_neo4j_dump(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    output_path: Optional[Union[str, Path]] = None,
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password: object = None,
    database: Optional[str] = None,
) -> KGArtifact:
    """Fetch a session KG from Neo4j and return it as a KGArtifact.

    Connection params fall back to host values
    (``NEO4J_URI``, ``NEO4J_USER``, ``NEO4J_DB_AGENT``). The password is a
    SecretRef (required unless optional); none declared means no password.

    Args:
        session_id: Session ID to retrieve (preferred over run_id).
        run_id: Run ID to retrieve (used when session_id is absent).
        output_path: If given, also save the KGArtifact to this path.
        uri: Bolt URI.
        username: Neo4j username.
        password: SecretRef for the password (none: no password).
        database: Target database name.

    Returns:
        The fetched KGArtifact.

    Raises:
        ValueError: if neither session_id nor run_id is given.
    """
    from mas.library.kg.neo4j.dump import fetch_kg_from_neo4j

    if not session_id and not run_id:
        raise ValueError("Either session_id or run_id must be provided")

    from mas.library.kg.steps.neo4j_push import _resolve_conn

    conn = _resolve_conn(uri, username, password, database)
    logger.info(
        "neo4j_dump: fetching session_id=%s run_id=%s from %s", session_id, run_id, conn["uri"]
    )
    doc = fetch_kg_from_neo4j(session_id=session_id, run_id=run_id, **conn)

    artifact = KGArtifact.from_doc(doc)

    logger.info(
        "neo4j_dump: fetched %d nodes, %d edges",
        artifact.node_count,
        artifact.edge_count,
    )

    if output_path:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        artifact.save(out)
        logger.info("neo4j_dump: wrote KG → %s", out)

    return artifact

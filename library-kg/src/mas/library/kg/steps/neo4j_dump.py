#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: fetch a KG from Neo4j and return a KGArtifact."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from mas.runtime import hostenv

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)

_ENV_URI = "NEO4J_URI"
_ENV_USER = "NEO4J_USER"
_ENV_PASSWORD = "NEO4J_PASSWORD"
_ENV_DATABASE = "NEO4J_DB_AGENT"


def run_neo4j_dump(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    output_path: Optional[Union[str, Path]] = None,
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    password_env: Optional[str] = None,
    database: Optional[str] = None,
) -> KGArtifact:
    """Fetch a session KG from Neo4j and return it as a KGArtifact.

    Connection params fall back to environment variables
    (``NEO4J_URI``, ``NEO4J_USER``, ``NEO4J_PASSWORD``, ``NEO4J_DB_AGENT``).

    Args:
        session_id: Session ID to retrieve (preferred over run_id).
        run_id: Run ID to retrieve (used when session_id is absent).
        output_path: If given, also save the KGArtifact to this path.
        uri: Bolt URI.
        username: Neo4j username.
        password: Literal password (prefer ``password_env``).
        password_env: Name of the env var holding the password.
        database: Target database name.

    Returns:
        The fetched KGArtifact.

    Raises:
        ValueError: if neither session_id nor run_id is given.
    """
    from mas.library.kg.neo4j.dump import fetch_kg_from_neo4j

    if not session_id and not run_id:
        raise ValueError("Either session_id or run_id must be provided")

    resolved_uri = uri or hostenv.get(_ENV_URI, "bolt://localhost:7687")
    resolved_user = username or hostenv.get(_ENV_USER, "neo4j")
    resolved_password = password or hostenv.get(password_env or _ENV_PASSWORD, "")
    resolved_database = database or hostenv.get(_ENV_DATABASE, "neo4j")

    logger.info(
        "neo4j_dump: fetching session_id=%s run_id=%s from %s",
        session_id,
        run_id,
        resolved_uri,
    )

    doc = fetch_kg_from_neo4j(
        session_id=session_id,
        run_id=run_id,
        uri=resolved_uri,
        username=resolved_user,
        password=resolved_password,
        database=resolved_database,
    )

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

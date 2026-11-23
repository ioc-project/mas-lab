#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: push kg.jsonld to Neo4j."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mas.runtime import hostenv

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)

_ENV_URI = "NEO4J_URI"
_ENV_USER = "NEO4J_USER"
_ENV_PASSWORD = "NEO4J_PASSWORD"
_ENV_DATABASE = "NEO4J_DB_AGENT"


def _as_artifact(inp: "KGArtifact | str | Path") -> KGArtifact:
    if isinstance(inp, KGArtifact):
        return inp
    return KGArtifact.from_file(inp)


def _resolve_conn(
    uri: Optional[str],
    username: Optional[str],
    password: Optional[str],
    password_env: Optional[str],
    database: Optional[str],
) -> Dict[str, str]:
    """Resolve Neo4j connection params, falling back to environment variables."""
    return {
        "uri": uri or hostenv.get(_ENV_URI, "bolt://localhost:7687"),
        "username": username or hostenv.get(_ENV_USER, "neo4j"),
        "password": password or hostenv.get(password_env or _ENV_PASSWORD, ""),
        "database": database or hostenv.get(_ENV_DATABASE, "neo4j"),
    }


def run_neo4j_push(
    artifact: Union[KGArtifact, str, Path],
    *,
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    password_env: Optional[str] = None,
    database: Optional[str] = None,
    batch_size: int = 200,
    app_name: str = "",
    source: str = "mas-lab",
    annotations: Optional[Dict[str, Any]] = None,
    extension_layers: Optional[List[str]] = None,
    clear_session: bool = False,
    ensure_indexes: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Push a KG to Neo4j.

    Connection params fall back to environment variables
    (``NEO4J_URI``, ``NEO4J_USER``, ``NEO4J_PASSWORD``, ``NEO4J_DB_AGENT``).

    Args:
        artifact: Input KG. Accepts a KGArtifact, a path string, or a Path
            object.
        uri: Bolt URI.
        username: Neo4j username.
        password: Literal password (prefer ``password_env``).
        password_env: Name of the env var holding the password.
        database: Target database name.
        batch_size: UNWIND batch size.
        app_name: Written as ``appName`` on every element.
        source: Data-origin marker.
        annotations: Extra attributes merged onto every element.
        extension_layers: Optional enabled extension layers (for example:
            ``["experiment"]``).
        clear_session: Delete existing nodes for this session before pushing.
        ensure_indexes: Create covering indexes before writing.
        dry_run: Build Cypher but do NOT connect to Neo4j.

    Returns:
        Summary dict: ``{"rows", "nodes", "edges", "uri", ...}``
    """
    from mas.library.kg.neo4j.push import push_kg_to_neo4j

    input_was_path = not isinstance(artifact, KGArtifact)
    input_path_obj = Path(artifact).expanduser().resolve() if input_was_path else None

    kg = _as_artifact(artifact)

    conn = _resolve_conn(uri, username, password, password_env, database)
    logger.info(
        "neo4j_push: %s → %s  db=%s  dry_run=%s",
        input_path_obj if input_path_obj else repr(artifact),
        conn["uri"],
        conn["database"],
        dry_run,
    )

    result = push_kg_to_neo4j(
        kg.to_doc(),
        uri=conn["uri"],
        username=conn["username"],
        password=conn["password"],
        database=conn["database"],
        batch_size=batch_size,
        app_name=app_name,
        source=source,
        annotations=annotations,
        extension_layers=extension_layers,
        clear_session=clear_session,
        ensure_indexes=ensure_indexes,
        dry_run=dry_run,
    )

    result["dry_run"] = bool(dry_run)
    if "nodes" in result and "node_count" not in result:
        result["node_count"] = int(result["nodes"])
    if "edges" in result and "edge_count" not in result:
        result["edge_count"] = int(result["edges"])

    if input_was_path:
        result["kg_path"] = str(input_path_obj)

    return result

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fetch a KG from Neo4j and return a ``kg.jsonld``-compatible dict.

Public API
----------
``fetch_kg_from_neo4j(*, session_id, run_id, uri, username, password, database)``
    Query Neo4j for all nodes and edges belonging to a session or run and
    return a ``{"nodes": [...], "edges": [...], "metadata": {...}}`` dict.

Requires: ``neo4j`` Python driver (optional dep).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mas.library.kg._dist import extra_hint
from mas.library.kg.neo4j.denormalize import _infer_app_name
from mas.library.kg.neo4j.push import _deserialize_neo4j_props as _deserialize_props

logger = logging.getLogger(__name__)

# Label applied to every KG node by the push step — always present alongside
# the semantic label (e.g. AgentCall, Session).  We must skip it when
# resolving node_type from the node's label set.
_SECONDARY_LABEL = "KGNode"


def _semantic_label(labels: frozenset) -> str:
    """Return the semantic node type from a Neo4j label set.

    Every KG node is labelled with both a semantic label (e.g. ``AgentCall``)
    and the shared secondary label ``KGNode`` (used for cross-label indexing).
    This function returns the first non-``KGNode`` label.  If the node only
    has the secondary label (unusual), ``KGNode`` is returned as a fallback.

    ``frozenset`` ordering is undefined, so we must not rely on positional
    access — iterate and prefer the semantic label explicitly.
    """
    for lbl in labels:
        if lbl != _SECONDARY_LABEL:
            return lbl
    return next(iter(labels), "Unknown")


def _fetch_nodes_and_edges(
    neo4j_session: Any,
    *,
    session_id: Optional[str],
    run_id: Optional[str],
) -> Dict[str, Any]:
    """Execute Cypher and return deserialized ``{"nodes": [...], "edges": [...]}``.

    The *neo4j_session* is already bound to the target database; no ``database``
    parameter is needed here.
    """
    if session_id:
        match_clause = "{sessionId: $sid}"
        params: Dict[str, str] = {"sid": session_id}
    elif run_id:
        match_clause = "{runId: $rid}"
        params = {"rid": run_id}
    else:
        raise ValueError("Either session_id or run_id must be provided")

    # Nodes — use camelCase property names as stored by the push step.
    node_result = neo4j_session.run(
        f"MATCH (n {match_clause}) RETURN n",
        **params,
    )
    nodes: List[Dict[str, Any]] = []
    node_by_id: Dict[str, Dict[str, Any]] = {}
    for record in node_result:
        n = record["n"]
        props = _deserialize_props(dict(n.items()))
        # Prefer the semantic label; skip the shared KGNode index label.
        props["node_type"] = _semantic_label(n.labels) if n.labels else "Unknown"
        nodes.append(props)
        node_id = str(props.get("id") or "")
        if node_id:
            node_by_id[node_id] = props

    # Edges — both endpoints must belong to this session/run.
    edge_result = neo4j_session.run(
        f"""
        MATCH (a {match_clause})-[r]->(b {match_clause})
        RETURN a.id AS from_id, b.id AS to_id, type(r) AS edge_type,
               properties(r) AS edge_props
        """,
        **params,
    )
    edges: List[Dict[str, Any]] = []
    for record in edge_result:
        edge: Dict[str, Any] = {
            "edge_type": record["edge_type"],
            "from_id": record["from_id"],
            "to_id": record["to_id"],
        }
        extra = _deserialize_props(dict(record["edge_props"] or {}))
        edge.update(extra)
        edges.append(edge)

    # Backfill Transition scalar fields when a graph stores them primarily
    # as relationships (fromState/toState/realizes).
    for edge in edges:
        transition_id = str(edge.get("from_id") or "")
        transition = node_by_id.get(transition_id)
        if not transition or transition.get("node_type") != "Transition":
            continue
        edge_type = edge.get("edge_type")
        target_id = str(edge.get("to_id") or "")
        if edge_type == "fromState" and target_id and not transition.get("fromState"):
            transition["fromState"] = target_id
        elif edge_type == "toState" and target_id and not transition.get("toState"):
            transition["toState"] = target_id
        elif edge_type == "realizes" and target_id and not transition.get("realizesCallId"):
            transition["realizesCallId"] = target_id

    # Ensure appName is present in dump output. Prefer explicit Session.appName,
    # then infer from hierarchical sessionId (shared with neo4j/denormalize.py's
    # pre-push pass), then fall back to any node appName.
    session_node = next((n for n in nodes if n.get("node_type") == "Session"), None)
    inferred_app_name = ""
    if session_node is not None:
        sid = str(session_node.get("sessionId") or session_node.get("id") or "").strip()
        inferred_app_name = _infer_app_name(str(session_node.get("appName") or ""), sid)
    if not inferred_app_name:
        for n in nodes:
            app_name = str(n.get("appName") or "").strip()
            if app_name:
                inferred_app_name = app_name
                break

    if inferred_app_name:
        for n in nodes:
            if not str(n.get("appName") or "").strip():
                n["appName"] = inferred_app_name
        for e in edges:
            if not str(e.get("appName") or "").strip():
                e["appName"] = inferred_app_name

    return {"nodes": nodes, "edges": edges}


def fetch_kg_from_neo4j(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    uri: str = "bolt://localhost:7687",
    username: str = "neo4j",
    password: str = "",
    database: str = "neo4j",
) -> Dict[str, Any]:
    """Fetch a KG from Neo4j and return a ``kg.jsonld``-compatible dict.

    Queries by ``sessionId`` (preferred) or ``runId``.

    Args:
        session_id: Session ID to retrieve.
        run_id: Run ID to retrieve (used when session_id is absent).
        uri: Neo4j Bolt URI.
        username: Neo4j username.
        credential (str): Auth credential for *username*.
        database: Target database name.

    Returns:
        ``{"nodes": [...], "edges": [...], "metadata": {...}}`` where
        ``metadata`` includes ``session_id``, ``run_id``, ``node_count``,
        ``edge_count``, ``source``, ``uri``, and ``database``.

    Raises:
        ValueError: if neither *session_id* nor *run_id* is given.
        ImportError: if the ``neo4j`` driver package is not installed.
    """
    try:
        from neo4j import GraphDatabase  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            f"neo4j driver not installed. Install with: {extra_hint('neo4j')}"
        ) from exc

    if not session_id and not run_id:
        raise ValueError("Either session_id or run_id must be provided")

    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as neo4j_session:
            result = _fetch_nodes_and_edges(
                neo4j_session,
                session_id=session_id,
                run_id=run_id,
            )
    finally:
        driver.close()

    result["metadata"] = {
        "session_id": session_id,
        "run_id": run_id,
        "node_count": len(result["nodes"]),
        "edge_count": len(result["edges"]),
        "source": "neo4j",
        "uri": uri,
        "database": database,
    }

    logger.info(
        "fetch_kg_from_neo4j: %d nodes, %d edges (session_id=%s, run_id=%s)",
        len(result["nodes"]),
        len(result["edges"]),
        session_id,
        run_id,
    )
    return result

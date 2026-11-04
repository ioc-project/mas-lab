#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Push a KG document to Neo4j using parameterized UNWIND MERGEs.

Public API
----------
``push_kg_to_neo4j(doc, *, uri, username, password, database, ...)``
    Accepts a ``kg.jsonld`` dict and pushes nodes + edges to Neo4j.
    Calls :func:`~.denormalize.denormalize` before writing so every node
    and edge carries ``sessionId``, ``appName``, ``source``, and ``block``.

``build_merge_statements(nodes, edges)``
    Returns display-only Cypher strings — suitable for dry-run output and
    unit tests. Not used for actual writes.

``execute_merge(nodes, edges, *, uri, ...)``
    Low-level: push pre-supplied node/edge lists directly (skips kg.jsonld
    parsing). Used by ``Neo4jPushStep`` in mas-lab-graph.

Requires: ``neo4j`` Python driver (``pip install neo4j``).
``neo4j`` is an optional dependency — import error is deferred to call time.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mas.library.kg._dist import extra_hint
from mas.library.kg.core.oxp_models import NATIVE_EXTENSION_NODE_TYPES, OXP_CORE_NODE_TYPES

_KNOWN_NODE_TYPES = OXP_CORE_NODE_TYPES | NATIVE_EXTENSION_NODE_TYPES

logger = logging.getLogger(__name__)

# Types that can be stored as plain Neo4j properties.
_SCALAR = (str, int, float, bool)

# 1:1 role -> label fallbacks for edge endpoints not present in the current
# push batch (annotation cross-references into an existing graph -- see
# push_annotations_to_neo4j). Only roles with exactly one possible concrete
# class are listed; "call" is deliberately absent (AgentCall/LLMCall/
# ToolCall/... are all "call" and cannot be disambiguated from the role
# alone), so those endpoints fall back to an unlabeled match instead of
# guessing a class.
_ROLE_TO_LABEL = {
    "session": "Session",
    "run": "Run",
    "agent": "Agent",
    "state": "State",
    "transition": "Transition",
}


def _endpoint_pattern(var: str, node_id: str, id_to_type: Dict[str, str], role: str = "") -> str:
    """Cypher node pattern for an edge endpoint, labeled with its real,
    most-specific type when known.

    Never falls back to a shared generic label: class hierarchy belongs in
    the ontology, not in the data. When the type genuinely can't be
    determined (an annotation edge referencing a node outside this push
    batch, with an ambiguous role like "call"), the pattern is left
    unlabeled -- matched by id alone.
    """
    label = id_to_type.get(node_id) or _ROLE_TO_LABEL.get(role, "")
    if label:
        return f"({var}:{label} {{id: {json.dumps(node_id)}}})"
    return f"({var} {{id: {json.dumps(node_id)}}})"


# JSON sentinel prefix used when serializing dicts/lists into Neo4j string props.
_JSON_PREFIX = "__json__:"


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------


def _neo4j_props(d: Dict[str, Any]) -> Dict[str, Any]:
    """Return Neo4j-safe properties.

    * Scalar values stored as-is.
    * Dicts and lists JSON-serialized with ``__json__:`` prefix so the dump
      step can round-trip them.
    * None and non-serializable types silently dropped.
    """
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, _SCALAR):
            out[k] = v
        elif isinstance(v, (dict, list)):
            try:
                out[k] = _JSON_PREFIX + json.dumps(v, ensure_ascii=False)
            except (TypeError, ValueError):
                pass
    return out


def _deserialize_neo4j_props(props: Dict[str, Any]) -> Dict[str, Any]:
    """Inverse of ``_neo4j_props`` — restore dict/list values from ``__json__:`` strings."""
    out: Dict[str, Any] = {}
    for k, v in props.items():
        if isinstance(v, str) and v.startswith(_JSON_PREFIX):
            try:
                out[k] = json.loads(v[len(_JSON_PREFIX) :])
            except (ValueError, TypeError):
                out[k] = v
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Dry-run display helpers (string-interpolated — NOT used for actual writes)
# ---------------------------------------------------------------------------


def _index_nodes_by_type(
    nodes: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    """Group *nodes* by node_type, and map each node's id to that type.

    The id->type map lets edge MATCH clauses resolve each endpoint's real,
    most-specific label (see ``_endpoint_pattern``) without a second pass
    over ``nodes``.
    """
    nodes_by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    id_to_type: Dict[str, str] = {}
    unknown_types: set[str] = set()
    for n in nodes:
        node_type = n.get("node_type", "ExecutionElement")
        if node_type not in _KNOWN_NODE_TYPES:
            unknown_types.add(node_type)
        nodes_by_type[node_type].append(n)
        nid = n.get("id")
        if nid:
            id_to_type[str(nid)] = node_type
    if unknown_types:
        # node_type becomes a Neo4j label verbatim (see _node_unwind_batches)
        # -- an unrecognized one is either a real new node type this list
        # needs to catch up with, or a typo silently creating a stray label.
        # Warn, don't block: a push must still succeed for a legitimate new
        # type this check doesn't know about yet.
        logger.warning(
            "Pushing node_type(s) not in OXP_CORE_NODE_TYPES/NATIVE_EXTENSION_NODE_TYPES: %s",
            sorted(unknown_types),
        )
    return nodes_by_type, id_to_type


def build_merge_statements(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[str]:
    """Return Cypher MERGE statements for dry-run display or unit tests."""
    nodes_by_type, id_to_type = _index_nodes_by_type(nodes)

    stmts: List[str] = []
    for node_type, nlist in nodes_by_type.items():
        for node in nlist:
            nid = node.get("id")
            if not nid:
                continue
            props = _neo4j_props({k: v for k, v in node.items() if k != "node_type"})
            props_str = ", ".join(f"n.{k} = {json.dumps(v)}" for k, v in props.items())
            stmts.append(
                f"MERGE (n:{node_type} {{id: {json.dumps(nid)}}})"
                + (f" SET {props_str}" if props_str else "")
            )
    for edge in edges:
        et = edge["edge_type"]
        from_id = str(edge.get("from_id") or edge.get("source") or "")
        to_id = str(edge.get("to_id") or edge.get("target") or "")
        if not from_id or not to_id:
            continue
        _skip = {"edge_type", "from_id", "to_id", "source", "target", "from_type", "to_type"}
        extra = {k: v for k, v in edge.items() if k not in _skip and isinstance(v, _SCALAR)}
        rel_props = (
            " {" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in extra.items()) + "}"
            if extra
            else ""
        )
        a_pattern = _endpoint_pattern("a", from_id, id_to_type, str(edge.get("from_type") or ""))
        b_pattern = _endpoint_pattern("b", to_id, id_to_type, str(edge.get("to_type") or ""))
        stmts.append(f"MATCH {a_pattern} MATCH {b_pattern} MERGE (a)-[:{et}{rel_props}]->(b)")
    return stmts


# ---------------------------------------------------------------------------
# Parameterized UNWIND batches (used for actual writes)
# ---------------------------------------------------------------------------

_NodeBatch = List[Tuple[str, Dict[str, Any]]]
_EdgeBatch = List[Tuple[str, Dict[str, Any]]]


def _node_unwind_batches(
    nodes_by_type: Dict[str, List[Dict[str, Any]]],
    batch_size: int = 200,
) -> _NodeBatch:
    batches: _NodeBatch = []
    for node_type, nodes in nodes_by_type.items():
        rows = []
        for node in nodes:
            nid = node.get("id")
            if not nid:
                continue
            props = _neo4j_props({k: v for k, v in node.items() if k != "node_type"})
            rows.append({"__id": nid, "__props": props})
        if not rows:
            continue
        cypher = (
            f"UNWIND $rows AS row\nMERGE (n:{node_type} {{id: row.__id}})\nSET n += row.__props"
        )
        for i in range(0, len(rows), batch_size):
            batches.append((cypher, {"rows": rows[i : i + batch_size]}))
    return batches


def _edge_unwind_batches(
    edges: List[Dict[str, Any]],
    id_to_type: Dict[str, str],
    batch_size: int = 200,
) -> _EdgeBatch:
    """Group edges by (edge_type, from_label, to_label) so each batch's MATCH
    clauses can use the endpoints' real, most-specific labels -- resolved
    from *id_to_type* (nodes in this same push) with the unambiguous-role
    fallback in ``_endpoint_pattern`` for endpoints outside this batch
    (annotation cross-references). No shared generic label is ever written
    or matched on.
    """
    _SKIP = frozenset({"edge_type", "from_id", "to_id", "source", "target", "from_type", "to_type"})
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        et = edge["edge_type"]
        from_id = str(edge.get("from_id") or edge.get("source") or "")
        to_id = str(edge.get("to_id") or edge.get("target") or "")
        if not from_id or not to_id:
            continue
        extra = _neo4j_props({k: v for k, v in edge.items() if k not in _SKIP})
        from_label = id_to_type.get(from_id) or _ROLE_TO_LABEL.get(
            str(edge.get("from_type") or ""), ""
        )
        to_label = id_to_type.get(to_id) or _ROLE_TO_LABEL.get(str(edge.get("to_type") or ""), "")
        groups[(et, from_label, to_label)].append(
            {"from_id": from_id, "to_id": to_id, "props": extra}
        )

    batches: _EdgeBatch = []
    for (et, from_label, to_label), rows in groups.items():
        # CRITICAL FIX: Use MERGE instead of MATCH for edge endpoints to avoid silent edge deletion
        # when nodes don't exist (e.g., annotation cross-references, partial pushes).
        # MERGE will create missing endpoint nodes with minimal properties (id + label only).
        # This ensures referential integrity - edges are never silently dropped.
        a_pattern = (
            f"(a:{from_label} {{id: row.from_id}})" if from_label else "(a {{id: row.from_id}})"
        )
        b_pattern = f"(b:{to_label} {{id: row.to_id}})" if to_label else "(b {{id: row.to_id}})"
        cypher = (
            f"UNWIND $rows AS row\n"
            f"MERGE {a_pattern}\n"
            f"MERGE {b_pattern}\n"
            f"MERGE (a)-[r:{et}]->(b)\n"
            f"SET r += row.props"
        )
        for i in range(0, len(rows), batch_size):
            batches.append((cypher, {"rows": rows[i : i + batch_size]}))
    return batches


# ---------------------------------------------------------------------------
# _Neo4jWriter — lazy Neo4j driver wrapper
# ---------------------------------------------------------------------------


class _Neo4jWriter:
    """Thin wrapper around the Neo4j Python driver."""

    def __init__(self, uri: str, username: str, password: str, database: str) -> None:
        try:
            from neo4j import GraphDatabase  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                f"neo4j driver not installed. Install with: {extra_hint('neo4j')}"
            ) from exc
        self._driver = GraphDatabase.driver(uri, auth=(username, password))
        self._database = database

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "_Neo4jWriter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def run_unwind_batches(
        self,
        node_batches: _NodeBatch,
        edge_batches: _EdgeBatch,
        *,
        single_transaction: bool = True,
    ) -> int:
        """Execute UNWIND batches. Returns total row count written."""
        total_rows = 0
        all_batches = list(node_batches) + list(edge_batches)

        if single_transaction:

            def _write_all(tx: Any) -> None:
                for cypher, params in all_batches:
                    tx.run(cypher, **params)

            with self._driver.session(database=self._database) as session:
                session.execute_write(_write_all)
            for _, params in all_batches:
                total_rows += len(params["rows"])
        else:
            with self._driver.session(database=self._database) as session:
                for cypher, params in all_batches:
                    session.execute_write(lambda tx, c=cypher, p=params: tx.run(c, **p))
                    total_rows += len(params["rows"])
        return total_rows

    def delete_session(self, session_id: str) -> int:
        """Delete all nodes and relationships tagged with sessionId."""
        with self._driver.session(database=self._database) as session:
            result = session.run(
                "MATCH (n {sessionId: $sid}) DETACH DELETE n RETURN count(n) AS cnt",
                sid=session_id,
            )
            cnt = result.single(strict=False)
            return int(cnt["cnt"]) if cnt else 0

    def delete_run(self, run_id: str) -> int:
        """Delete all nodes and relationships tagged with runId."""
        with self._driver.session(database=self._database) as session:
            result = session.run(
                "MATCH (n {runId: $rid}) DETACH DELETE n RETURN count(n) AS cnt",
                rid=run_id,
            )
            cnt = result.single(strict=False)
            return int(cnt["cnt"]) if cnt else 0

    def ensure_indexes(self) -> None:
        """Create covering indexes (idempotent). Safe to call on every push."""
        _LABELS = [
            "Session",
            "Run",
            "Agent",
            "AgentCall",
            "LLMCall",
            "ToolCall",
            "ProcessingCall",
            "WorkerCall",
            "RoutingCall",
            "GovernanceEvent",
            "CallAnnotation",
            "ContextContribution",
            "State",
            "Transition",
        ]
        # One index per concrete label, not a shared generic-label index:
        # class hierarchy belongs in the ontology, not in the data, so no
        # node is ever given an extra label just to make lookups fast.
        stmts = [
            f"CREATE INDEX {lbl.lower()}_id IF NOT EXISTS FOR (n:{lbl}) ON (n.id)"
            for lbl in _LABELS
        ] + [
            f"CREATE INDEX {lbl.lower()}_sessionId IF NOT EXISTS FOR (n:{lbl}) ON (n.sessionId)"
            for lbl in _LABELS
        ]
        with self._driver.session(database=self._database) as session:
            for stmt in stmts:
                try:
                    session.run(stmt)
                except Exception:
                    # `IF NOT EXISTS` already makes a duplicate-index attempt
                    # a no-op at the database level, so anything reaching
                    # here is a genuine problem (permissions, an
                    # unsupported Neo4j version, a connection drop) worth
                    # surfacing -- not silently invisible at DEBUG. Index
                    # creation is still best-effort: a lookup running
                    # without an index is slow, not wrong, so this does not
                    # fail the push itself.
                    logger.warning("Index creation failed, continuing without it: %s", stmt[:60], exc_info=True)  # noqa: E501


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def execute_merge(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    uri: str = "bolt://localhost:7687",
    username: str = "neo4j",
    password: str = "",
    database: str = "neo4j",
    batch_size: int = 200,
    ensure_indexes: bool = True,
    app_name: str = "",
    source: str = "mas-lab",
    annotations: Optional[Dict[str, Any]] = None,
    extension_layers: Optional[Iterable[str]] = None,
    clear_session: bool = False,
    skip_denormalize: bool = False,
) -> Dict[str, Any]:
    """Push nodes + edges to Neo4j (low-level, pre-parsed lists).

    Applies :func:`~.denormalize.denormalize` before writing unless
    *skip_denormalize* is True (used for annotation KGs whose nodes should
    not receive session-scoped attributes).

    Returns a summary dict: ``{"rows": N, "nodes": N, "edges": N, "uri": ...}``.
    """
    if not skip_denormalize:
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes, edges = denormalize(
            nodes,
            edges,
            app_name=app_name,
            source=source,
            annotations=annotations,
            extension_layers=extension_layers,
        )

    nodes_by_type, id_to_type = _index_nodes_by_type(nodes)

    node_batches = _node_unwind_batches(dict(nodes_by_type), batch_size=batch_size)
    edge_batches = _edge_unwind_batches(edges, id_to_type, batch_size=batch_size)

    with _Neo4jWriter(uri, username, password, database) as writer:
        if clear_session:
            # Resolve sessionId for targeted delete
            session_id = ""
            for n in nodes:
                if n.get("node_type") == "Session":
                    session_id = str(n.get("sessionId") or n.get("id") or "")
                    break
            if session_id:
                deleted = writer.delete_session(session_id)
                logger.info("Cleared %d existing nodes for session %s", deleted, session_id)
        if ensure_indexes:
            writer.ensure_indexes()
        committed = writer.run_unwind_batches(node_batches, edge_batches)

    return {
        "rows": committed,
        "nodes": len(nodes),
        "edges": len(edges),
        "uri": uri,
    }


def push_kg_to_neo4j(
    doc: Dict[str, Any],
    *,
    uri: str = "bolt://localhost:7687",
    username: str = "neo4j",
    password: str = "",
    database: str = "neo4j",
    batch_size: int = 200,
    ensure_indexes: bool = True,
    app_name: str = "",
    source: str = "mas-lab",
    annotations: Optional[Dict[str, Any]] = None,
    extension_layers: Optional[Iterable[str]] = None,
    clear_session: bool = False,
    skip_denormalize: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Push a ``kg.jsonld`` document to Neo4j.

    Args:
        doc: KG document dict (``{"nodes": [...], "edges": [...], ...}``).
        uri: Bolt URI.
        username: Neo4j username.
        credential (str): Auth credential for *username*.
        database: Target database name.
        batch_size: UNWIND batch size.
        ensure_indexes: Create covering indexes before writing (idempotent).
        app_name: Written as ``appName`` on every node and edge.
        source: Data-origin marker (default: ``"mas-lab"``).
        annotations: Extra attributes merged onto every node and edge.
        extension_layers: Optional enabled extension layers forwarded to
            ``denormalize`` (e.g. ``["experiment"]``).
        clear_session: Delete existing nodes for this session before pushing.
        skip_denormalize: Skip the denormalize step (for annotation KGs whose
            nodes reference existing Neo4j nodes and should not receive
            session-scoped attributes).
        dry_run: Log and return Cypher strings without connecting to Neo4j.

    Returns:
        Summary dict with ``rows``, ``nodes``, ``edges``, ``uri`` keys.
        In dry_run mode, also includes ``statements`` (list of Cypher strings).
    """
    nodes: List[Dict[str, Any]] = doc.get("nodes") or []
    edges: List[Dict[str, Any]] = doc.get("edges") or []

    if dry_run:
        if skip_denormalize:
            d_nodes, d_edges = list(nodes), list(edges)
        else:
            from mas.library.kg.neo4j.denormalize import denormalize

            d_nodes, d_edges = denormalize(
                nodes,
                edges,
                app_name=app_name,
                source=source,
                annotations=annotations,
                extension_layers=extension_layers,
            )
        stmts = build_merge_statements(d_nodes, d_edges)
        logger.info(
            "dry_run: %d nodes, %d edges → %d Cypher statements",
            len(d_nodes),
            len(d_edges),
            len(stmts),
        )
        return {
            "rows": 0,
            "nodes": len(d_nodes),
            "edges": len(d_edges),
            "uri": uri,
            "statements": stmts,
        }

    return execute_merge(
        nodes,
        edges,
        uri=uri,
        username=username,
        password=password,
        database=database,
        batch_size=batch_size,
        ensure_indexes=ensure_indexes,
        app_name=app_name,
        source=source,
        annotations=annotations,
        extension_layers=extension_layers,
        clear_session=clear_session,
        skip_denormalize=skip_denormalize,
    )


def push_annotations_to_neo4j(
    annotations_doc: Dict[str, Any],
    *,
    uri: str = "bolt://localhost:7687",
    username: str = "neo4j",
    password: str = "",
    database: str = "neo4j",
    batch_size: int = 200,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Push a KG annotation document (with dangling node refs) to Neo4j.

    An annotation document is a kg.jsonld-shaped dict where some edge endpoints
    reference nodes that **already exist in Neo4j** but are NOT present in this
    doc.  The serializer skips denormalization and does not fail when edge
    endpoints are absent from the local node list.

    Typical use: pushing Metric nodes with ``hasMetric`` edges that reference
    existing ``Session`` nodes (resolved via the edge's ``to_type: "session"``
    role hint -- see ``_ROLE_TO_LABEL`` -- since the referenced node isn't in
    this doc's own node list).

    The annotation nodes are pushed with full MERGE semantics (idempotent).
    Edges also use ``MERGE`` on both endpoints (``_edge_unwind_batches``) —
    a referenced node that doesn't exist in Neo4j yet is created as a
    minimal stub (``id`` + label only), not skipped. This is deliberate:
    referential integrity (every edge has two real endpoint nodes) takes
    priority over silently dropping an edge whose target hasn't landed yet
    (e.g. a partial/out-of-order push). If the referenced node is pushed
    properly later, ``MERGE`` on its own full properties fills the stub in.

    Args:
        annotations_doc: kg.jsonld-shaped dict.  Nodes are new annotation nodes
            (e.g. Metric); edges may reference nodes not in this doc.
        uri, username, password, database: Neo4j connection params.
        batch_size: UNWIND batch size.
        dry_run: Print Cypher without writing.

    Returns:
        Summary dict: ``{nodes_pushed, edges_pushed, dry_run}``.
    """
    result = push_kg_to_neo4j(
        annotations_doc,
        uri=uri,
        username=username,
        password=password,
        database=database,
        batch_size=batch_size,
        skip_denormalize=True,
        dry_run=dry_run,
    )
    return {
        "nodes_pushed": result["nodes"],
        "edges_pushed": result["edges"],
        "dry_run": dry_run,
        "rows": result.get("rows", 0),
    }

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Denormalize a KG document before Neo4j push.

Propagates session-scoped attributes (``sessionId``, ``appName``, ``source``,
``block``) to every node and edge so any single node can be located, filtered,
or deleted without traversing edges.

Attributes propagated
---------------------
``sessionId``   — resolved from the Session node → every node and edge.
``appName``     — caller-supplied application identifier/name.
``source``      — data-origin marker (default: ``"mas-lab"``).
``block``       — ``"structural"`` for catalog/agent definitions shared across
                  sessions; ``"trajectory"`` for State/Transition nodes;
                  ``"execution"`` for all other ExecutionElement nodes.
``annotations`` — optional free-form dict merged onto every node and edge.

Why
---
* **DROP by session** — ``MATCH (n {sessionId: $sid}) DETACH DELETE n`` removes
  all nodes for one conversation without traversing edges.
* **Structural queries** — ``MATCH (n) WHERE n.appName = $app AND n.block = 'structural'``
  retrieves catalog nodes shared across sessions, regardless of their
  specific label (no shared generic label is written to make this a
  single-label lookup -- see neo4j/push.py's ``_endpoint_pattern``).
* **Debuggability** — Neo4j Browser / Bloom can filter by ``sessionId``
  across any label since every node carries it as a property.

The function is idempotent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE = "mas-lab"
_EXPERIMENT_LAYER = "experiment"

# Structural types — shared across sessions/runs; block="structural".
# "Agent"/"LLM"/"Tool"/"Skill" are the real catalog/definition node types
# (not "CatalogTool"/"CatalogModel"/"CatalogSkill", which don't exist
# anywhere in the ontology's vocabulary -- see core/oxp_models.py's
# SCOPE_CORE_NODE_TYPES / NATIVE_EXTENSION_NODE_TYPES).
_STRUCTURAL_TYPES = frozenset({"Agent", "LLM", "Tool", "Skill"})

# Trajectory types — normalized State/Transition; block="trajectory".
_TRAJECTORY_TYPES = frozenset({"State", "Transition"})


def _block_for(node_type: str) -> str:
    if node_type in _STRUCTURAL_TYPES:
        return "structural"
    if node_type in _TRAJECTORY_TYPES:
        return "trajectory"
    return "execution"


def _infer_app_name(app_name: str, session_id: str) -> str:
    """Return explicit app_name or infer it from hierarchical session_id."""
    explicit = str(app_name or "").strip()
    if explicit:
        return explicit

    sid = str(session_id or "").strip()
    if not sid or "/" not in sid:
        return ""
    return sid.split("/", 1)[0].strip()


def _experiment_annotations_from_session(session_id: str) -> Dict[str, Any]:
    """Derive extension facets from session_id hierarchy.

    Expected shape: ``{appName}/{experiment}/{scenario}/{testItem}/{runLabel}``.
    Partial session IDs produce partial annotations.
    """
    parts = [p for p in str(session_id or "").split("/") if p]
    if len(parts) < 2:
        return {}

    out: Dict[str, Any] = {}
    if len(parts) > 1:
        out["experiment"] = parts[1]
    if len(parts) > 2:
        out["scenario"] = parts[2]
    if len(parts) > 3:
        out["testItem"] = parts[3]
    if len(parts) > 4:
        out["runLabel"] = parts[4]
    return out


def denormalize(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    app_name: str = "",
    source: str = _DEFAULT_SOURCE,
    annotations: Optional[Dict[str, Any]] = None,
    extension_layers: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Propagate session-scoped attributes to all nodes and edges.

    Args:
        nodes: KG node list.
        edges: KG edge list.
        app_name: Application identifier written as ``appName`` on every element.
        source: Data-origin marker written as ``source`` on every element.
        annotations: Optional extra attributes merged onto every element.
        extension_layers: Optional enabled extension layers. Supported values:
            ``experiment`` (stamps experiment/scenario/testItem/runLabel from
            hierarchical session IDs).

    Returns:
        ``(nodes, edges)`` — new lists; originals are not mutated.
    """
    annotations = annotations or {}

    # Resolve sessionId from the Session node.
    session_id = ""
    for n in nodes:
        if n.get("node_type") == "Session":
            session_id = str(n.get("sessionId") or n.get("id") or "")
            break
    if not session_id:
        # Fall back to run_id if present
        for n in nodes:
            if n.get("run_id"):
                session_id = str(n["run_id"])
                break

    enabled_layers = {
        str(layer).strip().lower() for layer in (extension_layers or []) if str(layer).strip()
    }
    app_name = _infer_app_name(app_name, session_id)
    extension_annotations: Dict[str, Any] = {}
    if _EXPERIMENT_LAYER in enabled_layers:
        extension_annotations.update(_experiment_annotations_from_session(session_id))

    # Built-in fields take precedence over caller-supplied annotations so that
    # session scoping (sessionId, appName, source) is never silently overwritten.
    common = {
        **annotations,
        **extension_annotations,
        "sessionId": session_id,
        "appName": app_name,
        "source": source,
    }

    new_nodes = []
    for n in nodes:
        enriched = dict(n)
        enriched.update(common)
        enriched["block"] = n.get("block") or _block_for(str(n.get("node_type") or ""))
        if enriched.get("block") == "trajectory" and not enriched.get("layer"):
            enriched["layer"] = "normalized"
        new_nodes.append(enriched)

    new_edges = []
    for e in edges:
        enriched = dict(e)
        enriched.update(common)
        new_edges.append(enriched)

    return new_nodes, new_edges


def session_id_from_nodes(nodes: List[Dict[str, Any]]) -> Optional[str]:
    """Return the sessionId from the Session node in *nodes*, or None.

    Scans for the first node with ``node_type == "Session"`` and returns its
    ``sessionId`` (or ``id`` as fallback).  Returns ``None`` if no Session node
    is present or its identifier is empty.
    """
    for n in nodes:
        if n.get("node_type") == "Session":
            sid = str(n.get("sessionId") or n.get("id") or "")
            return sid if sid else None
    return None

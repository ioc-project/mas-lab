#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""KG query and facet layer.

Pure stdlib — no dependencies on mas.lab.* or any plotting code.

Public API
----------
KGIndex    — lightweight in-memory graph index (O(1) node/edge lookups).
FacetQuery — immutable filter spec for KG subgraph extraction.
KGSource   — applies a FacetQuery to a kg.jsonld dict and returns a filtered subgraph.
KGView     — faceted access to call records by call_type.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)

__all__ = ["KGIndex", "FacetQuery", "KGSource", "KGView"]

# ---------------------------------------------------------------------------
# Node-type → call_type alias (for FacetQuery.call_types convenience filter)
# ---------------------------------------------------------------------------
_CALL_NODE_TYPES: Set[str] = {
    "AgentCall",
    "LLMCall",
    "ToolCall",
    "SkillCall",
    "MemoryCall",
    "RAGQuery",
    "ProcessingCall",
    "ThinkingCall",
    "MITMCall",
    "MASCall",
}


# ---------------------------------------------------------------------------
# KGIndex — lightweight in-memory graph index
# ---------------------------------------------------------------------------


class KGIndex:
    """Lightweight in-memory index over a kg.jsonld ``{nodes, edges}`` dict.

    Pre-indexes nodes by id and type, and edges by source/target for O(1) lookups.
    No external dependencies.

    Usage::

        idx = KGIndex.from_doc(kg_doc)
        root = idx.find_root_agent_call()
        neighbors = idx.out_neighbors(root["id"], edge_type="hasAgentCall")
    """

    def __init__(self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
        self._nodes_by_id: Dict[str, Dict[str, Any]] = {}
        self._nodes_by_type: Dict[str, List[Dict[str, Any]]] = {}
        self._out_edges: Dict[str, List[Tuple[str, str]]] = {}  # id → [(edge_type, target_id)]
        self._in_edges: Dict[str, List[Tuple[str, str]]] = {}  # id → [(edge_type, source_id)]

        for n in nodes:
            nid = n.get("id", "")
            self._nodes_by_id[nid] = n
            ntype = n.get("node_type", "")
            self._nodes_by_type.setdefault(ntype, []).append(n)

        for e in edges:
            src = e.get("from_id") or e.get("source") or e.get("from") or ""
            tgt = e.get("to_id") or e.get("target") or e.get("to") or ""
            etype = e.get("edge_type") or e.get("type") or ""
            if src:
                self._out_edges.setdefault(src, []).append((etype, tgt))
            if tgt:
                self._in_edges.setdefault(tgt, []).append((etype, src))

    @classmethod
    def from_doc(cls, doc: Dict[str, Any]) -> "KGIndex":
        """Build a KGIndex from a raw kg.jsonld dict."""
        return cls(doc.get("nodes") or [], doc.get("edges") or [])

    # -- Typed accessors ----------------------------------------------------

    @property
    def session(self) -> Optional[Dict[str, Any]]:
        """Return the Session node, or None."""
        sessions = self._nodes_by_type.get("Session", [])
        return sessions[0] if sessions else None

    @property
    def agent_calls(self) -> List[Dict[str, Any]]:
        return self._nodes_by_type.get("AgentCall", [])

    @property
    def llm_calls(self) -> List[Dict[str, Any]]:
        return self._nodes_by_type.get("LLMCall", [])

    @property
    def tool_calls(self) -> List[Dict[str, Any]]:
        return self._nodes_by_type.get("ToolCall", [])

    @property
    def calls_by_time(self) -> List[Dict[str, Any]]:
        """All call nodes (Agent + LLM + Tool + Processing) sorted by startTime."""
        calls = [
            n
            for ntype in ("AgentCall", "LLMCall", "ToolCall", "ProcessingCall")
            for n in self._nodes_by_type.get(ntype, [])
        ]
        calls.sort(key=lambda n: float(n.get("startTime") or 0))
        return calls

    # -- Lookup methods ----------------------------------------------------

    def nodes_of_type(self, ntype: str) -> List[Dict[str, Any]]:
        """Return all nodes of a given node_type."""
        return self._nodes_by_type.get(ntype, [])

    def node(self, nid: str) -> Optional[Dict[str, Any]]:
        """Return the node with the given id, or None."""
        return self._nodes_by_id.get(nid)

    def out_neighbors(self, nid: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return nodes reachable from *nid* (optionally filtered by edge_type)."""
        result: List[Dict[str, Any]] = []
        for etype, tgt_id in self._out_edges.get(nid, []):
            if edge_type is None or etype == edge_type:
                n = self._nodes_by_id.get(tgt_id)
                if n:
                    result.append(n)
        return result

    def in_neighbors(self, nid: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return nodes with an edge pointing into *nid* (optionally filtered)."""
        result: List[Dict[str, Any]] = []
        for etype, src_id in self._in_edges.get(nid, []):
            if edge_type is None or etype == edge_type:
                n = self._nodes_by_id.get(src_id)
                if n:
                    result.append(n)
        return result

    def find_root_agent_call(self, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Find the outermost AgentCall node.

        When *agent_id* is provided, return the earliest AgentCall for that agent.
        Otherwise, return the AgentCall with no parent call (root of delegation tree).
        """
        agent_calls = self.agent_calls
        if not agent_calls:
            return None

        if agent_id:
            matches = [n for n in agent_calls if n.get("agentId") == agent_id]
            if matches:
                matches.sort(key=lambda n: float(n.get("startTime") or 0))
                return matches[0]
            logger.warning("agent_id %r not found in KG; auto-detecting root", agent_id)

        # AgentCall with no parentCallId = root
        for n in sorted(agent_calls, key=lambda n: float(n.get("startTime") or 0)):
            if not n.get("parentCallId"):
                return n

        # Fallback: earliest AgentCall
        return min(agent_calls, key=lambda n: float(n.get("startTime") or 0))


# ---------------------------------------------------------------------------
# FacetQuery — immutable filter spec
# ---------------------------------------------------------------------------


@dataclass
class FacetQuery:
    """Lightweight filter spec for KG subgraph extraction.

    All fields are optional (``None`` = no filter on that dimension).
    JSON-serialisable via :meth:`to_dict` / :meth:`from_dict`.

    Examples::

        # All nodes for a specific session
        q = FacetQuery(session_id="abc-123")

        # Only State and Transition nodes
        q = FacetQuery(node_types=["State", "Transition"])

        # LLM and Tool calls for two specific agents
        q = FacetQuery(
            agent_ids=["orchestrator", "analyst"],
            call_types=["LLMCall", "ToolCall"],
        )

        # Time slice (Unix epoch seconds)
        q = FacetQuery(time_range=(1716000000.0, 1716000010.0))
    """

    session_id: Optional[str] = None
    run_id: Optional[str] = None
    agent_ids: Optional[List[str]] = field(default=None)
    call_types: Optional[List[str]] = field(default=None)
    node_types: Optional[List[str]] = field(default=None)
    edge_types: Optional[List[str]] = field(default=None)
    time_range: Optional[Tuple[float, float]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dict (omits None fields)."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FacetQuery":
        """Deserialise from a dict, accepting both snake_case and camelCase keys."""
        tr = d.get("time_range") or d.get("timeRange")
        return cls(
            session_id=d.get("session_id") or d.get("sessionId"),
            run_id=d.get("run_id") or d.get("runId"),
            agent_ids=d.get("agent_ids") or d.get("agentIds"),
            call_types=d.get("call_types") or d.get("callTypes"),
            node_types=d.get("node_types") or d.get("nodeTypes"),
            edge_types=d.get("edge_types") or d.get("edgeTypes"),
            time_range=tuple(tr) if tr and len(tr) == 2 else None,
        )


# ---------------------------------------------------------------------------
# KGSource — apply FacetQuery to a KG document
# ---------------------------------------------------------------------------


class KGSource:
    """High-level data source backed by a kg.jsonld dict.

    Applies a :class:`FacetQuery` and returns a filtered ``{nodes, edges}``
    subgraph dict — suitable for downstream analysis, plotting, or Neo4j push.

    Usage::

        src = KGSource(kg_doc)
        src = KGSource.from_file("path/to/kg.jsonld")

        sub = src.subgraph()                           # full graph
        sub = src.subgraph(FacetQuery(node_types=["State", "Transition"]))
        nodes, edges = src.load(FacetQuery(agent_ids=["cra"]))
    """

    def __init__(self, kg: Dict[str, Any]) -> None:
        self._kg = kg

    @classmethod
    def from_file(cls, path: str | Path) -> "KGSource":
        """Load a KG file (kg.jsonld) and return a KGSource."""
        p = Path(path).expanduser().resolve()
        return cls(KGArtifact.from_file(p).to_doc())

    def subgraph(self, query: Optional[FacetQuery] = None) -> Dict[str, Any]:
        """Return a filtered ``{nodes, edges, run_id, metadata}`` dict.

        Filtering is applied in this order:
        1. node_types / call_types filter
        2. agent_ids filter (agentId field)
        3. session_id filter (sessionId field)
        4. run_id filter (runId field)
        5. time_range filter (startTime field)
        6. edge_types filter
        7. Drop edges whose endpoints were removed by node filters

        ``call_types`` is a convenience alias: it maps to the same node_type
        names (e.g. "LLMCall" → node_type "LLMCall").
        """
        nodes: List[Dict[str, Any]] = list(self._kg.get("nodes") or [])
        edges: List[Dict[str, Any]] = list(self._kg.get("edges") or [])

        if query is None:
            return dict(self._kg)

        # Resolve effective node_type whitelist
        allowed_types: Optional[Set[str]] = None
        if query.node_types:
            allowed_types = set(query.node_types)
        if query.call_types:
            extra = set(query.call_types) & _CALL_NODE_TYPES
            allowed_types = (allowed_types | extra) if allowed_types else extra

        if allowed_types is not None:
            nodes = [n for n in nodes if n.get("node_type") in allowed_types]

        if query.agent_ids:
            allowed_agents = {a.lower() for a in query.agent_ids}
            nodes = [
                n
                for n in nodes
                if str(n.get("agentId") or "").lower() in allowed_agents
                or n.get("node_type") not in _CALL_NODE_TYPES
            ]

        if query.session_id:
            sid = query.session_id.lower()
            nodes = [
                n
                for n in nodes
                if str(n.get("sessionId") or "").lower() == sid or not n.get("sessionId")
            ]

        if query.run_id:
            rid = query.run_id.lower()
            nodes = [
                n for n in nodes if str(n.get("runId") or "").lower() == rid or not n.get("runId")
            ]

        if query.time_range:
            t0, t1 = query.time_range
            nodes = [
                n
                for n in nodes
                if float(n.get("startTime") or 0) <= t1
                and float(n.get("endTime") or n.get("startTime") or 0) >= t0
            ]

        # Edge filtering — remove edges pointing to removed nodes
        kept_ids: Set[str] = {n.get("id", "") for n in nodes}

        def _src(e: Dict[str, Any]) -> str:
            return e.get("from_id") or e.get("source") or e.get("from") or ""

        def _tgt(e: Dict[str, Any]) -> str:
            return e.get("to_id") or e.get("target") or e.get("to") or ""

        if query.edge_types:
            allowed_et = set(query.edge_types)
            edges = [e for e in edges if (e.get("edge_type") or e.get("type")) in allowed_et]

        edges = [e for e in edges if _src(e) in kept_ids and _tgt(e) in kept_ids]

        result = dict(self._kg)
        result["nodes"] = nodes
        result["edges"] = edges
        return result

    def load(
        self, query: Optional[FacetQuery] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return ``(nodes, edges)`` after applying *query*."""
        sub = self.subgraph(query)
        return sub.get("nodes") or [], sub.get("edges") or []


# ---------------------------------------------------------------------------
# KGView — faceted call-record access
# ---------------------------------------------------------------------------


class KGView:
    """Faceted access layer over KG call records.

    Inspired by the ``dataquery.Store`` pattern — build once, query many times.

    Usage::

        view = KGView.from_kg(kg_doc)
        llm_calls = view.query("LLMCall")
        root_agents = view.query("AgentCall", parent_call_id=None)
        parent = view.get(record["parent_call_id"])
    """

    def __init__(self, nodes: List[Dict[str, Any]]) -> None:
        from collections import defaultdict

        self._by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._by_id: Dict[str, Dict[str, Any]] = {}

        for n in nodes:
            ntype = n.get("node_type", "")
            self._by_type[ntype].append(n)
            nid = n.get("id", "")
            if nid:
                self._by_id[nid] = n

    @classmethod
    def from_kg(cls, kg: Dict[str, Any]) -> "KGView":
        """Build a KGView from a raw kg.jsonld dict."""
        return cls(kg.get("nodes") or [])

    def query(self, node_type: str, **field_filters: Any) -> List[Dict[str, Any]]:
        """Return nodes of *node_type* matching *field_filters*.

        Each keyword argument is an equality filter. String comparisons are
        case-insensitive. Pass no filters to retrieve all nodes of that type.
        Nodes are sorted by startTime ascending.

        Examples::

            view.query("LLMCall")
            view.query("AgentCall", parentCallId=None)
            view.query("ToolCall", agentId="sre")
        """
        nodes = list(self._by_type.get(node_type, []))
        if not field_filters:
            nodes.sort(key=lambda n: float(n.get("startTime") or 0))
            return nodes

        def _match(node: Dict[str, Any]) -> bool:
            for k, v in field_filters.items():
                rv = node.get(k)
                if v is None:
                    if rv not in (None, ""):
                        return False
                elif isinstance(v, str):
                    if str(rv or "").lower() != v.lower():
                        return False
                else:
                    if rv != v:
                        return False
            return True

        result = [n for n in nodes if _match(n)]
        result.sort(key=lambda n: float(n.get("startTime") or 0))
        return result

    def get(self, node_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return the node with *node_id*, or ``None``."""
        if not node_id:
            return None
        return self._by_id.get(node_id)

    def types(self) -> List[str]:
        """Return the node_type names present in this view."""
        return list(self._by_type.keys())

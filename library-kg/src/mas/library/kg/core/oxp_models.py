#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map native graph_builder dicts onto ``scope_ontology.models``.

SCOPE (https://github.com/ioc-project/scope) ships Session, MASCall,
AgentCall, LLMCall, ToolCall, ProcessingCall, State, Transition, and the
structural catalog classes. Native-path classes that are not yet on that
package (RAGQuery, MemoryCall, SkillCall, GovernanceEvent, CallAnnotation,
…) stay as typed dicts with the same keys ``norm`` uses (``node_type`` /
``edge_type`` / ``id``) plus ``@type``.

Allowed default attributes come from the installed SCOPE models — this
module does not copy field lists. Never imports a private ontology package.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any, Dict, List, Optional, Tuple

from scope_ontology.models.base import MAS_CONTEXT, KGEdge, KGNode

logger = logging.getLogger(__name__)

# Native-path classes not in SCOPE. Declared in
# library-kg/ontology/extensions/mas-kg-native-extensions.ttl pending upstream.
NATIVE_EXTENSION_NODE_TYPES: frozenset[str] = frozenset(
    {
        "RAGQuery",
        "MemoryCall",
        "SkillCall",
        "Skill",
        "CallAnnotation",
        "ContextContribution",
        "ParallelGroup",
        "Branch",
        "GovernanceEvent",
        "Worker",
        "Run",
        "Application",
        "ThinkingCall",
        "TaskCall",
    }
)

# Native-extension / future A2A+MCP properties. Not emitted on the default path.
DEFERRED_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "callsAgent",
        "usesTool",
        "usesLLM",
        "usesCapability",
        "usesProcessing",
    }
)


def mas_namespace() -> str:
    """Return the SCOPE ``mas:`` namespace URI."""
    return str(MAS_CONTEXT["mas"])


def _scope_model_namespace() -> Any:
    from scope_ontology import models as scope_models

    return scope_models


def _scope_nodes_module() -> Any:
    try:
        from scope_ontology.models import nodes as scope_nodes

        return scope_nodes
    except ImportError:
        return _scope_model_namespace()


def _scope_edges_module() -> Any:
    try:
        from scope_ontology.models import edges as scope_edges

        return scope_edges
    except ImportError:
        return _scope_model_namespace()


def scope_core_node_types() -> frozenset[str]:
    """Node class names declared by the installed SCOPE models."""
    names: set[str] = set()
    for obj in vars(_scope_nodes_module()).values():
        if isinstance(obj, type) and issubclass(obj, KGNode) and obj is not KGNode:
            names.add(obj.__name__)
    return frozenset(names)


SCOPE_CORE_NODE_TYPES: frozenset[str] = scope_core_node_types()
# Back-compat alias used by Neo4j push / native model tests.
OXP_CORE_NODE_TYPES = SCOPE_CORE_NODE_TYPES


def _scope_node_class(name: str) -> Optional[type]:
    cls = getattr(_scope_model_namespace(), name, None)
    if cls is None:
        cls = getattr(_scope_nodes_module(), name, None)
    return cls if isinstance(cls, type) else None


def _scope_edge_class(name: str) -> Optional[type]:
    cls = getattr(_scope_model_namespace(), name, None)
    if cls is None:
        cls = getattr(_scope_edges_module(), name, None)
    return cls if isinstance(cls, type) else None


def _model_field_names(cls: type) -> set[str]:
    fields = getattr(cls, "model_fields", None)
    if isinstance(fields, dict):
        return set(fields)
    return set()


@cache
def graph_identity_keys() -> frozenset[str]:
    """Keys SCOPE ``to_json`` / ``to_jsonld`` emit for graph identity.

    Built from ``KGNode`` / ``KGEdge``, not a duplicated field table.
    """
    node = KGNode()
    edge = KGEdge(source_id="_", target_id="_")
    keys = set(node.to_json()) | set(node.to_jsonld()) | set(edge.to_json()) | set(edge.to_jsonld())
    keys.discard("@context")
    # Empty instances omit `id`; native/OTel dicts always carry it.
    keys.update({"id", "node_type", "edge_type", "from_id", "to_id", "@type"})
    return frozenset(keys)


# Back-compat name. Prefer :func:`graph_identity_keys`.
GRAPH_KEYS = graph_identity_keys()


def _dump_model(instance: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if hasattr(instance, "to_json"):
        dumped = instance.to_json()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(instance, "model_dump"):
        dumped = instance.model_dump(mode="json", exclude_none=True)
        if isinstance(dumped, dict):
            dumped.setdefault("node_type", fallback.get("node_type"))
            dumped.setdefault("edge_type", fallback.get("edge_type"))
            dumped.setdefault("id", fallback.get("id"))
            return dumped
    return fallback


def map_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """Return a native node dict annotated with SCOPE ``@type`` when possible.

    Overlapping fields are validated through the model and merged back.
    Native keys the model does not declare are kept (graph_builder / Neo4j
    still need them) until :func:`restrict_graph`.
    """
    ntype = str(node.get("node_type") or node.get("@type") or "")
    out = dict(node)
    if ntype:
        out.setdefault("@type", ntype)
    cls = _scope_node_class(ntype) if ntype else None
    if cls is None:
        return out
    allowed = _model_field_names(cls)
    payload = {k: v for k, v in node.items() if k in allowed and v is not None}
    try:
        instance = cls(**payload)
    except Exception as exc:  # noqa: BLE001 — extra=forbid / missing required
        logger.debug("scope node model %s rejected native payload: %s", ntype, exc)
        return out
    dumped = _dump_model(instance, node)
    for key, value in dumped.items():
        if value is not None:
            out.setdefault(key, value)
    out.setdefault("node_type", ntype)
    return out


def map_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    """Return a native edge dict annotated with SCOPE ``@type`` when possible."""
    etype = str(edge.get("edge_type") or edge.get("@type") or "")
    out = dict(edge)
    if etype:
        out.setdefault("@type", etype)
    cls = _scope_edge_class(etype) if etype else None
    if cls is None:
        return out
    allowed = _model_field_names(cls)
    payload: Dict[str, Any] = {}
    source = edge.get("source_id") or edge.get("from_id")
    target = edge.get("target_id") or edge.get("to_id")
    if "source_id" in allowed and source is not None:
        payload["source_id"] = str(source)
    if "target_id" in allowed and target is not None:
        payload["target_id"] = str(target)
    for key, value in edge.items():
        if key in allowed and key not in payload and value is not None:
            payload[key] = value
    try:
        instance = cls(**payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scope edge model %s rejected native payload: %s", etype, exc)
        return out
    dumped = _dump_model(instance, edge)
    for key, value in dumped.items():
        if value is not None:
            out.setdefault(key, value)
    out.setdefault("edge_type", etype)
    if source is not None:
        out.setdefault("from_id", source)
    if target is not None:
        out.setdefault("to_id", target)
    return out


def map_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Map a native ``(nodes, edges)`` pair onto SCOPE model dumps when possible."""
    return [map_node(n) for n in nodes], [map_edge(e) for e in edges]


def _edge_type_name(edge: Dict[str, Any]) -> str:
    return str(edge.get("edge_type") or edge.get("edgeType") or edge.get("type") or "")


def allowed_node_keys(ntype: str) -> frozenset[str]:
    """Return SCOPE model fields for *ntype*, plus graph identity keys."""
    cls = _scope_node_class(ntype) if ntype else None
    keys = set(graph_identity_keys())
    if cls is not None:
        keys |= _model_field_names(cls)
    return frozenset(keys)


def allowed_edge_keys(etype: str) -> frozenset[str]:
    """Return SCOPE model fields for *etype*, plus graph identity keys."""
    cls = _scope_edge_class(etype) if etype else None
    keys = set(graph_identity_keys())
    if cls is not None:
        keys |= _model_field_names(cls)
    return frozenset(keys)


def restrict_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    all_attributes: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Drop deferred edges and, unless *all_attributes*, extra native fields.

    Default output is the installed SCOPE attribute set so native→KG and
    OTel→KG share the same keys. ``callsAgent`` / ``usesTool`` stay off until
    A2A / MCP observability lands.
    """
    kept_edges = [e for e in edges if _edge_type_name(e) not in DEFERRED_EDGE_TYPES]
    if all_attributes:
        return nodes, kept_edges
    out_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        ntype = str(node.get("node_type") or node.get("@type") or "")
        if ntype in NATIVE_EXTENSION_NODE_TYPES:
            out_nodes.append(dict(node))
            continue
        allowed = allowed_node_keys(ntype)
        out_nodes.append({key: value for key, value in node.items() if key in allowed})
    out_edges: List[Dict[str, Any]] = []
    for edge in kept_edges:
        allowed = allowed_edge_keys(_edge_type_name(edge))
        out_edges.append({key: value for key, value in edge.items() if key in allowed})
    return out_nodes, out_edges

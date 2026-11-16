#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map native graph_builder dicts onto ``scope_ontology.models``.

SCOPE (https://github.com/ioc-project/scope) ships every class the fold emits
(Session, MASCall, AgentCall, LLMCall, ToolCall, ProcessingCall, SkillCall,
MemoryCall, RAGQuery, State, Transition, CallAnnotation and its subclasses, and
the catalog classes). Nodes and edges are plain dicts with the keys ``norm`` uses
(``node_type`` / ``edge_type`` / ``id``) plus ``@type``; the class declares which
other keys survive the projection.

Allowed default attributes come from the installed SCOPE models — this
module does not copy field lists. Never imports a private ontology package.
"""

from __future__ import annotations

import importlib
import logging
from functools import cache
from typing import Any, Dict, List, Optional, Tuple

from scope_ontology.models.base import MAS_CONTEXT, KGEdge, KGNode

logger = logging.getLogger(__name__)

# Classes the native fold emits that a released SCOPE ontology may not declare yet.
# Declared in library-kg/ontology/extensions/mas-kg-native-extensions.ttl until it does.
NATIVE_EXTENSION_NODE_TYPES: frozenset[str] = frozenset(
    {
        "RAGQuery",
        "MemoryCall",
        "SkillCall",
        "Skill",
        "CallAnnotation",
        "ContextContribution",
        "GovernanceEvent",
        "Worker",
        "ThinkingCall",
    }
)

# Future A2A+MCP properties. Not emitted on the default path.
DEFERRED_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "callsAgent",
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


def _models_part(part: str) -> Any:
    try:
        return importlib.import_module(f"scope_ontology.models.{part}")
    except ImportError:
        return _scope_model_namespace()


def _scope_class(name: str, part: str) -> Optional[type]:
    if not name:
        return None
    for module in (_scope_model_namespace(), _models_part(part)):
        cls = getattr(module, name, None)
        if isinstance(cls, type):
            return cls
    return None


def scope_core_node_types() -> frozenset[str]:
    """Node class names declared by the installed SCOPE models."""
    names: set[str] = set()
    for obj in vars(_models_part("nodes")).values():
        if isinstance(obj, type) and issubclass(obj, KGNode) and obj is not KGNode:
            names.add(obj.__name__)
    return frozenset(names)


SCOPE_CORE_NODE_TYPES: frozenset[str] = scope_core_node_types()


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


# Fields whose SCOPE model default (0, "") would read as a value: they are in the output only
# when the event carries them (a model that was not asked for reasoning has no effort).
_ONLY_WHEN_STATED = frozenset({"reasoningTokenCount", "reasoningEffort"})


def _merge_model(
    cls: type,
    payload: Dict[str, Any],
    fallback: Dict[str, Any],
    *,
    kind: str,
    kind_key: str,
) -> Dict[str, Any]:
    out = dict(fallback)
    if kind:
        out.setdefault("@type", kind)
    try:
        instance = cls(**payload)
    except Exception as exc:  # noqa: BLE001 — extra=forbid / missing required
        logger.debug("scope %s model %s rejected native payload: %s", kind_key, kind, exc)
        return out
    for key, value in _dump_model(instance, fallback).items():
        if value is not None and (key not in _ONLY_WHEN_STATED or key in payload):
            out.setdefault(key, value)
    out.setdefault(kind_key, kind)
    return out


def map_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """Return a native node dict annotated with SCOPE ``@type`` when possible.

    Overlapping fields are validated through the model and merged back.
    Native keys the model does not declare are kept (graph_builder / Neo4j
    still need them) until :func:`restrict_graph`.
    """
    ntype = str(node.get("node_type") or node.get("@type") or "")
    cls = _scope_class(ntype, "nodes")
    if cls is None:
        out = dict(node)
        if ntype:
            out.setdefault("@type", ntype)
        return out
    allowed = _model_field_names(cls)
    payload = {k: v for k, v in node.items() if k in allowed and v is not None}
    return _merge_model(cls, payload, node, kind=ntype, kind_key="node_type")


def map_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    """Return a native edge dict annotated with SCOPE ``@type`` when possible."""
    etype = str(edge.get("edge_type") or edge.get("@type") or "")
    cls = _scope_class(etype, "edges")
    if cls is None:
        out = dict(edge)
        if etype:
            out.setdefault("@type", etype)
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
    out = _merge_model(cls, payload, edge, kind=etype, kind_key="edge_type")
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


def _allowed_keys(name: str, part: str) -> frozenset[str]:
    keys = set(graph_identity_keys())
    cls = _scope_class(name, part)
    if cls is not None:
        keys |= _model_field_names(cls)
    return frozenset(keys)


def allowed_node_keys(ntype: str) -> frozenset[str]:
    """Return SCOPE model fields for *ntype*, plus graph identity keys."""
    return _allowed_keys(ntype, "nodes")


def allowed_edge_keys(etype: str) -> frozenset[str]:
    """Return SCOPE model fields for *etype*, plus graph identity keys."""
    return _allowed_keys(etype, "edges")


def restrict_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    all_attributes: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Drop deferred edges and, unless *all_attributes*, extra native fields.

    Default output is the installed SCOPE attribute set so native→KG and
    OTel→KG share the same keys. ``callsAgent`` stays off until
    A2A / MCP observability lands.
    """
    kept_edges = [e for e in edges if _edge_type_name(e) not in DEFERRED_EDGE_TYPES]
    if all_attributes:
        return nodes, kept_edges
    out_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        ntype = str(node.get("node_type") or node.get("@type") or "")
        if ntype in NATIVE_EXTENSION_NODE_TYPES and _scope_class(ntype, "nodes") is None:
            # The installed SCOPE does not declare the class: nothing to restrict it to.
            out_nodes.append(dict(node))
            continue
        allowed = allowed_node_keys(ntype)
        out_nodes.append({key: value for key, value in node.items() if key in allowed})
    out_edges: List[Dict[str, Any]] = []
    for edge in kept_edges:
        allowed = allowed_edge_keys(_edge_type_name(edge))
        out_edges.append({key: value for key, value in edge.items() if key in allowed})
    return out_nodes, out_edges

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map native graph_builder dicts onto ``oxp_ontology.models`` when present.

PyPI ``oxp-ontology`` 1.0.0 ships Session, MASCall, AgentCall, LLMCall,
ToolCall, ProcessingCall, State, Transition, and the structural catalog
classes (MAS, Agent, LLM, Tool, Processing). Native-path classes that are
not yet on that package (RAGQuery, MemoryCall, SkillCall, GovernanceEvent,
CallAnnotation, …) stay as typed dicts with the same keys ``norm`` uses
(``node_type`` / ``edge_type`` / ``id``) plus ``@type``.

Never imports a private ontology package.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# KIND_TO_CLASS / graph_builder local names that oxp-ontology 1.0.0 models
# are expected to cover. Audited against `pip install oxp-ontology==1.0.0`.
OXP_CORE_NODE_TYPES: frozenset[str] = frozenset(
    {
        "Session",
        "MASCall",
        "AgentCall",
        "LLMCall",
        "ToolCall",
        "ProcessingCall",
        "MAS",
        "Agent",
        "LLM",
        "Tool",
        "Processing",
        "State",
        "Transition",
        "Capability",
        "CapabilityCall",
        "StructuralElement",
        "ExecutionElement",
        "TrajectoryElement",
    }
)

# Native-path classes not in oxp-ontology 1.0.0. Declared in
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


def _oxp_node_class(name: str) -> Optional[type]:
    try:
        from oxp_ontology import models as oxp_models  # type: ignore[import]
    except ImportError:
        return None
    cls = getattr(oxp_models, name, None)
    if cls is None:
        try:
            from oxp_ontology.models import nodes as oxp_nodes  # type: ignore[import]

            cls = getattr(oxp_nodes, name, None)
        except ImportError:
            return None
    return cls if isinstance(cls, type) else None


def _oxp_edge_class(name: str) -> Optional[type]:
    try:
        from oxp_ontology import models as oxp_models  # type: ignore[import]
    except ImportError:
        return None
    cls = getattr(oxp_models, name, None)
    if cls is None:
        try:
            from oxp_ontology.models import edges as oxp_edges  # type: ignore[import]

            cls = getattr(oxp_edges, name, None)
        except ImportError:
            return None
    return cls if isinstance(cls, type) else None


def _model_field_names(cls: type) -> set[str]:
    fields = getattr(cls, "model_fields", None)
    if isinstance(fields, dict):
        return set(fields)
    return set()


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
    """Return a native node dict annotated with scope ``@type`` when possible.

    When an ``oxp_ontology.models`` class exists, overlapping fields are
    validated through the model and merged back. Native keys the model does
    not declare are kept (graph_builder / Neo4j still need them).
    """
    ntype = str(node.get("node_type") or node.get("@type") or "")
    out = dict(node)
    if ntype:
        out.setdefault("@type", ntype)
    cls = _oxp_node_class(ntype) if ntype else None
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
    """Return a native edge dict annotated with scope ``@type`` when possible."""
    etype = str(edge.get("edge_type") or edge.get("@type") or "")
    out = dict(edge)
    if etype:
        out.setdefault("@type", etype)
    cls = _oxp_edge_class(etype) if etype else None
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
    """Map a native ``(nodes, edges)`` pair onto scope model dumps when possible."""
    return [map_node(n) for n in nodes], [map_edge(e) for e in edges]

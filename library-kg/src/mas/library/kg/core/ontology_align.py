#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Align a KG dict graph onto oxp-ontology instance contracts.

Native builders historically used Neo4j-style edge names (``contains``,
``hasCall``, ``realizes``). SCOPE ``norm`` copies class ``:layer`` annotations
onto instances. This module rewrites those at the graph-dict boundary so
SHACL and ``unknown_edge_types`` see the ontology vocabulary.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

_AGENT_CALL_NAME = re.compile(r"^(.*) call (?:0x)?[0-9a-fA-F]+$")
_TOOL_CALL_NAME = re.compile(r"^Tool call \((.+)\)(?:\s|$)")
_LLM_CALL_NAME = re.compile(r"^LLM call \(([^/]+)/([^)]+)\)")

# Destination node_type → ontology containment property (replaces contains/hasCall).
CONTAINS_TO_OXP: Dict[str, str] = {
    "LLMCall": "hasLLMCall",
    "ToolCall": "hasToolCall",
    "AgentCall": "hasAgentCall",
    "MASCall": "hasMASCall",
    "ProcessingCall": "hasProcessingCall",
    "MemoryCall": "hasMemoryCall",
    "SkillCall": "hasSkillCall",
    "RAGQuery": "hasRAGQuery",
    "ThinkingCall": "hasProcessingCall",
    "ContextContribution": "contributesTo",
    "GovernanceEvent": "annotates",
    "CallAnnotation": "annotates",
}

CONTAINMENT_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "contains",
        "hasCall",
        *CONTAINS_TO_OXP.values(),
    }
)

_OXP_LAYER_AS_BLOCK = frozenset({"structural", "execution", "trajectory", "evaluation"})

_EXECUTES = {
    "MASCall": ("executesMAS", "MAS"),
    "AgentCall": ("executesAgent", "Agent"),
    "LLMCall": ("executesLLM", "LLM"),
    "ToolCall": ("executesTool", "Tool"),
    "ProcessingCall": ("executesProcessing", "Processing"),
    "SkillCall": ("executesProcessing", "Processing"),
}

_CATALOG_EDGE = {
    "ofLLMType": "executesLLM",
    "ofToolType": "executesTool",
    "invokesProcessing": "executesProcessing",
    "invokesSkill": "executesProcessing",
    "realizes": "representsExecution",
}


def _agent_id_from_node(node: Dict[str, Any]) -> str:
    for key in ("agentId", "agent_id", "agentName"):
        val = node.get(key)
        if val not in (None, ""):
            return str(val)
    name = str(node.get("name") or "")
    match = _AGENT_CALL_NAME.match(name)
    return match.group(1) if match else ""


def _tool_name_from_node(node: Dict[str, Any]) -> str:
    for key in ("toolName", "tool_name"):
        val = node.get(key)
        if val not in (None, ""):
            return str(val)
    name = str(node.get("name") or "")
    match = _TOOL_CALL_NAME.match(name)
    return match.group(1) if match else ""


def _node_key(node: Dict[str, Any]) -> str:
    return str(
        node.get("id")
        or node.get("callId")
        or node.get("sessionId")
        or node.get("stateNodeId")
        or node.get("transitionId")
        or node.get("agentId")
        or ""
    )


def _index_nodes(nodes: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        nid = node.get("id")
        if nid:
            out[str(nid)] = node
    for node in nodes:
        for key in (
            "callId",
            "sessionId",
            "stateNodeId",
            "transitionId",
            "agentId",
            "annotationId",
        ):
            val = node.get(key)
            if val:
                out.setdefault(str(val), node)
    return out


def _edge_tuple(edge: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(edge.get("edge_type") or ""),
        str(edge.get("from_id") or edge.get("source_id") or ""),
        str(edge.get("to_id") or edge.get("target_id") or ""),
    )


def _add_edge(
    edges: List[Dict[str, Any]],
    seen: set[Tuple[str, str, str]],
    edge_type: str,
    from_id: str,
    to_id: str,
    **extra: Any,
) -> None:
    if not from_id or not to_id or from_id == to_id:
        return
    key = (edge_type, from_id, to_id)
    if key in seen:
        return
    seen.add(key)
    rec: Dict[str, Any] = {"edge_type": edge_type, "from_id": from_id, "to_id": to_id}
    rec.update(extra)
    edges.append(rec)


def _duration_ms(node: Dict[str, Any]) -> Optional[float]:
    raw = node.get("duration")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    start, end = node.get("startTime"), node.get("endTime")
    if start in (None, "") or end in (None, ""):
        return None
    try:
        delta = float(end) - float(start)
    except (TypeError, ValueError):
        return None
    # Epoch seconds → milliseconds. Values already in ms stay as-is.
    if delta >= 0 and delta < 1e6:
        return delta * 1000.0 if delta < 1000.0 else delta
    return max(delta, 0.0)


def _default_name(node: Dict[str, Any]) -> str:
    for key in (
        "name",
        "agentName",
        "masName",
        "llmName",
        "toolName",
        "processingName",
        "skillName",
        "appName",
        "agentId",
    ):
        val = node.get(key)
        if val not in (None, ""):
            return str(val)
    ntype = str(node.get("node_type") or "Element")
    nid = _node_key(node)
    return f"{ntype} {nid}".strip()


def align_node_fields(node: Dict[str, Any]) -> Dict[str, Any]:
    """Fill Element/ExecutionElement identity fields; un-leak class ``layer``."""
    out = dict(node)
    ntype = str(out.get("node_type") or "")
    layer = out.get("layer")
    if layer in _OXP_LAYER_AS_BLOCK:
        out.setdefault("block", layer)
        if ntype in {"State", "Transition", "Branch", "ParallelGroup"}:
            out["layer"] = "normalized"
        else:
            out.pop("layer", None)
    elif ntype in {"State", "Transition", "Branch", "ParallelGroup"}:
        out.setdefault("layer", "normalized")

    if not out.get("id"):
        key = _node_key(out)
        if key:
            out["id"] = key
    out.setdefault("name", _default_name(out))
    duration = _duration_ms(out)
    if duration is not None and ntype in {
        "Session",
        "MASCall",
        "AgentCall",
        "LLMCall",
        "ToolCall",
        "ProcessingCall",
        "SkillCall",
        "MemoryCall",
        "RAGQuery",
        "ThinkingCall",
        "CapabilityCall",
    }:
        out.setdefault("duration", duration)
    if ntype in {
        "Agent",
        "MAS",
        "LLM",
        "Tool",
        "Processing",
        "Skill",
        "Run",
        "Application",
        "Worker",
        "Capability",
    }:
        out.setdefault("declared", False)
        out.setdefault("description", out.get("name"))
    if ntype in {"Tool", "LLM", "Skill", "Processing", "MAS", "Capability", "Agent"}:
        name = str(out.get("name") or out.get("agentId") or ntype)
        old = str(out.get("id") or "")
        if " " in old or not old.startswith("catalog:"):
            # keep existing Agent instance ids (agent:u1); only rewrite spaced catalog ids
            if " " in old:
                out["id"] = _catalog_id(ntype, name)
    if ntype == "AgentCall" and not out.get("agentId"):
        recovered = _agent_id_from_node(out)
        if recovered:
            out["agentId"] = recovered
    if ntype == "ToolCall" and not out.get("toolName"):
        recovered = _tool_name_from_node(out)
        if recovered:
            out["toolName"] = recovered
    if ntype == "LLMCall":
        if not out.get("provider") or not out.get("modelName"):
            match = _LLM_CALL_NAME.match(str(out.get("name") or ""))
            if match:
                out.setdefault("provider", match.group(1))
                out.setdefault("modelName", match.group(2))
        if not out.get("provider"):
            model = str(out.get("modelName") or "")
            if "/" in model:
                out["provider"] = model.split("/", 1)[0] or "unknown"
            else:
                out["provider"] = "unknown"
        out.setdefault("promptTokenCount", 0)
        out.setdefault("completionTokenCount", 0)
        out.setdefault("totalTokenCount", 0)
        out.setdefault("cacheReadTokenCount", 0)
        out.setdefault("finishReason", "")
        out.setdefault("responseId", "")
        out.setdefault("temperature", 0.0)
    if ntype == "LLM" and not out.get("provider"):
        out["provider"] = "unknown"
    return out


def canonicalize_edge_type(
    edge_type: str,
    src_type: str,
    dst_type: str,
) -> Optional[str]:
    name = edge_type.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    if name in {"fromState", "toState"}:
        return None
    if name in {"contains", "hasCall"}:
        if dst_type == "Run" or src_type == "Run":
            return None
        return CONTAINS_TO_OXP.get(dst_type)
    if name in _CATALOG_EDGE:
        return _CATALOG_EDGE[name]
    return name


def _first_of_type(nodes: List[Dict[str, Any]], ntype: str) -> Optional[Dict[str, Any]]:
    for node in nodes:
        if node.get("node_type") == ntype:
            return node
    return None


def _catalog_id(ntype: str, name: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in str(name).strip())
    return f"catalog:{ntype.lower()}:{slug or ntype.lower()}"


def _ensure_catalog(
    nodes: List[Dict[str, Any]],
    ntype: str,
    name: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    nid = _catalog_id(ntype, name)
    for node in nodes:
        if node.get("node_type") == ntype and (
            node.get("id") == nid or node.get("name") == name or node.get("agentId") == name
        ):
            return node
    rec: Dict[str, Any] = {
        "node_type": ntype,
        "id": nid,
        "name": name,
        "block": "structural",
        "declared": False,
        "description": name,
    }
    if ntype == "Agent":
        rec["agentId"] = name
    if extra:
        rec.update(extra)
    nodes.append(rec)
    return rec


def ensure_oxp_topology(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add required Session/MAS/Agent/LLM typing and trajectory inverses."""
    by_id = _index_nodes(nodes)
    seen = {_edge_tuple(e) for e in edges}
    session = _first_of_type(nodes, "Session")
    mas_call = _first_of_type(nodes, "MASCall")
    app_name = "mas"
    if session is not None:
        raw_app = str(session.get("appName") or "")
        app_name = raw_app if raw_app and not raw_app.startswith("Session ") else "mas"
    mas = _first_of_type(nodes, "MAS")
    if mas is None:
        mas = _ensure_catalog(nodes, "MAS", app_name)
        by_id = _index_nodes(nodes)

    if session is not None and mas is not None:
        _add_edge(edges, seen, "executesSession", str(session.get("id")), str(mas.get("id")))
        _add_edge(edges, seen, "executes", str(session.get("id")), str(mas.get("id")))
    if session is not None and mas_call is not None:
        _add_edge(
            edges,
            seen,
            "hasMASCall",
            str(session.get("id")),
            str(mas_call.get("id") or mas_call.get("callId")),
        )
    if mas_call is not None and mas is not None:
        _add_edge(
            edges,
            seen,
            "executesMAS",
            str(mas_call.get("id") or mas_call.get("callId")),
            str(mas.get("id")),
        )

    for node in list(nodes):
        ntype = str(node.get("node_type") or "")
        nid = str(node.get("id") or node.get("callId") or "")
        spec = _EXECUTES.get(ntype)
        if spec and nid:
            edge_name, catalog_type = spec
            if catalog_type == "Agent":
                catalog_name = _agent_id_from_node(node)
                if not catalog_name:
                    continue
            elif catalog_type == "LLM":
                catalog_name = str(node.get("modelName") or "")
                if not catalog_name:
                    llm_name = str(node.get("llmName") or "")
                    agent = _agent_id_from_node(node)
                    # Cached LLM events often copy agent_id into name/llmName.
                    # That must not become a second catalog entry that steals
                    # the Agent's RDF id (belongsToMAS lives on the Agent).
                    if llm_name and llm_name != agent:
                        catalog_name = llm_name
                    else:
                        catalog_name = "llm"
            elif catalog_type == "Tool":
                catalog_name = _tool_name_from_node(node)
                if not catalog_name:
                    continue
            elif catalog_type == "Processing":
                catalog_name = str(node.get("processingName") or node.get("name") or "processing")
            else:
                catalog_name = app_name
            if catalog_type == "MAS" and mas is not None:
                catalog = mas
            else:
                catalog = _ensure_catalog(nodes, catalog_type, catalog_name)
            cat_id = str(catalog.get("id"))
            _add_edge(edges, seen, edge_name, nid, cat_id)
            _add_edge(edges, seen, "executes", nid, cat_id)
        if ntype == "Agent" and mas is not None and nid:
            _add_edge(edges, seen, "belongsToMAS", nid, str(mas.get("id")))
        if ntype == "AgentCall" and mas_call is not None and nid:
            mc_id = str(mas_call.get("id") or mas_call.get("callId"))
            _add_edge(edges, seen, "belongsToMASCall", nid, mc_id)
            _add_edge(edges, seen, "hasAgentCall", mc_id, nid)

    # Catalog Agents / AgentCalls created during the snapshot loop above
    # must still receive containment edges (SHACL minCount=1).
    if mas is not None:
        mas_id = str(mas.get("id") or "")
        for node in nodes:
            if str(node.get("node_type") or "") != "Agent":
                continue
            nid = str(node.get("id") or node.get("agentId") or "")
            _add_edge(edges, seen, "belongsToMAS", nid, mas_id)
    if mas_call is not None:
        mc_id = str(mas_call.get("id") or mas_call.get("callId") or "")
        for node in nodes:
            if str(node.get("node_type") or "") != "AgentCall":
                continue
            nid = str(node.get("id") or node.get("callId") or "")
            _add_edge(edges, seen, "belongsToMASCall", nid, mc_id)
            _add_edge(edges, seen, "hasAgentCall", mc_id, nid)

    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        parent = str(node.get("parentCallId") or "")
        if parent:
            by_parent.setdefault(parent, []).append(node)
    for node in nodes:
        ntype = str(node.get("node_type") or "")
        if ntype not in {"AgentCall", "MASCall", "Session"}:
            continue
        nid = str(node.get("id") or node.get("callId") or "")
        aliases = {nid, str(node.get("callId") or "")}
        children: List[Dict[str, Any]] = []
        for alias in aliases:
            children.extend(by_parent.get(alias, []))
        for child in children:
            scope = CONTAINS_TO_OXP.get(str(child.get("node_type") or ""))
            cid = str(child.get("id") or child.get("callId") or "")
            if scope and cid:
                _add_edge(edges, seen, scope, nid, cid)

    # Norm stores AgentCall↔child links as parentSpanId (no hasLLMCall /
    # hasToolCall edges). Project those so SHACL sees the same tree.
    def _span_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        return text

    by_span: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        span = _span_key(node.get("spanId") or node.get("span_id"))
        if span:
            by_span[span] = node
    for node in nodes:
        parent_span = _span_key(node.get("parentSpanId") or node.get("parent_span_id"))
        parent = by_span.get(parent_span) if parent_span else None
        if parent is None:
            continue
        scope = CONTAINS_TO_OXP.get(str(node.get("node_type") or ""))
        if not scope:
            continue
        src = str(parent.get("id") or parent.get("callId") or "")
        dst = str(node.get("id") or node.get("callId") or "")
        if src and dst:
            _add_edge(edges, seen, scope, src, dst)
        if str(node.get("node_type") or "") in {"LLMCall", "ToolCall"} and not node.get("agentId"):
            recovered = _agent_id_from_node(parent)
            if recovered:
                node["agentId"] = recovered

    # Trajectory inverses + hasState (explicit; do not rely on RDFS inference).
    extra: List[Tuple[str, str, str]] = []
    for edge in edges:
        et = str(edge.get("edge_type") or "")
        src, dst = str(edge.get("from_id") or ""), str(edge.get("to_id") or "")
        if et in {"hasInitialState", "hasFinalState"}:
            extra.append(("hasState", src, dst))
        if et == "leadsTo":
            extra.append(("inputTo", dst, src))
        if et == "representsExecution":
            extra.append(("correspondsToTransition", dst, src))
        if et == "correspondsToTransition":
            extra.append(("representsExecution", dst, src))
    for et, src, dst in extra:
        _add_edge(edges, seen, et, src, dst)

    # MASCall and Session share the session-level trajectory (SCOPE contract).
    if session is not None and mas_call is not None:
        sid_node = str(session.get("id") or "")
        mc_id = str(mas_call.get("id") or mas_call.get("callId") or "")
        for edge in list(edges):
            if str(edge.get("from_id") or "") != sid_node:
                continue
            et = str(edge.get("edge_type") or "")
            if et in {
                "hasInitialState",
                "hasFinalState",
                "hasState",
                "correspondsToTransition",
            }:
                _add_edge(edges, seen, et, mc_id, str(edge.get("to_id") or ""))
        trans = _first_of_type(nodes, "Transition")
        if trans is not None:
            trans_id = str(trans.get("id") or trans.get("transitionId") or "")
            _add_edge(edges, seen, "correspondsToTransition", sid_node, trans_id)
            _add_edge(edges, seen, "correspondsToTransition", mc_id, trans_id)
    return nodes


def align_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    emit_topology: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ontology-aligned copies of *nodes* and *edges*."""
    session = _first_of_type(nodes, "Session")
    sid = str((session or {}).get("sessionId") or (session or {}).get("id") or "")
    aligned_nodes = []
    id_map: Dict[str, str] = {}
    for node in nodes:
        rec = dict(node)
        old_id = str(rec.get("id") or "")
        if sid and rec.get("node_type") in {
            "Session",
            "MASCall",
            "AgentCall",
            "LLMCall",
            "ToolCall",
            "ProcessingCall",
            "SkillCall",
            "MemoryCall",
            "RAGQuery",
            "ThinkingCall",
            "CapabilityCall",
            "State",
            "Transition",
        }:
            rec.setdefault("sessionId", sid)
        rec = align_node_fields(rec)
        new_id = str(rec.get("id") or "")
        if old_id and new_id and old_id != new_id:
            id_map[old_id] = new_id
        aligned_nodes.append(rec)
    by_id = _index_nodes(aligned_nodes)
    rewritten: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str]] = set()
    for edge in edges:
        src_id = str(edge.get("from_id") or edge.get("source_id") or "")
        dst_id = str(edge.get("to_id") or edge.get("target_id") or "")
        src_id = id_map.get(src_id, src_id)
        dst_id = id_map.get(dst_id, dst_id)
        src = by_id.get(src_id) or {}
        dst = by_id.get(dst_id) or {}
        mapped = canonicalize_edge_type(
            str(edge.get("edge_type") or ""),
            str(src.get("node_type") or ""),
            str(dst.get("node_type") or ""),
        )
        if not mapped:
            continue
        rec = dict(edge)
        rec["edge_type"] = mapped
        rec["from_id"] = src_id
        rec["to_id"] = dst_id
        key = _edge_tuple(rec)
        if key in seen:
            continue
        seen.add(key)
        rewritten.append(rec)
    if emit_topology:
        aligned_nodes = ensure_oxp_topology(aligned_nodes, rewritten)
    return aligned_nodes, rewritten

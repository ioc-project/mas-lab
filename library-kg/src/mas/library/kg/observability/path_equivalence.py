#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compare native events → KG vs native events → observe-sdk OTel → norm.

Two modes:

* ``default`` — only the SCOPE / observe-sdk native OTel categories
  (``*.agent`` / ``*.chat`` / ``*.tool`` / ``*.graph`` / ``session.*``).
* ``complete`` — every export layer plus observe-sdk MAS extensions
  (``*.memory`` / ``*.processing`` / ``*.context`` / ``*.governance`` /
  ``*.skill`` / ``*.rag``). Stock ``norm.normalize()`` still owns the
  default categories; MAS materializes the extension suffixes that SCOPE
  does not yet dispatch.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Literal, Sequence

from mas.library.kg.observability.otel_via_norm import normalize_otel, to_clickhouse_spans
from mas.library.kg.pipeline import build_kg_document

Mode = Literal["default", "complete"]

DEFAULT_COMPARE_TYPES: frozenset[str] = frozenset(
    {
        "Agent",
        "AgentCall",
        "LLMCall",
        "Tool",
        "ToolCall",
    }
)

COMPLETE_COMPARE_TYPES: frozenset[str] = DEFAULT_COMPARE_TYPES | frozenset(
    {
        "ProcessingCall",
        "MemoryCall",
        "SkillCall",
        "RAGQuery",
        "ContextContribution",
        "GovernanceEvent",
    }
)

_SUFFIX_TO_TYPE: dict[str, str] = {
    "memory": "MemoryCall",
    "processing": "ProcessingCall",
    "context": "ContextContribution",
    "governance": "GovernanceEvent",
    "skill": "SkillCall",
    "rag": "RAGQuery",
}

_NAME_FIELDS = (
    "name",
    "toolName",
    "tool_name",
    "agentId",
    "agent_id",
    "callId",
    "call_id",
    "processingName",
    "skillName",
)


def node_type(node: dict[str, Any]) -> str:
    raw = node.get("node_type") or node.get("@type") or ""
    text = str(raw)
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if "#" in text:
        text = text.rsplit("#", 1)[-1]
    return text


def _is_synthesized(node: dict[str, Any]) -> bool:
    return bool(node.get("synthesised") or node.get("synthesized"))


def _is_end_governance(node: dict[str, Any]) -> bool:
    if node_type(node) != "GovernanceEvent":
        return False
    kind = str(node.get("kind") or node.get("annotationKind") or "")
    return kind.endswith("_end")


def _align_reused_call_ids(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give each start/end pair a unique call_id so native KG keeps instances.

    The observe-sdk converter opens a new span when a call_id is reused after
    close. Native ``extract_graph`` keys nodes by ``call_id`` and would
    otherwise collapse those instances.
    """
    stacks: dict[str, list[str]] = {}
    seen: dict[str, int] = {}
    aligned: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        call_id = item.get("call_id")
        kind = str(item.get("kind") or "")
        if not call_id:
            aligned.append(item)
            continue
        key = str(call_id)
        if kind.endswith("_start"):
            seen[key] = seen.get(key, 0) + 1
            unique = key if seen[key] == 1 else f"{key}#{seen[key]}"
            stacks.setdefault(key, []).append(unique)
            item["call_id"] = unique
        elif kind.endswith("_end") and stacks.get(key):
            item["call_id"] = stacks[key].pop()
        aligned.append(item)
    return aligned


def _identity_name(node: dict[str, Any]) -> str:
    for field in _NAME_FIELDS:
        value = node.get(field)
        if value not in (None, ""):
            return _strip_generated_suffix(str(value))
    return ""


def _strip_generated_suffix(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("Tool call (") and ")" in text:
        inner = text[len("Tool call (") :]
        inner = inner.rsplit(")", 1)[0]
        return inner
    if text.startswith("LLM call ("):
        return ""
    for prefix in ("Session ", "MASCall ", "AgentCall "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if " call 0x" in text:
        text = text.rsplit(" call 0x", 1)[0]
    return text


def _logical_key(
    node: dict[str, Any],
    *,
    agent_by_span: dict[str, str] | None = None,
    span_index: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    ntype = node_type(node)
    agent = _strip_generated_suffix(
        str(node.get("agentId") or node.get("agent_id") or "")
    )
    if not agent and agent_by_span:
        parent = _norm_span(node.get("parentSpanId") or node.get("parent_span_id") or "")
        agent = agent_by_span.get(parent, "")
        if not agent and span_index:
            # The direct parent span may be an LLM/tool span (not the
            # AgentCall itself) -- walk one more hop up to that span's own
            # parent, which is where the owning AgentCall's span lives.
            parent_node = span_index.get(parent)
            if parent_node is not None:
                grandparent = _norm_span(
                    parent_node.get("parentSpanId") or parent_node.get("parent_span_id") or ""
                )
                agent = agent_by_span.get(grandparent, "")
    tool = _strip_generated_suffix(
        str(node.get("toolName") or node.get("tool_name") or "")
    )
    if ntype in {"Tool", "ToolCall"}:
        label = tool or _identity_name(node)
        return ntype, label
    if ntype in {"Agent", "AgentCall", "LLMCall", "ProcessingCall", "MemoryCall", "SkillCall", "RAGQuery", "GovernanceEvent", "ContextContribution"}:
        return ntype, agent or _identity_name(node)
    if ntype == "LLM":
        return ntype, _identity_name(node) or "llm"
    return ntype, _identity_name(node)


def _norm_span(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text


def _agent_by_span(nodes: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in nodes:
        if node_type(node) != "AgentCall":
            continue
        agent = _strip_generated_suffix(
            str(node.get("agentId") or node.get("agent_id") or _identity_name(node))
        )
        span = _norm_span(node.get("spanId") or node.get("span_id") or "")
        if span and agent:
            out[span] = agent
    return out


def _span_index(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        span = _norm_span(node.get("spanId") or node.get("span_id") or "")
        if span:
            out[span] = node
    return out


def _enclosing_call(
    node: dict[str, Any],
    *,
    by_span: dict[str, dict[str, Any]],
    types: set[str],
) -> dict[str, Any] | None:
    seen: set[str] = set()
    current = node
    for _ in range(8):
        parent = _norm_span(current.get("parentSpanId") or current.get("parent_span_id") or "")
        if not parent or parent in seen:
            return None
        seen.add(parent)
        ancestor = by_span.get(parent)
        if ancestor is None:
            return None
        if node_type(ancestor) in types:
            return ancestor
        current = ancestor
    return None


def _enclosing_agent_call(
    node: dict[str, Any],
    *,
    by_span: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    return _enclosing_call(node, by_span=by_span, types={"AgentCall"})


_CONTAINS_TO_OXP = {
    "LLMCall": "hasLLMCall",
    "ToolCall": "hasToolCall",
    "AgentCall": "hasAgentCall",
    "MASCall": "hasMASCall",
    "ProcessingCall": "hasProcessingCall",
    "MemoryCall": "hasMemoryCall",
    "SkillCall": "hasSkillCall",
    "RAGQuery": "hasRAGQuery",
    "ContextContribution": "contributesTo",
    "GovernanceEvent": "annotates",
}


def _canonical_edge_type(etype: str, src_type: str, dst_type: str) -> str | None:
    name = etype.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    if name in {"contains", "hasCall"}:
        return _CONTAINS_TO_OXP.get(dst_type)
    if name in {
        "hasLLMCall",
        "hasToolCall",
        "hasAgentCall",
        "hasMASCall",
        "hasProcessingCall",
        "hasMemoryCall",
        "hasSkillCall",
        "hasRAGQuery",
        "contributesTo",
        "annotates",
    }:
        return name
    return None


def _keep_canonical_edge(mapped: str, src_key: tuple[str, str], dst_key: tuple[str, str]) -> bool:
    """Keep the overlapping call-tree projection both paths can emit.

    Native KG nests AgentCalls under delegate ToolCalls and ProcessingCalls
    under LLM/tool spans. Stock OTel→norm only has AgentCall containment plus
    ToolCall annotations. Comparing that shared tree (modulo order) is the
    contract; native-only nesting is ignored.
    """
    if mapped.startswith("has") and mapped != "hasProcessingCall" and src_key[0] == "AgentCall":
        return True
    return False


def _canonical_subgraph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    allowed: frozenset[str],
) -> dict[str, Any]:
    """Order-insensitive projection of the overlapping KG."""
    by_span_agent = _agent_by_span(nodes)
    by_span_node = _span_index(nodes)
    kept = [
        node
        for node in nodes
        if node_type(node) in allowed and not _is_synthesized(node) and not _is_end_governance(node)
    ]
    keys = {str(node.get("id") or ""): _logical_key(node, agent_by_span=by_span_agent) for node in kept}
    referenced_agents = {key[1] for key in keys.values() if key[0] == "AgentCall"}
    referenced_tools = {key[1] for key in keys.values() if key[0] == "ToolCall"}
    keys = {
        nid: key
        for nid, key in keys.items()
        if not (
            (key[0] == "Agent" and key[1] not in referenced_agents)
            or (key[0] == "Tool" and key[1] not in referenced_tools)
        )
    }
    node_keys = sorted(keys.values())
    edge_keys = []
    linked_children: set[str] = set()
    for edge in edges:
        src = str(edge.get("from_id") or edge.get("source_id") or "")
        dst = str(edge.get("to_id") or edge.get("target_id") or "")
        if src not in keys or dst not in keys:
            continue
        etype = str(edge.get("edge_type") or edge.get("@type") or "")
        mapped = _canonical_edge_type(etype, keys[src][0], keys[dst][0])
        if mapped is None:
            continue
        src_key, dst_key = keys[src], keys[dst]
        if mapped == "contributesTo" and src_key[0] == "AgentCall" and dst_key[0] == "ContextContribution":
            src_key, dst_key = dst_key, src_key
        elif mapped == "annotates" and dst_key[0] == "GovernanceEvent" and src_key[0] != "GovernanceEvent":
            src_key, dst_key = dst_key, src_key
        if not _keep_canonical_edge(mapped, src_key, dst_key):
            continue
        edge_keys.append((mapped, src_key, dst_key))
        linked_children.add(src if mapped == "annotates" else dst)
    # OTel via stock norm often omits AgentCall→child containment; recover it
    # from parentSpanId so both paths share the same call tree (modulo order).
    for node in kept:
        child_key = keys.get(str(node.get("id") or ""))
        if child_key is None:
            continue
        parent_edge = _CONTAINS_TO_OXP.get(child_key[0])
        if parent_edge is None:
            continue
        if child_key[0] in {"AgentCall", "ProcessingCall", "ContextContribution", "GovernanceEvent"}:
            continue
        ancestor = _enclosing_agent_call(node, by_span=by_span_node)
        if ancestor is None:
            continue
        parent_key = keys.get(str(ancestor.get("id") or "")) or _logical_key(
            ancestor, agent_by_span=by_span_agent
        )
        item = (parent_edge, parent_key, child_key)
        child_id = str(node.get("id") or "")
        if child_id in linked_children:
            continue
        if not _keep_canonical_edge(item[0], item[1], item[2]):
            continue
        linked_children.add(child_id)
        edge_keys.append(item)
    return {
        "nodes": node_keys,
        "edges": sorted(edge_keys),
    }


def _identity_bag(nodes: Iterable[dict[str, Any]], allowed: frozenset[str]) -> dict[str, Counter[str]]:
    bags: dict[str, Counter[str]] = {ntype: Counter() for ntype in allowed}
    for node in nodes:
        ntype = node_type(node)
        if ntype not in allowed or _is_synthesized(node) or _is_end_governance(node):
            continue
        bags[ntype][_identity_name(node)] += 1
    return bags


def _span_suffix(name: str) -> str:
    text = str(name or "")
    if "." not in text:
        return ""
    return text.rsplit(".", 1)[-1]


def materialize_extension_nodes(spans: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn observe-sdk MAS extension suffixes into typed KG nodes."""
    nodes: list[dict[str, Any]] = []
    for row in to_clickhouse_spans(spans):
        suffix = _span_suffix(str(row.get("SpanName") or ""))
        ntype = _SUFFIX_TO_TYPE.get(suffix)
        if ntype is None:
            continue
        attrs = row.get("SpanAttributes") if isinstance(row.get("SpanAttributes"), dict) else {}
        name = (
            attrs.get("ioa_observe.entity.name")
            or attrs.get("mas.skill.name")
            or attrs.get("mas.tool.name")
            or attrs.get("mas.memory.type")
            or attrs.get("agent_id")
            or str(row.get("SpanName") or "").rsplit(".", 1)[0]
        )
        nodes.append(
            {
                "node_type": ntype,
                "@type": ntype,
                "id": str(row.get("SpanId") or f"{ntype}-{len(nodes)}"),
                "name": str(name or ""),
                "agentId": str(attrs.get("agent_id") or attrs.get("mas.agent.id") or ""),
                "spanId": str(row.get("SpanId") or ""),
                "parentSpanId": str(row.get("ParentSpanId") or ""),
            }
        )
    return nodes


def _native_kg(events: list[dict[str, Any]], *, mode: Mode) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    run_id = "unknown-run"
    for event in events:
        rid = event.get("run_id") or event.get("session_id")
        if rid:
            run_id = str(rid)
            break
    events = _align_reused_call_ids(events)
    complete = mode == "complete"
    doc = build_kg_document(
        events,
        run_id=run_id,
        include_trajectory=complete,
        include_provenance=complete,
        include_governance=complete,
        fill_synthesized_processing_defaults=False,
    )
    return list(doc.get("nodes") or []), list(doc.get("edges") or [])


def _otel_kg(events: list[dict[str, Any]], *, mode: Mode) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from mas.library.telemetry.conversion.layers import ExportLayers
    from mas.library.telemetry.conversion.replay import replay_events_file
    import tempfile
    from pathlib import Path
    import json

    layers = ExportLayers.complete() if mode == "complete" else ExportLayers.oxp_default()
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as handle:
        events_path = Path(handle.name)
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as handle:
        spans_path = Path(handle.name)
    try:
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
        replay_events_file(
            events_path,
            spans_path,
            converter_profile="observe_sdk",
            export_layers=layers,
            extensions=mode == "complete",
        )
        spans = [
            json.loads(line)
            for line in spans_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    finally:
        events_path.unlink(missing_ok=True)
        spans_path.unlink(missing_ok=True)

    nodes, edges = normalize_otel(spans)
    if mode == "complete":
        keep = DEFAULT_COMPARE_TYPES | {"Session", "MAS", "MASCall", "LLM"}
        core = [node for node in nodes if node_type(node) in keep]
        nodes = core + materialize_extension_nodes(spans)
    return nodes, edges, spans


def compare_native_and_otel(
    events: list[dict[str, Any]],
    *,
    mode: Mode,
) -> dict[str, Any]:
    """Return a type-count / identity comparison for one mode."""
    allowed = COMPLETE_COMPARE_TYPES if mode == "complete" else DEFAULT_COMPARE_TYPES
    native_nodes, native_edges = _native_kg(events, mode=mode)
    otel_nodes, otel_edges, spans = _otel_kg(events, mode=mode)
    native_canon = _canonical_subgraph(native_nodes, native_edges, allowed)
    otel_canon = _canonical_subgraph(otel_nodes, otel_edges, allowed)
    same_trace = Counter(native_canon["nodes"]) == Counter(otel_canon["nodes"]) and Counter(
        native_canon["edges"]
    ) == Counter(otel_canon["edges"])
    native_counts = Counter(ntype for ntype, _ in native_canon["nodes"])
    otel_counts = Counter(ntype for ntype, _ in otel_canon["nodes"])
    mismatched = sorted(ntype for ntype in allowed if native_counts[ntype] != otel_counts[ntype])
    native_ids = _identity_bag(native_nodes, allowed)
    otel_ids = _identity_bag(otel_nodes, allowed)
    native_sessions = sum(1 for node in native_nodes if node_type(node) == "Session")
    otel_sessions = sum(1 for node in otel_nodes if node_type(node) == "Session")
    return {
        "mode": mode,
        "allowed_types": sorted(allowed),
        "native_counts": dict(native_counts),
        "otel_counts": dict(otel_counts),
        "native_identities": {k: dict(v) for k, v in native_ids.items() if v},
        "otel_identities": {k: dict(v) for k, v in otel_ids.items() if v},
        "mismatched_types": mismatched,
        "equivalent": not mismatched,
        "same_trace": same_trace,
        "canonical_diff": {
            "native_nodes": native_canon["nodes"],
            "otel_nodes": otel_canon["nodes"],
            "native_edges": native_canon["edges"],
            "otel_edges": otel_canon["edges"],
        } if not same_trace else {},
        "session_present": {"native": native_sessions > 0, "otel": otel_sessions > 0},
        "span_count": len(spans),
        "native_node_count": len(native_nodes),
        "otel_node_count": len(otel_nodes),
        "native_edge_count": len(native_edges),
        "otel_edge_count": len(otel_edges),
    }


def compare_both_modes(events: list[dict[str, Any]]) -> dict[str, Any]:
    default = compare_native_and_otel(events, mode="default")
    complete = compare_native_and_otel(events, mode="complete")
    return {
        "default": default,
        "complete": complete,
        "equivalent": bool(default["equivalent"] and complete["equivalent"]),
    }

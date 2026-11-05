#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for point-in-time / annotation event kinds: ``CallAnnotation``
(routing, context_assembled, state_update_*, agent_communication_*,
checkpoint_*, observability_*_execute_*, user_input/output, ...),
``GovernanceEvent`` (audit, policy_*, budget_event, governance_*, ...), and
``ContextContribution`` (context_part_contributed).

These carry no ``call_id`` by design — they are attached to their nearest
enclosing structural call later, in ``finalize()``
(``core.graph_builder._resolve_annotation_edges``), which needs the full
call-node set to find the tightest enclosing window.

Also updates the builder's routing-derived parent-call-id inference state
(``_pending_parent``) when a ``routing`` event names a source and target
agent: that is used by ``handlers/agent.py``'s ``execution_start`` handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder

# Mapping of raw annotation event fields to camelCase node properties.
# Kept at module level to avoid re-allocating the dict on every annotation.
_ANN_KEY_MAP: Dict[str, str] = {
    "source_agent_id": "sourceAgentId",
    "target_agent_id": "targetAgentId",
    "task": "task",
    "correlation_id": "correlationId",
    "status": "status",
    "segments": "segments",
    "total_tokens": "totalTokens",
    "operation": "operation",
    "target": "target",
    "from": "from",
    "to": "to",
    "message_type": "messageType",
    "policy_id": "policyId",
    "decision": "decision",
    "reason": "reason",
    "denied_call_id": "deniedCallId",
    "budget_scope": "budgetScope",
    "amount": "amount",
    "intervention_type": "interventionType",
}

_GOVERNANCE_KINDS = frozenset(
    {
        "audit",
        "policy_denial",
        "policy_allow",
        "budget_event",
        "transformation_event",
        "control_intervention",
        "hitl_gate",
        "governance_denied",
        "governance_checked",
    }
)


def handle_annotation(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    """Handle CallAnnotation / GovernanceEvent kinds."""
    from mas.library.kg.core.graph_builder import _annotation_id

    kind = event.get("kind", "")
    run_id = event.get("run_id", "")
    agent_id = event.get("agent_id", "")
    local_name = event.get("mas_class", "CallAnnotation")

    if kind == "routing":
        source = event.get("source_agent_id", "")
        target = event.get("target_agent_id", "")
        src_exec = builder._active_exec.get(source)
        if source and target and src_exec:
            builder._pending_parent[target] = src_exec

    # routing/routing_result events carry agent_id=null but
    # source_agent_id=<emitter>.  Fall back so the annotates resolver
    # can match by agent.
    effective_agent_id = agent_id or event.get("source_agent_id", "")
    ann_id = f"ann-{_annotation_id(event, run_id)}"
    ann_node: Dict[str, Any] = {
        "node_type": local_name,
        "id": ann_id,
        "annotationId": ann_id,
        "agentId": effective_agent_id,
        "kind": kind,
        "timestamp": event.get("timestamp"),
        "runId": run_id,
    }
    for raw_field, camel_field in _ANN_KEY_MAP.items():
        if event.get(raw_field) is not None:
            ann_node[camel_field] = event[raw_field]
    if kind in _GOVERNANCE_KINDS or kind.startswith("governance_"):
        ann_node["annotationKind"] = kind
        ann_node["block"] = "governance"
    elif kind.startswith("checkpoint_"):
        ann_node["annotationKind"] = kind
        ann_node["block"] = "governance"

    builder.raw_annotations.append(ann_node)
    return [ann_node]


def handle_context_contribution(
    event: Dict[str, Any], builder: "NativeGraphBuilder"
) -> List[Dict[str, Any]]:
    """Handle context_part_contributed (L4 context provenance) events."""
    from mas.library.kg.core.graph_builder import _annotation_id

    run_id = event.get("run_id", "")
    agent_id = event.get("agent_id", "")
    if str(agent_id).strip().lower() in {"", "agent", "unknown"}:
        for aid in reversed(list(getattr(builder, "_active_exec", {}) or {})):
            if str(aid).strip().lower() not in {"", "agent", "unknown", "mas"}:
                agent_id = aid
                break
    from mas.library.kg.core.oxp_models import mas_namespace

    _MAS_NS = mas_namespace()

    part_id = event.get("part_id", _annotation_id(event, run_id))
    parents = list(event.get("parents") or (event.get("provenance") or {}).get("parents") or [])
    cpr_node: Dict[str, Any] = {
        "node_type": "ContextContribution",
        "id": f"cpr-{part_id}",
        "masUri": _MAS_NS + "ContextContribution",
        "agentId": agent_id,
        "partId": part_id,
        "parents": parents,
        "source": event.get("source", ""),
        "sectionId": event.get("section_id", ""),
        "sourceType": event.get("source_type", "unknown"),
        "accessMechanism": event.get("access_mechanism", "inject"),
        "mechanism": event.get("access_mechanism", "inject"),
        "cause": event.get("cause", "context_manager"),
        "causeType": event.get("cause_type", "deterministic"),
        "tokenEstimate": event.get("token_estimate", 0),
        "retained": event.get("retained", True),
        "timestamp": event.get("timestamp"),
        "runId": run_id,
    }
    if not cpr_node["retained"]:
        cpr_node["evictionReason"] = event.get("eviction_reason", "budget_exceeded")
    if event.get("sensitivity"):
        cpr_node["sensitivity"] = event["sensitivity"]
    content = str(event.get("content") or event.get("content_preview") or "")
    if content:
        cpr_node["content"] = content
        cpr_node["contentPreview"] = content[:200]

    # contributesTo edge — link to LLMCall by llm_call_id when known;
    # otherwise deferred to finalize()'s agent+timestamp containment
    # resolution (same as CallAnnotation).
    llm_call_id = event.get("llm_call_id", "")
    if llm_call_id:
        builder.cpr_nodes.append(cpr_node)
        builder.edges.append(
            {
                "edge_type": "contributesTo",
                "from_id": cpr_node["id"],
                "from_type": "annotation",
                "to_id": llm_call_id,
                "to_type": "call",
            }
        )
    else:
        builder.raw_annotations.append(cpr_node)

    return [cpr_node]

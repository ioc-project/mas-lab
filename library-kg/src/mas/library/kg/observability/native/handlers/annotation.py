#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for point-in-time event kinds: ``CallAnnotation`` (routing, context_assembled,
state_update_*, agent_communication_*, checkpoint_*, observability_*_execute_*,
user_input/output, ...), ``GovernanceEvent`` (audit, policy_*, budget_event,
governance_*, ...) and ``ContextContribution`` (context_part_contributed).

Each is a node of its own with the SCOPE properties: ``eventKind`` (the record's kind),
``eventTime`` (its timestamp) and ``payload`` (the other fields the record carries, as
JSON). It is linked to a call only when its record names that call: ``annotates`` to the
call in ``call_id`` (else ``parent_call_id``). The ``call_id`` of an interval pair
(``<name>_start`` / ``<name>_end``, e.g. ``state_update_*``, ``wait_state_*``) is the
interval's own key and no node, so the pair ``annotates`` its ``parent_call_id``. A
ContextContribution ``annotates`` the LLM call in ``llm_call_id``. The name is resolved to
the call's node when the graph is finalized; no call is looked up by time.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

from mas.library.kg.core.native_graph_builder import (
    call_node_id,
    call_ref,
    session_placeholder_id,
)

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder

# Fields of an annotation record that have no SCOPE property of their own; they are
# written as the ``payload`` JSON object, under the record's own names.
_ANNOTATION_PAYLOAD_FIELDS = (
    "source_agent_id",
    "target_agent_id",
    "task",
    "correlation_id",
    "status",
    "segments",
    "total_tokens",
    "operation",
    "target",
    "from",
    "to",
    "message_type",
    "denied_call_id",
    "budget_scope",
    "amount",
    "intervention_type",
)
_CONTEXT_PAYLOAD_FIELDS = (
    "parents",
    "section_id",
    "source_type",
    "cause",
    "cause_type",
    "sensitivity",
    "eviction_reason",
    "content",
)


def _payload(event: Dict[str, Any], fields: tuple[str, ...]) -> str:
    """The fields the record carries, as one JSON object (empty when it carries none)."""
    values = {key: event[key] for key in fields if event.get(key) not in (None, "", [])}
    return json.dumps(values, sort_keys=True, separators=(",", ":"), default=str) if values else ""


def stated_decision(event: Dict[str, Any]) -> str:
    """The decision a record states (``decision_type``, ``decision``, ``outcome`` or a HITL
    ``resolution``), or ``""``."""
    for key in ("decision_type", "decision", "outcome", "resolution"):
        value = event.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def annotated_call(event: Dict[str, Any]) -> str:
    """The call a point annotation is about, as the record names it.

    An interval record's ``call_id`` is its own key (no node): its ``parent_call_id`` is
    the call. A point record's ``call_id`` is the call it decides on or reports.
    """
    kind = str(event.get("kind") or "")
    parent = str(event.get("parent_call_id") or "")
    own = str(event.get("call_id") or "")
    if kind.endswith(("_start", "_end")):
        return parent
    return own or parent


def annotation_node(
    event: Dict[str, Any], local_name: str, builder: "NativeGraphBuilder"
) -> Dict[str, Any]:
    """The CallAnnotation / GovernanceEvent node a record maps to (added to ``builder``)."""
    from mas.library.kg.core.graph_builder import _annotation_id

    run_id = event.get("run_id", "")
    node: Dict[str, Any] = {
        "node_type": local_name,
        "id": f"ann-{_annotation_id(event, run_id)}",
        "name": event.get("kind", ""),  # an annotation is named by its kind
        "eventKind": event.get("kind", ""),
        "eventTime": event.get("timestamp"),
        "sessionId": session_placeholder_id(str(run_id)),
        "payload": _payload(event, _ANNOTATION_PAYLOAD_FIELDS),
        "runId": run_id,
    }
    if local_name == "GovernanceEvent":
        node["decision"] = stated_decision(event)
        node["policyName"] = event.get("policy_name") or event.get("policy_id")
        node["decisionReason"] = event.get("reason")
    builder.add_node(node)
    return node


def handle_annotation(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    """Handle CallAnnotation / GovernanceEvent kinds."""
    run_id = event.get("run_id", "")
    ann_node = annotation_node(event, event.get("mas_class", "CallAnnotation"), builder)
    target = annotated_call(event)
    if target:
        builder.add_edge("annotates", ann_node["id"], call_ref(run_id, target))
    return [ann_node]


def handle_context_contribution(
    event: Dict[str, Any], builder: "NativeGraphBuilder"
) -> List[Dict[str, Any]]:
    """Handle context_part_contributed events: one part of the context of an LLM call."""
    from mas.library.kg.core.graph_builder import _annotation_id

    run_id = event.get("run_id", "")
    part_id = event.get("part_id", _annotation_id(event, run_id))
    node: Dict[str, Any] = {
        "node_type": "ContextContribution",
        "id": f"cpr-{part_id}",
        "name": event.get("kind", ""),  # an annotation is named by its kind
        "eventKind": event.get("kind", ""),
        "eventTime": event.get("timestamp"),
        "sessionId": session_placeholder_id(str(run_id)),
        "partId": part_id,
        "contextSource": event.get("source", ""),
        "contributionMechanism": event.get("access_mechanism", ""),
        "payload": _payload(event, _CONTEXT_PAYLOAD_FIELDS),
        "runId": run_id,
    }
    if event.get("token_estimate") is not None:
        node["tokenEstimate"] = int(event["token_estimate"])
    if event.get("retained") is not None:
        node["retained"] = bool(event["retained"])

    builder.add_node(node)
    llm_call_id = str(event.get("llm_call_id") or "")
    if llm_call_id:
        builder.add_edge("annotates", node["id"], call_node_id("LLMCall", run_id, llm_call_id))
    else:
        # A contribution's own ``call_id`` is its part id; it names its call as the parent.
        target = str(event.get("parent_call_id") or "")
        if target:
            builder.add_edge("annotates", node["id"], call_ref(run_id, target))
    return [node]

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""State/Transition synthesis for the OTel path over a released ``norm``.

The native fold no longer synthesizes States (it reads them from the events). This module
is what completes a graph that a ``norm`` without explicit State ids returns
(:mod:`mas.library.kg.observability.otel_completion`); it goes away with that path.

State/Transition trajectory construction, shared by every call-kind handler.

Every call node (AgentCall, LLMCall, ToolCall, ProcessingCall) that has both
an input and output content value gets a ``State -> Transition -> State``
triple: an initial State (the call's input), a final State (the call's
output), and a Transition connecting them that `realizes` the call.

This module is pure call-node -> (State/Transition/edges) construction with
no knowledge of event kinds or dispatch -- ``otel_completion``
calls :func:`synthesize_states_and_transitions`
once over the complete set of call nodes:
the sibling-chain rewiring below (a call's real input *is* its immediately
preceding sibling's real output) needs the full set of siblings under the
same parent to order and chain them, so it cannot be done per-event.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Max characters stored in State.content nodes (Neo4j property size guard).
_STATE_CONTENT_MAX_LEN = 8000


def _content_hash(content: str) -> str:
    """SHA-256 hex of content — 64 chars, stable across sessions.

    This is a *content* hash, NOT a node identity.  Two State nodes from
    different trajectories may share the same content_hash (enabling
    embedding reuse) yet have different identities (state_node_id).

    Python analogy::

        hash(obj)   → content_hash   # same value = same content
        id(obj)     → state_node_id  # unique per instance / session
    """
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class StateTransitionSpec:
    """Specification for generating a State/Transition triple from a call node."""

    prefix: str  # e.g. "agent", "llm", "tool", "proc"
    action_type: str  # e.g. "agent_delegation", "llm_call"
    in_content: str  # initial state content
    out_content: str  # final state content
    in_semantic: str = "initial"  # semanticType for initial state
    out_semantic: str = "final"  # semanticType for final state
    edge_type: str = "sequential"
    applied_operator: str = "sequential_compose"
    # Extra fields to merge into the Transition node
    transition_extras: Dict[str, Any] = field(default_factory=dict)
    # Extra fields for initial/final state nodes
    initial_extras: Dict[str, Any] = field(default_factory=dict)
    final_extras: Dict[str, Any] = field(default_factory=dict)


def state_transition_ids(call_id: str, spec: StateTransitionSpec) -> Tuple[str, str, str]:
    """Derive a call's (sid_in, sid_out, tid) -- deterministic from call_id/spec
    alone, so callers can learn a call's State/Transition ids (e.g. to order
    and chain siblings) without building its full triple.
    """
    sid_in = f"state-{spec.prefix}-{call_id}-{spec.in_semantic}"
    sid_out = f"state-{spec.prefix}-{call_id}-{spec.out_semantic}"
    # Keep the same id format as the original code for backward compat
    if spec.prefix == "llm":
        sid_in, sid_out = f"state-llm-{call_id}-prompt", f"state-llm-{call_id}-completion"
    elif spec.prefix == "tool":
        sid_in, sid_out = f"state-tool-{call_id}-args", f"state-tool-{call_id}-result"
    elif spec.prefix == "proc":
        sid_in, sid_out = f"state-proc-{call_id}-input", f"state-proc-{call_id}-output"
    elif spec.prefix == "agent":
        sid_in, sid_out = f"state-agent-{call_id}-initial", f"state-agent-{call_id}-final"
    return sid_in, sid_out, f"trans-{spec.prefix}-{call_id}"


def make_state_transition_triple(
    node: Dict[str, Any],
    spec: StateTransitionSpec,
    override_from_state: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    """Build (state_nodes, transition_node, edges) for a single call node.

    ``override_from_state``, when given, is the id of the State already
    created for the immediately preceding sibling call's output. It replaces
    this call's own synthesized "input" State outright rather than letting
    both coexist as two distinct input states feeding the same Transition —
    the ontology models a single content-delta chain per parent scope
    (mas-ontology.ttl: "context(n) = context0 ⊗ content(1) ⊗ … ⊗
    content(n)"), so a call's real input *is* its predecessor's real output,
    not a second, usually-empty state of its own. See the sibling-chain
    rewiring in :func:`synthesize_states_and_transitions`.
    """
    call_id = node["callId"]
    sid_in, sid_out, tid = state_transition_ids(call_id, spec)
    _sess = f"session-{node.get('runId', 'unknown')}"

    state_nodes: List[Dict[str, Any]] = []
    if override_from_state:
        sid_in = override_from_state
    else:
        state_nodes.append(
            {
                "node_type": "State",
                "id": sid_in,
                "stateNodeId": sid_in,
                "contentHash": _content_hash(spec.in_content),
                "sessionId": _sess,
                "content": spec.in_content[:_STATE_CONTENT_MAX_LEN],
                "semanticType": spec.in_semantic,
                **spec.initial_extras,
            }
        )
    s_final: Dict[str, Any] = {
        "node_type": "State",
        "id": sid_out,
        "stateNodeId": sid_out,
        "contentHash": _content_hash(spec.out_content),
        "sessionId": _sess,
        "content": spec.out_content[:_STATE_CONTENT_MAX_LEN],
        "semanticType": spec.out_semantic,
        **spec.final_extras,
    }
    trans: Dict[str, Any] = {
        "node_type": "Transition",
        "id": tid,
        "transitionId": tid,
        "sessionId": _sess,
        "fromState": sid_in,
        "toState": sid_out,
        "realizesCallId": call_id,
        "edgeType": spec.edge_type,
        "actionType": spec.action_type,
        "appliedOperator": spec.applied_operator,
        "transitionTimestamp": node.get("startTime"),
        "transitionDuration": (
            float(node["endTime"]) - float(node["startTime"])
            if node.get("startTime") and node.get("endTime")
            else None
        ),
        **spec.transition_extras,
    }

    state_nodes.append(s_final)
    st_edges: List[Dict[str, Any]] = [
        {
            "edge_type": "hasInitialState",
            "from_id": call_id,
            "from_type": "call",
            "to_id": sid_in,
            "to_type": "state",
        },
        {
            "edge_type": "hasFinalState",
            "from_id": call_id,
            "from_type": "call",
            "to_id": sid_out,
            "to_type": "state",
        },
        {
            "edge_type": "inputTo",
            "from_id": sid_in,
            "from_type": "state",
            "to_id": tid,
            "to_type": "transition",
        },
        {
            "edge_type": "leadsTo",
            "from_id": tid,
            "from_type": "transition",
            "to_id": sid_out,
            "to_type": "state",
        },
        {
            "edge_type": "fromState",
            "from_id": tid,
            "from_type": "transition",
            "to_id": sid_in,
            "to_type": "state",
        },
        {
            "edge_type": "toState",
            "from_id": tid,
            "from_type": "transition",
            "to_id": sid_out,
            "to_type": "state",
        },
        {
            "edge_type": "realizes",
            "from_id": tid,
            "from_type": "transition",
            "to_id": call_id,
            "to_type": "call",
        },
    ]

    return state_nodes, trans, st_edges


def build_processing_spec(
    node: Dict[str, Any],
    in_content: str,
    out_content: str,
) -> StateTransitionSpec:
    """Build a StateTransitionSpec for ProcessingCall nodes.

    Encapsulates the delta-type inference, token metrics, and context strategy
    logic that drives the Transition's context-management annotations.
    """
    _pk = node.get("processingKind") or node.get("processingName") or ""
    _delta_map = {
        "context_truncation": "remove",
        "context_summarization": "compress",
        "context_paging_out": "page_out",
        "context_paging_in": "page_in",
        "context_swap": "replace",
        "context_forgetting": "remove",
        "context_injection": "add",
        "context_assembly": "add",
        "prompt_engineering": "add",
        "memory_injection": "add",
        "ontology_injection": "add",
    }
    _delta_type = node.get("deltaType") or node.get("delta_type") or _delta_map.get(_pk, "add")
    _action_type = _pk if _pk.startswith("context_") else "context_assembly"

    # Token metrics
    _tok_in = node.get("tokensInput") or node.get("tokens_input") or 0
    _tok_out = node.get("tokensOutput") or node.get("tokens_output") or 0
    _tok_delta = _tok_out - _tok_in if (_tok_in or _tok_out) else None
    _comp_ratio = node.get("compressionRatio") or node.get("compression_ratio")
    if _comp_ratio is None and _tok_in and _tok_in > 0:
        _comp_ratio = _tok_out / _tok_in

    # Build transition extras
    trans_extras: Dict[str, Any] = {"transitionDeltaType": _delta_type}
    if _tok_in:
        trans_extras["contextTokensIn"] = _tok_in
    if _tok_out:
        trans_extras["contextTokensOut"] = _tok_out
    if _tok_delta is not None:
        trans_extras["contextTokensDelta"] = _tok_delta
    if _comp_ratio is not None:
        trans_extras["compressionRatio"] = _comp_ratio
    if node.get("contextStrategy") or node.get("context_strategy"):
        trans_extras["contextStrategy"] = node.get("contextStrategy") or node.get(
            "context_strategy"
        )

    return StateTransitionSpec(
        prefix="proc",
        action_type=_action_type,
        in_content=in_content,
        out_content=out_content,
        applied_operator=node.get("appliedOperator")
        or node.get("applied_operator")
        or "context_transform",
        initial_extras={"deltaType": "initial", "contextWindowSize": _tok_in if _tok_in else None},
        final_extras={
            "deltaType": _delta_type,
            "contextWindowSize": _tok_out if _tok_out else None,
        },
        transition_extras=trans_extras,
    )


def _spec_for_call(node: Dict[str, Any], agent_output_text) -> Optional[StateTransitionSpec]:
    """Return the StateTransitionSpec for one call node, or None if it has
    neither input nor output content (nothing to represent)."""
    local_name = node["node_type"]

    if local_name == "AgentCall":
        in_content = str(node.get("inputContent") or "")
        out_content = agent_output_text(node)
        if not (in_content or out_content):
            return None
        return StateTransitionSpec(
            prefix="agent",
            action_type="agent_delegation",
            in_content=in_content,
            out_content=out_content,
        )

    if local_name == "LLMCall":
        prompt = str(node.get("prompt") or "")
        completion = str(node.get("completion") or "")
        if not (prompt or completion):
            return None
        return StateTransitionSpec(
            prefix="llm",
            action_type="llm_call",
            in_content=prompt,
            out_content=completion,
            in_semantic="initial",
            out_semantic="final",
        )

    if local_name == "ToolCall":
        args_content = str(node.get("toolArguments") or node.get("arguments") or "")
        result_content = str(node.get("toolOutput") or node.get("result") or "")
        if not (args_content or result_content):
            return None
        edge_type = "parallel" if node.get("barrierId") else "sequential"
        operator = "parallel_merge" if node.get("barrierId") else "sequential_compose"
        extras: Dict[str, Any] = {}
        if node.get("barrierId"):
            extras["barrierId"] = node["barrierId"]
        return StateTransitionSpec(
            prefix="tool",
            action_type="tool_call",
            in_content=args_content,
            out_content=result_content,
            edge_type=edge_type,
            applied_operator=operator,
            initial_extras={"deltaType": "add"},
            final_extras={"deltaType": "add"},
            transition_extras=extras,
        )

    if local_name == "ProcessingCall":
        in_content = str(node.get("inputContent") or node.get("input") or "")
        out_content = str(node.get("processingOutput") or node.get("output") or "")
        if not (in_content or out_content):
            return None
        return build_processing_spec(node, in_content, out_content)

    return None


def synthesize_states_and_transitions(
    call_nodes: Dict[str, Dict[str, Any]],
    agent_output_text,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Create State and Transition nodes for every AgentCall/LLMCall/ToolCall/
    ProcessingCall that has input and/or output content.

    ``agent_output_text`` is injected (rather than imported) to avoid a
    dependency on ``core.output_rendering`` from this module -- it is called
    exactly once per AgentCall to render its final output text.

    Sibling-chain rewiring
    ----------------------
    Each call's (sid_out, timestamp) is deterministic from call_id/spec
    alone (:func:`state_transition_ids`), so siblings can be ordered and
    chained without building a full triple per call first. Chaining is
    scoped to calls that share the same parentCallId (true siblings in the
    call tree) — never a parent's own wrapper transition with its own
    children, which would otherwise produce backwards inputTo edges purely
    because a flat session-wide timestamp sort placed a parent next to one
    of its children.

    Every sibling after the first in timestamp order gets
    ``override_from_state`` set to the immediately preceding sibling's
    sid_out, so its own synthesized input State never coexists with the
    chained one as a second, usually-empty input to the same Transition.
    """
    entries: List[Tuple[Dict[str, Any], StateTransitionSpec]] = []
    for node in call_nodes.values():
        spec = _spec_for_call(node, agent_output_text)
        if spec is not None:
            entries.append((node, spec))

    by_group: Dict[Tuple[str, str], List[Tuple[float, str, str]]] = {}
    for node, spec in entries:
        sess = f"session-{node.get('runId', 'unknown')}"
        parent = str(node.get("parentCallId") or "")
        _, sid_out, _ = state_transition_ids(node["callId"], spec)
        timestamp = float(node.get("startTime") or 0)
        by_group.setdefault((sess, parent), []).append((timestamp, node["callId"], sid_out))

    override_from: Dict[str, str] = {}
    for siblings in by_group.values():
        siblings.sort(key=lambda entry: entry[0])
        for (_, _cur_id, cur_out), (_, nxt_id, _) in zip(siblings, siblings[1:]):
            override_from[nxt_id] = cur_out

    state_nodes: List[Dict[str, Any]] = []
    transition_nodes: List[Dict[str, Any]] = []
    all_edges: List[Dict[str, Any]] = []

    for node, spec in entries:
        call_id = node["callId"]
        s_nodes, trans, edges = make_state_transition_triple(
            node, spec, override_from_state=override_from.get(call_id)
        )
        state_nodes.extend(s_nodes)
        transition_nodes.append(trans)
        all_edges.extend(edges)

    return state_nodes + transition_nodes, all_edges


def session_trajectory_anchors(
    session_nodes: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    call_node_by_call_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """``hasInitialState`` / ``hasFinalState`` edges from each Session to its trajectory ends.

    A session starts at the first top-level Transition (one whose call has no
    parent) and ends at the last. ``call_node_by_call_id`` is keyed by each
    call's own ``callId``, which is what a Transition's ``realizesCallId`` holds.
    """
    transitions_by_session: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        if node.get("node_type") != "Transition":
            continue
        sess = str(node.get("sessionId") or "")
        if sess:
            transitions_by_session.setdefault(sess, []).append(node)

    edges: List[Dict[str, Any]] = []
    for session_node in session_nodes:
        session_id = str(session_node.get("sessionId") or "")
        session_transitions = transitions_by_session.get(session_id, [])
        if not session_transitions:
            continue
        top_level = [
            t
            for t in session_transitions
            if not call_node_by_call_id.get(t.get("realizesCallId", ""), {}).get("parentCallId")
        ]
        ordered = sorted(
            top_level or session_transitions,
            key=lambda node: float(node.get("transitionTimestamp") or 0),
        )
        for edge_type, state in (
            ("hasInitialState", ordered[0].get("fromState")),
            ("hasFinalState", ordered[-1].get("toState")),
        ):
            if state:
                edges.append(
                    {
                        "edge_type": edge_type,
                        "from_id": session_node["id"],
                        "from_type": "session",
                        "to_id": state,
                        "to_type": "state",
                    }
                )
    return edges

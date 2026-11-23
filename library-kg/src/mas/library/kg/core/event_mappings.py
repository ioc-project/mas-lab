"""Declarative mapping tables for internal event kind → ontology KG class.

To add a new event kind: add one entry to KIND_TO_CLASS.
To suppress a kind silently: map it to None.
Any kind not in KIND_TO_CLASS raises UnknownSpanBoundaryError at normalization time.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

# Event kind (exact string, including _start/_end suffix where applicable) → ontology class
# Reference: mas-ontology.ttl §1b ExecutionElement and Annotation subclasses
KIND_TO_CLASS: Dict[str, Optional[str]] = {
    # ------------------------------------------------------------------
    # Structural events — form the call tree (call_id + parent_call_id)
    # ------------------------------------------------------------------
    "tool_call_start": "ToolCall",
    "tool_call_end": "ToolCall",
    "llm_call_start": "LLMCall",
    "llm_call_end": "LLMCall",
    "execution_start": "AgentCall",
    "execution_end": "AgentCall",
    "mas_call_start": "MASCall",
    "mas_call_end": "MASCall",
    "rag_query_start": "RAGQuery",
    "rag_query_end": "RAGQuery",
    "memory_call_start": "MemoryCall",
    "memory_call_end": "MemoryCall",
    "memory_store_start": "MemoryCall",
    "memory_store_end": "MemoryCall",
    "memory_retrieve_start": "MemoryCall",
    "memory_retrieve_end": "MemoryCall",
    "processing_call_start": "ProcessingCall",
    "processing_call_end": "ProcessingCall",
    "skill_execution_start": "SkillCall",
    "skill_execution_end": "SkillCall",
    # thinking: the reasoning interval of one LLM call (call_id ``<llm_call_id>#think``,
    # parent_call_id the LLM call).
    "thinking_start": "ThinkingCall",
    "thinking_end": "ThinkingCall",
    "network_call_start": "ToolCall",
    "network_call_end": "ToolCall",
    # workflow_transition_* — no dedicated ontology class; modelled as ProcessingCall
    # (internal control-flow computation inside a MAS/Agent span).
    "workflow_transition_start": "ProcessingCall",
    "workflow_transition_end": "ProcessingCall",
    # ------------------------------------------------------------------
    # Annotation events — provide L2/L3 XAI context; attached to the
    # nearest enclosing structural node via an 'annotates' edge.
    # An interval pair (state_update_*) has its own call_id key, not a node.
    # ------------------------------------------------------------------
    "routing": "CallAnnotation",
    "routing_result": "CallAnnotation",
    "context_assembled": "CallAnnotation",
    "state_update_start": "CallAnnotation",
    "state_update_end": "CallAnnotation",
    "agent_communication_start": "CallAnnotation",
    "agent_communication_end": "CallAnnotation",
    # checkpoint_* is a check interval: a ProcessingCall (see CHECK_INTERVALS).
    "checkpoint_start": "ProcessingCall",
    "checkpoint_end": "ProcessingCall",
    # ------------------------------------------------------------------
    # Suppressed — redundant or legacy; not emitted by core
    # plugin (observability_plugin.py v2+).  Mapped to None so any
    # legacy events from older traces are silently skipped.
    # ------------------------------------------------------------------
    "prompt_build_start": None,  # redundant with llm_call_start.messages
    "prompt_build_end": None,
    "user_response": None,  # the reply is on execution_end; not a node
    "human_interaction": None,
    "delegated_agent_output": None,
    "skills_check": None,  # governance extension only (legacy)
    "object_upsert": None,  # domain-kg-extension only
    "object_link": None,
    "object_model_event": None,
    "user_input_request": None,  # not a call node
    # ------------------------------------------------------------------
    # L4 — Context Provenance
    # Per-part contribution events from the context assembler.
    # ------------------------------------------------------------------
    "context_part_contributed": "ContextContribution",
    # ------------------------------------------------------------------
    # Parallel groups are plain markers. The trajectory already says what they
    # mean: the members read one State (a fan-out of Transitions) and the
    # ``aggregate`` ProcessingCall at the group end is the join. SCOPE has no
    # ParallelGroup or Branch class, and no producer emits ``branch_*``.
    # ------------------------------------------------------------------
    "parallel_group_start": None,
    "parallel_group_end": None,
    # ------------------------------------------------------------------
    # L6 — Governance events (mas-ontology.ttl :GovernanceEvent, §21)
    # ------------------------------------------------------------------
    "audit": "GovernanceEvent",
    "policy_denial": "GovernanceEvent",
    "policy_allow": "GovernanceEvent",
    "budget_event": "GovernanceEvent",
    "transformation_event": "GovernanceEvent",
    "control_intervention": "GovernanceEvent",
    "hitl_gate": "GovernanceEvent",
    "governance_denied": "GovernanceEvent",
    "governance_checked": "GovernanceEvent",
    # The timing brackets of a check are work, not decisions: each start/end pair is one
    # ProcessingCall (see CHECK_INTERVALS); a stated outcome is a GovernanceEvent point.
    "obs_wrap_gov_authorize_start": "ProcessingCall",
    "obs_wrap_gov_authorize_end": "ProcessingCall",
    "obs_wrap_gov_validate_start": "ProcessingCall",
    "obs_wrap_gov_validate_end": "ProcessingCall",
    "governance_authorize_start": "ProcessingCall",
    "governance_authorize_end": "ProcessingCall",
    "governance_validate_start": "ProcessingCall",
    "governance_validate_end": "ProcessingCall",
    # Observability wrapper checkpoints (Mealy product symbols 3 & 5 — see
    # mealy-product-formal-design.md): record pre/post-execution state
    # independently of governance. Same runtime-wrapper pattern as obs_wrap_gov_*.
    "observability_pre_execute_start": "CallAnnotation",
    "observability_pre_execute_end": "CallAnnotation",
    "observability_post_execute_start": "CallAnnotation",
    "observability_post_execute_end": "CallAnnotation",
    # ------------------------------------------------------------------
    # L1 — Infrastructure events
    # Worker/endpoint presence recorded at execution start.
    # ------------------------------------------------------------------
    "infrastructure_info": "Worker",
    # ------------------------------------------------------------------
    # User I/O — UI-layer events, classified as annotations
    # ------------------------------------------------------------------
    "user_input": "CallAnnotation",
    "user_output": "CallAnnotation",
    # client_response — CallAnnotation whose mas.annotation.kind is
    # "client_response" (alias of user_output; see library-telemetry
    # conversion/mappings/trajectory.py register("user_output", "client_response")).
    "client_response": "CallAnnotation",
    # tool_result_injected — context provenance (tool result fed back into LLM)
    "tool_result_injected": "CallAnnotation",
    # Runtime wrappers / disabled observe-sdk categories. Mapped so default
    # native→KG does not warn; they are not SCOPE ingest classes.
    "boundary_ingress": "CallAnnotation",
    "boundary_egress": "CallAnnotation",
    "context_steer": "CallAnnotation",
    "compaction": "CallAnnotation",
    "wait_state_start": "CallAnnotation",
    "wait_state_end": "CallAnnotation",
    "contract_call_start": None,
    "contract_call_end": None,
    "governance_decision": "GovernanceEvent",
    "governance_event": "GovernanceEvent",
    "governance_policy": "GovernanceEvent",
    "hitl_request": "GovernanceEvent",
    # Transport — catalog declarations (Agent / Tool). Session, internals and
    # errors have no ontology class today; they are suppressed.
    "transport.message.sent": "Agent",
    "transport.message.received": "Agent",
    "transport.message.completed": "Agent",
    "transport.discovery": "Agent",
    "transport.session": None,
    "transport.internal": None,
    "transport.error": None,
}


# Check intervals: ``<name>_start`` / ``<name>_end`` pairs whose ``call_id`` is the interval's
# own key, whose ``parent_call_id`` names the visit they run in and ``checked_call_id`` the call
# they check (before or after it). Each is a ProcessingCall named ``<name>`` with
# ``processingType`` ``<name>``, with the State it reads as its input and output.
CHECK_INTERVALS: FrozenSet[str] = frozenset(
    {
        "governance_authorize",
        "governance_validate",
        "obs_wrap_gov_authorize",
        "obs_wrap_gov_validate",
        "checkpoint",
    }
)


# Opt-in categories: each names the ontology classes it adds to the default output.
# The default output holds the core classes only (Session, MASCall, AgentCall, LLMCall,
# ToolCall, ProcessingCall, State, Transition and the MAS / Agent / LLM / Tool /
# Processing catalog). Enabling a category adds its classes (and the edges that name
# them) on top; it never changes a core node. The filter acts on the class an event maps
# to, so realtime and batch folds drop the same events and the core graph is the same
# whether or not events of a disabled category are in the input.
CATEGORY_CLASSES: Dict[str, FrozenSet[str]] = {
    "capabilities": frozenset({"Skill", "SkillCall", "Memory", "MemoryCall", "RAGQuery"}),
    "annotations": frozenset({"CallAnnotation", "GovernanceEvent", "ContextContribution"}),
    "worker": frozenset({"Worker"}),
    "thinking": frozenset({"ThinkingCall"}),
}

TRANSPORT_EVENT_KINDS: FrozenSet[str] = frozenset(
    {
        "transport.message.sent",
        "transport.message.received",
        "transport.message.completed",
        "transport.discovery",
        "transport.session",
        "transport.internal",
        "transport.error",
    }
)

DEFAULT_CATEGORIES: FrozenSet[str] = frozenset()


def parse_categories(value: Any = None) -> FrozenSet[str]:
    """The enabled opt-in categories of ``value``.

    ``value`` is ``None`` (the default: none), a comma-separated string, or an iterable
    of names. ``all`` enables every category. An unknown name raises ``ValueError``.
    """
    if value is None:
        return DEFAULT_CATEGORIES
    names = (
        [part.strip() for part in value.split(",")]
        if isinstance(value, str)
        else [str(part).strip() for part in value]
    )
    names = [name for name in names if name]
    if "all" in names:
        return frozenset(CATEGORY_CLASSES)
    unknown = sorted(set(names) - set(CATEGORY_CLASSES))
    if unknown:
        raise ValueError(
            f"unknown categories {unknown}; choose from {sorted(CATEGORY_CLASSES)} or 'all'"
        )
    return frozenset(names)


def skipped_classes(categories: Any = None) -> FrozenSet[str]:
    """Classes to drop: those of every category that is not enabled."""
    enabled = parse_categories(categories)
    return frozenset().union(
        *(classes for name, classes in CATEGORY_CLASSES.items() if name not in enabled)
    )

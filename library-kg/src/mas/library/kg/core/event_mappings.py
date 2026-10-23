"""Declarative mapping tables for internal event kind → ontology KG class.

To add a new event kind: add one entry to KIND_TO_CLASS.
To suppress a kind silently: map it to None.
Any kind not in KIND_TO_CLASS raises UnknownSpanBoundaryError at normalization time.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

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
    "execution_start": "AgentCall",  # may become TaskCall below
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
    "network_call_start": "ToolCall",
    "network_call_end": "ToolCall",
    # workflow_transition_* — no dedicated ontology class; modelled as ProcessingCall
    # (internal control-flow computation inside a MAS/Agent span).
    "workflow_transition_start": "ProcessingCall",
    "workflow_transition_end": "ProcessingCall",
    # ------------------------------------------------------------------
    # Annotation events — provide L2/L3 XAI context; attached to the
    # nearest enclosing structural node via an 'annotates' edge.
    # No call_id — not first-class tree nodes.
    # ------------------------------------------------------------------
    "routing": "CallAnnotation",
    "routing_result": "CallAnnotation",
    "context_assembled": "CallAnnotation",
    "state_update_start": "CallAnnotation",
    "state_update_end": "CallAnnotation",
    "agent_communication_start": "CallAnnotation",
    "agent_communication_end": "CallAnnotation",
    "checkpoint_start": "CallAnnotation",
    "checkpoint_end": "CallAnnotation",
    # ------------------------------------------------------------------
    # Suppressed — redundant or legacy; not emitted by core
    # plugin (observability_plugin.py v2+).  Mapped to None so any
    # legacy events from older traces are silently skipped.
    # ------------------------------------------------------------------
    "prompt_build_start": None,  # redundant with llm_call_start.messages
    "prompt_build_end": None,
    "user_response": None,  # redundant with execution_end.output
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
    # L5 — Trajectory extensions
    # Explicit parallel group and branching topology events.
    # ------------------------------------------------------------------
    "parallel_group_start": "ParallelGroup",
    "parallel_group_end": "ParallelGroup",
    "branch_start": "Branch",
    "branch_end": "Branch",
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
    "obs_wrap_gov_authorize_start": "GovernanceEvent",
    "obs_wrap_gov_authorize_end": "GovernanceEvent",
    "obs_wrap_gov_validate_start": "GovernanceEvent",
    "obs_wrap_gov_validate_end": "GovernanceEvent",
    "governance_authorize_start": "GovernanceEvent",
    "governance_authorize_end": "GovernanceEvent",
    "governance_validate_start": "GovernanceEvent",
    "governance_validate_end": "GovernanceEvent",
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
}


# Layer name → frozenset of event kinds owned by that layer
LAYER_KINDS: Dict[str, FrozenSet[str]] = {
    "infrastructure": frozenset(
        {
            "infrastructure_info",
        }
    ),
    "provenance": frozenset(
        {
            "context_part_contributed",
        }
    ),
    "trajectory": frozenset(
        {
            "parallel_group_start",
            "parallel_group_end",
            "branch_start",
            "branch_end",
            "routing",
            "routing_result",
        }
    ),
    "governance": frozenset(
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
            "obs_wrap_gov_authorize_start",
            "obs_wrap_gov_authorize_end",
            "obs_wrap_gov_validate_start",
            "obs_wrap_gov_validate_end",
            "governance_authorize_start",
            "governance_authorize_end",
            "governance_validate_start",
            "governance_validate_end",
            "governance_decision",
            "governance_event",
            "governance_policy",
            "hitl_request",
        }
    ),
}

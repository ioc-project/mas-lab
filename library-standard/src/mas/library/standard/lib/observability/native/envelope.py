#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Appendix R envelope fields on native events.jsonl records."""

from __future__ import annotations

from typing import Any

# kind → (block, summand, mealy_symbol) — papers/mas-ontology-kg appendices R.2–R.3
_KIND_ENVELOPE: dict[str, tuple[str, str, str]] = {
    "mas_call_start": ("structural", "orchestrator", "RUN_START"),
    "mas_call_end": ("structural", "orchestrator", "RUN_END"),
    "execution_start": ("execution", "orchestrator", "AGENT_START"),
    "execution_end": ("execution", "orchestrator", "AGENT_END"),
    "user_response": ("execution", "orchestrator", "AGENT_END"),
    "llm_call_start": ("execution", "model", "LLM_CALL"),
    "thinking_start": ("execution", "model", "THINKING"),
    "thinking_end": ("execution", "model", "THINKING"),
    "llm_call_end": ("execution", "model", "LLM_CALL"),
    "tool_call_start": ("execution", "tool", "TOOL_CALL"),
    "tool_call_end": ("execution", "tool", "TOOL_CALL"),
    "memory_call_start": ("execution", "tool", "MEMORY_CALL"),
    "memory_call_end": ("execution", "tool", "MEMORY_CALL"),
    "memory_store_start": ("execution", "tool", "MEMORY_STORE"),
    "memory_store_end": ("execution", "tool", "MEMORY_STORE"),
    "memory_retrieve_start": ("execution", "tool", "MEMORY_RETRIEVE"),
    "memory_retrieve_end": ("execution", "tool", "MEMORY_RETRIEVE"),
    "rag_query_start": ("execution", "context", "RAG_QUERY"),
    "rag_query_end": ("execution", "context", "RAG_QUERY"),
    "context_assembled": ("context", "context", "CONTEXT_ASSEMBLE"),
    "client_response": ("execution", "orchestrator", "AGENT_END"),
    "context_part_contributed": ("context", "context", "SLICE_EMIT"),
    "state_update_start": ("context", "context", "CONTEXT_EVICT"),
    "state_update_end": ("context", "context", "CONTEXT_EVICT"),
    "routing": ("execution", "orchestrator", "ROUTE"),
    "routing_result": ("execution", "orchestrator", "ROUTE"),
    "parallel_group_start": ("trajectory", "orchestrator", "FORK"),
    "parallel_group_end": ("trajectory", "orchestrator", "JOIN"),
    "branch_start": ("trajectory", "orchestrator", "FORK"),
    "branch_end": ("trajectory", "orchestrator", "JOIN"),
    # A check's timing bracket is a ProcessingCall (a core class): execution layer.
    "governance_authorize_start": ("execution", "governance", "POLICY_CHECK"),
    "governance_authorize_end": ("execution", "governance", "POLICY_CHECK"),
    "governance_validate_start": ("execution", "governance", "POLICY_CHECK"),
    "governance_validate_end": ("execution", "governance", "POLICY_CHECK"),
    "hitl_gate": ("governance", "governance", "HITL"),
    "checkpoint_start": ("execution", "governance", "CHECKPOINT"),
    "checkpoint_end": ("execution", "governance", "CHECKPOINT"),
    "skill_execution_start": ("execution", "tool", "SKILL_EXEC"),
    "skill_execution_end": ("execution", "tool", "SKILL_EXEC"),
    "processing_call_start": ("execution", "context", "PROCESSING"),
    "processing_call_end": ("execution", "context", "PROCESSING"),
    "network_call_start": ("execution", "tool", "NETWORK_CALL"),
    "network_call_end": ("execution", "tool", "NETWORK_CALL"),
    "workflow_transition_start": ("execution", "orchestrator", "WORKFLOW"),
    "workflow_transition_end": ("execution", "orchestrator", "WORKFLOW"),
    "agent_communication_start": ("execution", "orchestrator", "DELEGATE"),
    "agent_communication_end": ("execution", "orchestrator", "DELEGATE"),
    "user_input": ("execution", "orchestrator", "USER_INPUT"),
    "user_output": ("execution", "orchestrator", "USER_OUTPUT"),
    "governance_checked": ("governance", "governance", "POLICY_CHECK"),
    "governance_denied": ("governance", "governance", "POLICY_DENY"),
    "policy_allow": ("governance", "governance", "POLICY_CHECK"),
    "policy_denial": ("governance", "governance", "POLICY_DENY"),
    "budget_event": ("governance", "governance", "BUDGET"),
    "control_intervention": ("governance", "governance", "CONTROL"),
    "control": ("control", "control", "CONTROL"),
    "audit": ("governance", "governance", "AUDIT"),
    "infrastructure_info": ("structural", "orchestrator", "WORKER"),
    "system_specification": ("structural", "orchestrator", "SPEC_EMIT"),
}

_BLOCK_TO_EXPORT_LAYER: dict[str, str] = {
    "structural": "structure",
    "execution": "execution",
    "context": "semantic",
    "trajectory": "provenance",
    "governance": "governance",
    "control": "control",
}

# Map Mealy contract_id → ontology summand (Appendix R).
CONTRACT_SUMMAND: dict[str, str] = {
    "model": "model",
    "tool": "tool",
    "context": "context",
    "governance": "governance",
    "orchestrator": "orchestrator",
    "observability": "orchestrator",
    "control": "control",
}


def stamp_envelope_fields(
    record: dict[str, Any],
    *,
    mas_id: str = "",
    session_id: str = "",
    task_id: str = "",
    transition_mealy_symbol: str = "",
    transition_summand: str = "",
) -> dict[str, Any]:
    """Add Appendix R universal envelope fields without mutating caller dict."""
    out = dict(record)
    kind = str(out.get("kind", ""))
    block, summand, mealy = _KIND_ENVELOPE.get(kind, ("execution", "orchestrator", kind.upper()))

    if transition_summand:
        summand = transition_summand
    elif transition_mealy_symbol:
        mealy = transition_mealy_symbol

    out.setdefault("block", block)
    out.setdefault("summand", summand)
    out.setdefault("mealy_symbol", mealy)
    export_layer = _BLOCK_TO_EXPORT_LAYER.get(block, block)
    if export_layer:
        out.setdefault("layer", export_layer)

    run_id = str(out.get("run_id") or "")
    if mas_id:
        out.setdefault("mas_id", mas_id)
        out.setdefault("app_name", mas_id)
    if session_id:
        out.setdefault("session_id", session_id)
    elif run_id:
        out.setdefault("session_id", f"session-{run_id}")
    if task_id:
        out.setdefault("task_id", task_id)

    return out

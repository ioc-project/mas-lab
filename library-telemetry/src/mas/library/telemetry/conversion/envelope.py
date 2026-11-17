#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native event categorization — ``kind`` → (block, summand, mealy_symbol).

This is the single source of truth for how a native ``events.jsonl`` ``kind``
maps onto:

* **block**   — the ontology block (``structural`` / ``execution`` / ``context``
  / ``trajectory`` / ``governance``), per papers/mas-ontology-kg appendices R.2–R.3.
* **summand** — the Mealy summand (``orchestrator`` / ``model`` / ``tool`` /
  ``context`` / ``governance``).
* **mealy_symbol** — the Mealy transition symbol.

The **block** is what determines the *category* a conversion handler lives in
(``conversion/mappings/<category>.py``) and the OTel **export layer** an event
belongs to (see :mod:`mas.library.telemetry.conversion.layers`).

Categorisation of the giant flat handler table into meaningful modules is driven
directly by this mapping — mirroring how ``library-kg`` groups its OTel→KG
mappings by wire format.  Adding a new event kind means (1) adding a row here and
(2) registering a handler in the matching category module.

.. note::
   Mirrors the ``_KIND_ENVELOPE`` table in
   ``mas.library.standard.lib.observability.native.envelope``.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# kind → (block, summand, mealy_symbol)
KIND_ENVELOPE: Dict[str, Tuple[str, str, str]] = {
    "mas_call_start": ("structural", "orchestrator", "RUN_START"),
    "mas_call_end": ("structural", "orchestrator", "RUN_END"),
    "execution_start": ("execution", "orchestrator", "AGENT_START"),
    "execution_end": ("execution", "orchestrator", "AGENT_END"),
    "user_response": ("execution", "orchestrator", "AGENT_END"),
    "llm_call_start": ("execution", "model", "LLM_CALL"),
    "llm_call_end": ("execution", "model", "LLM_CALL"),
    "tool_call_start": ("execution", "tool", "TOOL_CALL"),
    "tool_call_end": ("execution", "tool", "TOOL_CALL"),
    "memory_call_start": ("execution", "tool", "MEMORY_CALL"),
    "memory_call_end": ("execution", "tool", "MEMORY_CALL"),
    "memory_store_start": ("execution", "tool", "MEMORY_STORE"),
    "memory_store_end": ("execution", "tool", "MEMORY_STORE"),
    "memory_read_start": ("execution", "tool", "MEMORY_RETRIEVE"),
    "memory_read_end": ("execution", "tool", "MEMORY_RETRIEVE"),
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
    "parallel_group_merge": ("trajectory", "orchestrator", "JOIN"),
    "branch_start": ("trajectory", "orchestrator", "FORK"),
    "branch_end": ("trajectory", "orchestrator", "JOIN"),
    "governance_authorize_start": ("execution", "governance", "POLICY_CHECK"),
    "governance_authorize_end": ("execution", "governance", "POLICY_CHECK"),
    "governance_validate_start": ("execution", "governance", "POLICY_CHECK"),
    "governance_validate_end": ("execution", "governance", "POLICY_CHECK"),
    "hitl_gate": ("governance", "governance", "HITL"),
    "hitl_request": ("governance", "governance", "HITL"),
    "hitl_resolve": ("governance", "governance", "HITL"),
    "checkpoint_start": ("execution", "governance", "CHECKPOINT"),
    "checkpoint_end": ("execution", "governance", "CHECKPOINT"),
    "thinking_start": ("execution", "model", "THINKING"),
    "thinking_end": ("execution", "model", "THINKING"),
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
    "governance_event": ("governance", "governance", "POLICY_CHECK"),
    "governance_checked": ("governance", "governance", "POLICY_CHECK"),
    "governance_denied": ("governance", "governance", "POLICY_DENY"),
    "governance_policy": ("governance", "governance", "POLICY_CHECK"),
    "transformation_event": ("governance", "governance", "POLICY_CHECK"),
    "policy_allow": ("governance", "governance", "POLICY_CHECK"),
    "policy_denial": ("governance", "governance", "POLICY_DENY"),
    "budget_event": ("governance", "governance", "BUDGET"),
    "control_intervention": ("governance", "governance", "CONTROL"),
    "compaction": ("context", "context", "COMPACTION"),
    "audit": ("governance", "governance", "AUDIT"),
    "infrastructure_info": ("structural", "orchestrator", "WORKER"),
    "system_specification": ("structural", "orchestrator", "SPEC_EMIT"),
    "governance_decision": ("governance", "governance", "POLICY_CHECK"),
    "obs_wrap_gov_authorize_start": ("execution", "governance", "POLICY_CHECK"),
    "obs_wrap_gov_authorize_end": ("execution", "governance", "POLICY_CHECK"),
    "obs_wrap_gov_validate_start": ("execution", "governance", "POLICY_CHECK"),
    "obs_wrap_gov_validate_end": ("execution", "governance", "POLICY_CHECK"),
    "observability_pre_execute_start": ("execution", "orchestrator", "OBS_PRE"),
    "observability_pre_execute_end": ("execution", "orchestrator", "OBS_PRE"),
    "observability_post_execute_start": ("execution", "orchestrator", "OBS_POST"),
    "observability_post_execute_end": ("execution", "orchestrator", "OBS_POST"),
    "boundary_ingress": ("execution", "orchestrator", "BOUNDARY"),
    "boundary_egress": ("execution", "orchestrator", "BOUNDARY_EGRESS"),
    "context_steer": ("context", "context", "STEER"),
    "contract_call_start": ("execution", "tool", "CONTRACT"),
    "contract_call_end": ("execution", "tool", "CONTRACT"),
    "wait_state_start": ("trajectory", "orchestrator", "WAIT"),
    "wait_state_end": ("trajectory", "orchestrator", "WAIT"),
    "tool_result_injected": ("execution", "context", "TOOL_RESULT"),
}

# Ontology block → OTel export layer name.
BLOCK_TO_EXPORT_LAYER: Dict[str, str] = {
    "structural": "structure",
    "execution": "execution",
    "context": "semantic",
    "trajectory": "provenance",
    "governance": "governance",
}


def envelope_for_kind(kind: str) -> Tuple[str, str, str]:
    """Return ``(block, summand, mealy_symbol)`` for *kind*, with a safe default."""
    return KIND_ENVELOPE.get(kind, ("execution", "orchestrator", kind.upper()))


def block_for_kind(kind: str) -> Optional[str]:
    """Return the ontology block for *kind* (``None`` when unmapped)."""
    entry = KIND_ENVELOPE.get(kind)
    return entry[0] if entry else None


def export_layer_for_kind(kind: str) -> Optional[str]:
    """Map a native event kind to its OTel export layer name (or ``None``)."""
    block = block_for_kind(kind)
    if block is None:
        return None
    return BLOCK_TO_EXPORT_LAYER.get(block, block)


__all__ = [
    "KIND_ENVELOPE",
    "BLOCK_TO_EXPORT_LAYER",
    "envelope_for_kind",
    "block_for_kind",
    "export_layer_for_kind",
]

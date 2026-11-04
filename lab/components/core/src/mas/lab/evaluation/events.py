#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observability event schema helpers for mas-lab evaluation.

The agent runtime emits raw JSONL event dicts.  Eval plugins consume those
dicts directly via :class:`EvalContract.on_event` — there is no normalization
layer in the open-source tree.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


class Kind:
    """Single source of truth for event kind string literals."""

    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    LLM_CALL_START = "llm_call_start"
    LLM_CALL_END = "llm_call_end"
    EXEC_START = "execution_start"
    EXEC_END = "execution_end"
    MAS_CALL_START = "mas_call_start"
    MAS_CALL_END = "mas_call_end"
    RAG_QUERY_START = "rag_query_start"
    RAG_QUERY_END = "rag_query_end"
    MEMORY_CALL_START = "memory_call_start"
    MEMORY_CALL_END = "memory_call_end"
    PROCESSING_CALL_START = "processing_call_start"
    PROCESSING_CALL_END = "processing_call_end"
    WORKFLOW_TRANSITION_START = "workflow_transition_start"
    WORKFLOW_TRANSITION_END = "workflow_transition_end"
    CONTEXT_ASSEMBLED = "context_assembled"
    GOVERNANCE_EVENT = "governance_event"
    GOVERNANCE_CHECKED = "governance_checked"
    GOVERNANCE_DENIED = "governance_denied"
    POLICY_DENIAL = "policy_denial"
    POLICY_ALLOW = "policy_allow"
    HITL_GATE = "hitl_gate"
    HITL_RESPONSE = "hitl_response"
    HITL_TIMEOUT = "hitl_timeout"
    BUDGET_EVENT = "budget_event"
    CONTROL_INTERVENTION = "control_intervention"
    TRANSFORMATION_EVENT = "transformation_event"
    CIRCUIT_OPEN = "circuit_opened"
    CIRCUIT_HALF_OPEN = "circuit_half_open"
    CIRCUIT_CLOSED = "circuit_closed"
    CIRCUIT_SKIP = "circuit_skip"
    CIRCUIT_PROBE_FAILED = "circuit_probe_failed"
    TOOL_BLACKLISTED = "tool_blacklisted"
    PARALLEL_GROUP = "parallel_group"
    AUDIT = "audit"
    UNKNOWN = "unknown"


def obs_event(
    kind: str,
    agent_id: str,
    timestamp: Optional[float] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Construct a canonical event dict for eval plugins."""
    e: Dict[str, Any] = {
        "kind": kind,
        "agent_id": agent_id,
        "timestamp": timestamp if timestamp is not None else time.time(),
    }
    e.update(extra)
    return e

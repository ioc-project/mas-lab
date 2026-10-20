#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kind mappings, type colours, and shared trajectory constants."""
from mas.lab.plots.palette import PALETTE

_KIND_BASE_TO_TYPE: dict[str, str] = {
    "mas_call":          "MASCall",
    "execution":         "AgentCall",
    "llm_call":          "LLMCall",
    "tool_call":         "ToolCall",
    "memory_call":       "MemoryCall",
    "memory_store":      "MemoryCall",
    "memory_retrieve":   "MemoryCall",
    "rag_query":         "RAGQuery",
    "processing_call":   "ProcessingCall",
    "skill_execution":   "ProcessingCall",
    "network_call":      "ToolCall",
    # Shorter aliases emitted by some runtime versions
    "processing":        "ProcessingCall",
    "skill_call":        "ProcessingCall",
    "skill":             "ProcessingCall",
    # The following are cross-cutting/instrumentation events, not part of the
    # execution trajectory. Excluding them from call records keeps the Calls
    # lane to the real flow (agent → llm → tool …) and removes the empty
    # connector gaps + wide chart they used to create by adding timeline
    # buckets no lane had content at:
    #   state_update            → WM/turn-history mutations (ContextState)
    #   governance_authorize/validate → policy checks (still available as
    #                             annotations; HITL is out of the tutorials)
    #   obs_wrap_gov_*          → observability wrappers around governance
    # Context provenance still renders from context_assembled /
    # context_part_contributed annotations.
}

_CALL_TYPE_TO_LEVEL: dict[str, str] = {
    "MASCall":          "mas",
    "AgentCall":        "agent",
    "LLMCall":          "call",
    "ToolCall":         "call",
    "MemoryCall":       "call",
    "RAGQuery":         "call",
    "ProcessingCall":   "call",
    "MITMCall":         "call",
    "ThinkingCall":     "thinking",
    "ThinkingEmit":     "thinking",
    "ContextState":     "call",
}

# Canonical visual properties of each call type — owned by the model so
# renderers never need to switch on call type names.
TYPE_COLOR: dict[str, str] = {
    "Session":        PALETTE["session"],
    "MASCall":        PALETTE["mas"],
    "HITL":           "#0284c7",
    "AgentCall":      PALETTE["agent"],
    "LLMCall":        PALETTE["llm"],
    "ToolCall":       PALETTE["tool"],
    "MemoryCall":     PALETTE["memory"],
    "RAGQuery":       PALETTE["rag"],
    "ProcessingCall": PALETTE["processing"],
    "MITMCall":       "#dc2626",   # red-600 — fault injection
    "ThinkingCall":   "#7c3aed",   # violet
    "ThinkingEmit":   "#4c1d95",   # indigo-900 — LLM output emission phase
    "ContextState":   "#0d9488",   # teal-600 — WM / turn-history mutations
    # Synthetic Agents-lane connector bridging a fork's 2nd..Nth branch back
    # to its own bracketing StateNode (see dag.py's _bridge_to_state) — no
    # real call backs it, so it's coloured to match the reset separator line
    # it always coincides with rather than any real call-type colour.
    "BranchLink":     "#f59e0b",   # amber-500 — matches the reset separator
    # Governance lane (Phase 3): coloured by the worst decision it carries.
    "GovernanceBlock":   "#ef4444",  # red-500 — BLOCK/TERMINATE/BLACKLIST
    "GovernanceCaution": "#f59e0b",  # amber-500 — HITL/RETRY/SKIP/MODIFY
    "GovernanceAllow":   "#64748b",  # slate-500 — ALLOW/LOG
}

TYPE_LABEL: dict[str, str] = {
    "Session":        "Session",
    "MASCall":        "MAS",
    "HITL":           "HITL",
    "AgentCall":      "Agent",
    "LLMCall":        "LLM",
    "ToolCall":       "Tool",
    "MemoryCall":     "Mem",
    "RAGQuery":       "RAG",
    "ProcessingCall": "Proc",
    "MITMCall":       "MITM",
    "ThinkingCall":   "Think",
    "ThinkingEmit":   "Emit",
    "ContextState":   "State",
    "BranchLink":     "Branch",
    "GovernanceBlock":   "Gov",
    "GovernanceCaution": "Gov",
    "GovernanceAllow":   "Gov",
}

# Tolerance used exclusively for tree-traversal containment comparisons
# (NOT for bucketing or merging timestamps).
_TS_TOL: float = 0.05

# Per-item time offset used to stagger coincident point-in-time
# ProcessingCall records so each occupies its own visual slot in the Calls
# lane (see annotations.py's _stagger_coinc_processing_calls). Shared with
# dag.py so it can precisely re-derive how far staggering may have pushed
# the session's true final timestamp past the pre-stagger t_max.
_STAGGER_DUR: float = 0.001

# Processing type constants — standardized names for execution flow patterns
PROCESSING_TYPE_WAIT_STATE = "wait_state"
PROCESSING_TYPE_CONTEXT_ASSEMBLY = "context_assembly"
PROCESSING_TYPE_PARALLEL_GROUP = "parallel_group"
PROCESSING_TYPE_PARALLEL_FORK = "parallel_fork"

# Processing name constants — specific names for processing operations
PROCESSING_NAME_PARALLEL_FORK = "parallel fork"
PROCESSING_NAME_CONTEXT_ASSEMBLY = "context assembly"

# Wait scope constants — categorizes types of waiting/delegation
WAIT_SCOPE_DELEGATION = "delegation"

# Icons for instant (≈0-duration) call types — rendered on top of state boxes.
_INSTANT_ICON: dict[str, str] = {
    "ProcessingCall": "⚙",
    "MITMCall":       "⚠",
    "ToolCall":       "⚡",
    "ContextState":   "📝",
    "BranchLink":     "↳",
    "GovernanceBlock":   "⛔",
    "GovernanceCaution": "⚑",
}
_INSTANT_ICON_DEFAULT = "⊕"

# Short display labels for each processing_type (§3.20.3).
_PROC_TYPE_LABEL: dict[str, str] = {
    "prompt_engineering":     "⚙ prompt",
    "system_prompt":          "⚙ sys",
    "skill_catalog":          "⚙ skills",
    "memory":                 "⚙ mem↓",
    "memory_injection":       "⚙ mem↓",
    "knowledge_base":         "⚙ kb↓",
    "ontology_injection":     "⚙ onto↓",
    "agents":                 "⚙ agents",
    "tools":                  "⚙ tools",
    "context_injection":      "⚙ ctx↓",
    "context_compression":    "⚙ compr",
    "context_paging":         "⚙ page",
    "context_delegation_out": "⚙ ctx→",
    "context_delegation_in":  "⚙ ctx←",
    "content_propagation":    "⚙ prop",
    "mitm_rewrite":           "⚠ MITM",
}

# Laid out after the LLM that requested them, not as nested spans of it.
_LLM_FOLLOW_UP_TYPES = frozenset({"ToolCall", "MemoryCall", "RAGQuery"})

# Longest unspanned adjacent-boundary gap still drawn as a connector column.
_EMPTY_GAP_MAX_S = 0.005

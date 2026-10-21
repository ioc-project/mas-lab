#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Memory Contract - Memory read/write interface.

Paper Reference: Section 4.3 - "State Contract (Shared Context)"

The Memory Contract provides:
1. Read/write semantics with provenance
2. Conflict resolution for concurrent access
3. Memory type abstraction (episodic, semantic, procedural)
4. Versioning and consistency guarantees

This enables:
- Stateful governance policies ("reject if no user consent recorded")
- Memory portability across frameworks
- Provenance tracking (who wrote what, when)
- Shared context in multi-agent systems

Hook Usage:
- pre_memory_store: Intercept BEFORE writing memory
- post_memory_store: Audit AFTER memory written

Implementations live in library packages (``MemoryProviderPlugin``,
``SemanticMemoryPlugin``).

Memory Types:
- episodic: Conversation history, events, traces
- semantic: Facts, knowledge, embeddings
- procedural: Skills, workflows, patterns

Architecture Note:
    Memory is part of the State Contract in the paper. We separate it
    into a distinct interface for implementation clarity while maintaining
    the same hook-based governance guarantees.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from mas.runtime.contracts.base import CapabilityContract  # L3->L3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Working-memory retention model (M6)
# ---------------------------------------------------------------------------


@dataclass
class RetentionPolicy:
    """Declarative retention policy for a working-memory item.

    Governs *why* an item stays in or is dropped from assembled context.
    """

    max_age_s: Optional[float] = None
    min_score: Optional[float] = None
    prefer_type: Optional[str] = None  # "semantic" | "episodic" | "procedural"
    pinned: bool = False

    def evaluate(self, age_s: Optional[float], score: Optional[float]) -> str:
        """Return a verdict: 'retain', 'stale', 'low_score', or 'policy_pin'."""
        if self.pinned:
            return "policy_pin"
        if self.max_age_s is not None and age_s is not None and age_s > self.max_age_s:
            return "stale"
        if self.min_score is not None and score is not None and score < self.min_score:
            return "low_score"
        return "retain"


@dataclass
class WorkingMemoryAnnotation:
    """Rich annotation for a single working-memory item (M6).

    Provides structured policy verdicts, drop tracking, and cross-memory
    preference signals.
    """

    item_index: int = 0
    source_type: str = ""          # episodic | semantic | procedural
    score: Optional[float] = None
    age_s: Optional[float] = None
    verdict: str = "retain"        # retain | stale | low_score | policy_pin | dropped
    drop_reason: Optional[str] = None  # why dropped (if verdict == 'dropped')
    preferred_over: Optional[str] = None  # cross-memory preference signal
    trust_level: str = ""          # tentative | authoritative | unverified
                                   # OC metric C5 — Memory Authority Level
    policy: Optional[RetentionPolicy] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "item_index": self.item_index,
            "source_type": self.source_type,
            "verdict": self.verdict,
        }
        if self.score is not None:
            d["score"] = round(self.score, 4)
        if self.age_s is not None:
            d["age_s"] = round(self.age_s, 1)
        if self.drop_reason:
            d["drop_reason"] = self.drop_reason
        if self.preferred_over:
            d["preferred_over"] = self.preferred_over
        if self.trust_level:
            d["trust_level"] = self.trust_level
        return d


# ---------------------------------------------------------------------------
# Memory contract
# ---------------------------------------------------------------------------


class MemoryContract(CapabilityContract):

    contract_id = "memory"
    """Base interface for memory access using hooks.
    
    All memory operations MUST go through these hooks to ensure:
    - Provenance (who wrote what, when)
    - Consistency (conflict resolution)
    - Governance (access control, quotas)
    - Observability (track memory usage)
    
    Subclasses MUST implement read_memory() and write_memory().
    """
    
    def read_memory(self, memory_type: str, query: Dict[str, Any]) -> Dict[str, Any]:
        """Read from memory store.
        
        Args:
            memory_type: Type of memory ("episodic", "semantic", "procedural")
            query: Query parameters:
                {
                    "agent_id": str (optional),
                    "session_id": str (optional),
                    "limit": int (optional),
                    "filters": dict (optional),
                    "search": str (optional, for semantic search)
                }
        
        Returns:
            Memory query result:
            {
                "status": "ok" | "error",
                "items": [
                    {
                        "key": "memory_id",
                        "content": <data>,
                        "metadata": {
                            "created_at": <timestamp>,
                            "created_by": <agent_id>,
                            "version": <int>
                        }
                    },
                    ...
                ],
                "count": int,
                "has_more": bool
            }
        
        Raises:
            NotImplementedError: Must be implemented by subclass
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement read_memory()"
        )
    
    def write_memory(self, memory_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Write to memory store.
        
        This method is called WITHIN the hook pipeline:
        1. pre_memory_store hook fires (validation, quotas)
        2. write_memory() executes (actual write)
        3. post_memory_store hook fires (audit logging)
        
        Args:
            memory_type: Type of memory ("episodic", "semantic", "procedural")
            payload: Data to store:
                {
                    "key": str (memory identifier),
                    "content": Any (data to store),
                    "metadata": dict (optional, provenance)
                }
        
        Returns:
            Write confirmation:
            {
                "status": "ok" | "error",
                "key": str (memory identifier),
                "version": int (version number after write)
            }
        
        Raises:
            NotImplementedError: Must be implemented by subclass
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement write_memory()"
        )
    
    # Hook methods (optional overrides)
    
    def pre_memory_store(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called BEFORE storing memory.
        
        Override this to implement:
        - Memory quotas (size limits)
        - Access control (who can write where)
        - Conflict detection (versioning checks)
        - Content validation (schema checks)
        
        Args:
            context: {
                "memory_type": str,
                "payload": dict,
                "agent_id": str,
            }
        
        Returns:
            Modified context (or raise exception to block)
        """
        return context
    
    def post_memory_store(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called AFTER storing memory.
        
        Override this to implement:
        - Audit logging (record writes)
        - Cache invalidation
        - Replication triggers
        - Event notifications
        
        Args:
            context: {
                "memory_type": str,
                "payload": dict,
                "result": dict,
                "agent_id": str,
            }
        
        Returns:
            Modified context
        """
        return context


#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observability events — auditability, accountability, attribution (3As)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObsPhase(str, Enum):
    """Control-library envelope phases (collapsed seven-step view)."""

    REQUEST = "REQUEST"
    INGRESS = "INGRESS"
    AUTHZ = "AUTHZ"
    OBSERVABILITY_PRE = "OBSERVABILITY_PRE"
    EXECUTE = "EXECUTE"
    RESULT = "RESULT"
    EGRESS = "EGRESS"
    VALID = "VALID"
    END = "END"


class ObsEventKind(str, Enum):
    BOUNDARY_EGRESS = "boundary.egress"
    BOUNDARY_INGRESS = "boundary.ingress"
    GOVERNANCE_DECISION = "governance.decision"
    HITL_REQUEST = "hitl.request"
    HITL_RESOLVE = "hitl.resolve"
    ENGINE_IO = "engine.io"
    CLIENT_RESPONSE = "client.response"
    BOUNDARY_ERROR = "boundary.error"
    CONTEXT_STEER = "context.steer"
    CONTEXT_MUTATION = "context.mutation"
    CONTEXT_ASSEMBLED = "context.assembled"
    CACHE_LOOKUP = "cache.lookup"
    ENGINE_IO_RETURN = "engine.io.return"
    ENVELOPE_ACTIVITY = "envelope.activity"
    CONTROL = "control"
    LLM_THINKING = "llm.thinking"


class ObservabilityEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=0)
    kind: ObsEventKind
    phase: ObsPhase
    machine_id: str
    correlation_id: int = 0
    policy_name: str = ""
    actor_id: str = "kernel"
    attribution_code: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int
    correlation_ids: list[int]
    machines_touched: list[str]
    has_full_envelope: bool
    auditability: bool
    accountability: bool
    attribution: bool
    gaps: list[str] = Field(default_factory=list)

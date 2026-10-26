#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Σ_in — immutable ingress symbols entering the kernel."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Annotated, Literal, Union

from mas.runtime.schema.hitl import HitlResolveChoice
from pydantic import BaseModel, ConfigDict, Field


class IngressKind(str, Enum):
    USER_INPUT_RECEIVED = "USER_INPUT_RECEIVED"
    LIFECYCLE_PAUSE = "LIFECYCLE_PAUSE"
    LIFECYCLE_RESUME = "LIFECYCLE_RESUME"
    LIFECYCLE_ABORT = "LIFECYCLE_ABORT"
    ENGINE_IO_RETURN = "ENGINE_IO_RETURN"
    CTX_ASSEMBLY_COMPLETE = "CTX_ASSEMBLY_COMPLETE"
    HITL_APPROVE = "HITL_APPROVE"
    HITL_RESOLVE = "HITL_RESOLVE"
    OPERATOR_STEER_RECEIVED = "OPERATOR_STEER_RECEIVED"


class UserInputReceived(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.USER_INPUT_RECEIVED] = IngressKind.USER_INPUT_RECEIVED
    user_turn_id: str
    text: str
    # One fresh id per input prompt — distinct from user_turn_id (a short,
    # session-sequential label like "u1", reused for delegation matching).
    # Governance/observability read this (via Transition.task_id, propagated
    # by the driver to every transition produced while processing this turn)
    # to correlate a decision back to the exact prompt that triggered it,
    # across sessions where user_turn_id alone isn't unique. task_id is
    # always local to one agent — never propagated across delegation, unlike
    # session_id below.
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # One id for the whole MAS run, shared by every agent in it. The default
    # here only ever applies to the very first input of a run (no one has a
    # session yet, so mint one) — every subsequent UserInputReceived, whether
    # a follow-up turn or a delegated call to another agent, is constructed
    # with the SAME session_id explicitly passed through (see
    # mas.ctl.session.controller.SessionController.session_id, threaded into
    # every agent-to-agent delegation call by
    # mas.ctl.executor.mas_session.make_workflow_send), so it never re-mints.
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Observability context supplied by an upstream caller. The kernel keeps
    # allocating its engine correlation identifiers locally.
    upstream_correlation_id: int | None = Field(default=None, ge=0)


class LifecyclePause(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.LIFECYCLE_PAUSE] = IngressKind.LIFECYCLE_PAUSE
    reason: str = ""


class LifecycleResume(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.LIFECYCLE_RESUME] = IngressKind.LIFECYCLE_RESUME


class LifecycleAbort(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.LIFECYCLE_ABORT] = IngressKind.LIFECYCLE_ABORT
    reason: str = ""


class ToolCallSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_name: str
    tool_arguments: dict = Field(default_factory=dict)


class EngineIoReturn(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.ENGINE_IO_RETURN] = IngressKind.ENGINE_IO_RETURN
    correlation_id: int = Field(ge=1)
    response_kind: Literal["MODEL_TEXT", "TOOL_RESULT", "TRANSPORT_ACK", "ERROR"] = "MODEL_TEXT"
    next_step: Literal["STOP", "TOOL_CALL", "PARALLEL_TOOL_CALLS", "LLM_CALL", "DELEGATE"] = "STOP"
    tool_name: str = ""
    tool_arguments: dict = Field(default_factory=dict)
    parallel_tools: tuple[ToolCallSpec, ...] = ()
    text: str = ""
    usage: dict = Field(default_factory=dict)
    finish_reason: str = ""
    # Names sent as the OpenAI ``tools`` array for this LLM call.
    # None means the engine did not record them (budget stop, non-LLM return).
    offered_tools: list[str] | None = None
    # Resolved LiteLLM id actually dispatched. Empty for non-LLM returns.
    model: str = ""
    failure_class: str = ""
    failure_code: str = ""
    retry_attempts: int = 0
    # Output-token budget sent on the final attempt; None when no limit was sent.
    max_tokens: int | None = None
    truncation_retries: int = 0
    # Response-cache provenance is separate from provider-reported token usage.
    cache_status: Literal["hit", "miss"] | None = None
    cache_layer: str = ""
    cache_events: list[dict[str, str]] = Field(default_factory=list)
    pricing: dict[str, float] = Field(default_factory=dict)


class CtxAssemblyComplete(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.CTX_ASSEMBLY_COMPLETE] = IngressKind.CTX_ASSEMBLY_COMPLETE
    collect_id: str = "default"


class HitlApprove(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.HITL_APPROVE] = IngressKind.HITL_APPROVE


class HitlResolve(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.HITL_RESOLVE] = IngressKind.HITL_RESOLVE
    request_id: int = Field(ge=1)
    resolution: HitlResolveChoice
    answer: object | None = None
    operator_context: dict = Field(default_factory=dict)

    @property
    def operator_id(self) -> str:
        return str(self.operator_context.get("operator_id") or "operator")

    @property
    def steering_text(self) -> str:
        raw = self.operator_context.get("steering") or self.operator_context.get("message") or ""
        return raw if isinstance(raw, str) else str(raw)


class OperatorSteerReceived(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal[IngressKind.OPERATOR_STEER_RECEIVED] = IngressKind.OPERATOR_STEER_RECEIVED
    steer_id: str = "steer-1"
    context_text: str = ""


IngressSymbol = Annotated[
    Union[
        UserInputReceived,
        LifecyclePause,
        LifecycleResume,
        LifecycleAbort,
        EngineIoReturn,
        CtxAssemblyComplete,
        HitlApprove,
        HitlResolve,
        OperatorSteerReceived,
    ],
    Field(discriminator="kind"),
]

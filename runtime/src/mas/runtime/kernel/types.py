#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared kernel type symbols — no imports from machines (breaks import cycles)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class LifecycleState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class DpState(str, Enum):
    IDLE = "IDLE"
    CTX_BUILD = "CTX_BUILD"
    EGRESS_PENDING = "EGRESS_PENDING"
    AWAITING_INGRESS = "AWAITING_INGRESS"
    EVALUATING = "EVALUATING"


class ModelState(str, Enum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    CALLING = "CALLING"
    DONE = "DONE"
    ERROR = "ERROR"


class ToolState(str, Enum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    WAIT_GOV = "WAIT_GOV"
    EXECUTING = "EXECUTING"
    DONE = "DONE"
    ERROR = "ERROR"


class CtxState(str, Enum):
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    COMMITTED = "COMMITTED"


class MemoryState(str, Enum):
    IDLE = "IDLE"
    QUERYING = "QUERYING"
    WRITING = "WRITING"
    DONE = "DONE"


class SessionState(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    CHECKPOINTING = "CHECKPOINTING"


class TransportState(str, Enum):
    IDLE = "IDLE"
    SENDING = "SENDING"
    AWAITING_ACK = "AWAITING_ACK"


class GovState(str, Enum):
    IDLE = "IDLE"
    AUTHZ_EGRESS = "AUTHZ_EGRESS"
    AUTHZ_INGRESS = "AUTHZ_INGRESS"
    HITL_PENDING = "HITL_PENDING"
    VALIDATING = "VALIDATING"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


InflightKind = Literal["NONE", "MODEL", "TOOL", "HITL"]
ScheduledEgress = Literal["NONE", "LLM_CALL", "TOOL_CALL", "MEMORY_OP", "TRANSPORT_MSG"]


@dataclass(frozen=True)
class OutboundWait:
    """One external call the runtime has dispatched but not yet resolved."""

    correlation_id: int
    kind: InflightKind
    op: ScheduledEgress | str = "NONE"


@dataclass
class HitlHold:
    """One human answer the kernel is waiting for, kept in ``QProduct.hitl_holds`` under its request id.

    An egress hold keeps the call it stopped (``tool_name``, ``tool_arguments``). An ingress hold parks the
    engine return it stopped (``ingress_return``, a dumped ``EngineIoReturn``). ``deferred`` is an answer
    that arrived while the lifecycle was paused: it is applied on resume, once.
    """

    request_id: int
    pending_schedule: ScheduledEgress = "NONE"
    question_type: str = "CONFIRM"
    tool_name: str = ""
    tool_arguments: dict = field(default_factory=dict)
    ingress_return: dict = field(default_factory=dict)
    # Which declared tool call of its model reply the held call is (``call_<model id>_<index>``); "" for a single call.
    call_id: str = ""
    deferred: dict = field(default_factory=dict)

    @property
    def is_ingress(self) -> bool:
        return bool(self.ingress_return)

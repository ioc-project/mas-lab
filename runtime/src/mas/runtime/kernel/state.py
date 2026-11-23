#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kernel product state — Q (control tuple) and run ledger (shared turn data)."""

from __future__ import annotations

import copy
from dataclasses import MISSING, dataclass, field, fields

from mas.runtime.kernel.types import (
    CtxState,
    DpState,
    GovState,
    HitlHold,
    InflightKind,
    LifecycleState,
    MemoryState,
    ModelState,
    OutboundWait,
    ScheduledEgress,
    SessionState,
    ToolState,
    TransportState,
)

# Formal docs use τ for the shared tape; in code we call it the run ledger.

__all__ = [
    "CtxState",
    "DpState",
    "GovState",
    "HitlHold",
    "InflightKind",
    "LifecycleState",
    "MemoryState",
    "ModelState",
    "OutboundWait",
    "QProduct",
    "RunEvent",
    "RunLedger",
    "ScheduledEgress",
    "SessionState",
    "ToolState",
    "TransportState",
]


@dataclass
class RunEvent:
    """One accepted engine fact on the run log (correlation id + payload).

    The chat transcript is a copy of these rows, not a second store.
    Tool-call name and arguments live here so the copier does not read
    the engine return before governance has accepted it.
    """

    correlation_id: int
    response_kind: str
    next_step: str
    text: str = ""
    tool_name: str = ""
    tool_arguments: dict = field(default_factory=dict)
    parallel_tools: tuple[tuple[str, dict], ...] = ()
    finish_reason: str = ""
    # Opaque protocol-layer state of the completion (EngineIoReturn.provider_state).
    provider_state: dict = field(default_factory=dict)
    seq: int = 0
    # True for a row the kernel wrote for a call that did not run (skipped, blocked,
    # blacklisted); its text is a note for the model, not a reply.
    placeholder: bool = False
    # The declared tool call of the reply this row answers (``call_<reply id>_<index>`` for a batch, "" otherwise).
    call_id: str = ""


@dataclass
class RunLedger:
    """Ordered accepted facts for the current run."""

    events: list[RunEvent] = field(default_factory=list)
    _allocated_high: int = 0
    _next_seq: int = 0

    @classmethod
    def restore(cls, events: list[RunEvent], *, allocated_high: int, next_seq: int) -> RunLedger:
        """A ledger as a checkpoint held it, ids and sequence numbers included.

        A checkpoint older than the allocator carries neither counter: they are rebuilt from the rows, and a
        row without a ``seq`` is numbered in order.
        """
        ledger = cls(events=list(events))
        for position, row in enumerate(ledger.events, start=1):
            if row.seq <= 0:
                row.seq = position
        ledger._next_seq = max(next_seq, max((row.seq for row in ledger.events), default=0))
        ledger._allocated_high = max(allocated_high, max((row.correlation_id for row in ledger.events), default=0))
        return ledger

    def mark(self) -> tuple[int, int]:
        """The ledger's position (sequence number, id allocator): rows are only ever added after it."""
        return self._next_seq, self._allocated_high

    def rewind(self, mark: tuple[int, int]) -> None:
        """Drop every row added since ``mark`` and give the counters back."""
        next_seq, allocated_high = mark
        self.events[:] = [row for row in self.events if row.seq <= next_seq]
        self._next_seq, self._allocated_high = next_seq, allocated_high

    @property
    def allocated_high(self) -> int:
        """Highest correlation id handed out so far (0 if none)."""
        return self._allocated_high

    @property
    def records(self) -> list[RunEvent]:
        """Alias for tests and trace replay."""
        return self.events

    def next_correlation_id(self) -> int:
        if self._allocated_high > 0:
            return self._allocated_high + 1
        return (self.events[-1].correlation_id + 1) if self.events else 1

    def allocate_correlation_id(self) -> int:
        cid = self.next_correlation_id()
        self._allocated_high = cid
        return cid

    def _sync_allocated(self, correlation_id: int) -> None:
        self._allocated_high = max(self._allocated_high, correlation_id)

    def _stamp(self, record: RunEvent) -> None:
        self._next_seq += 1
        record.seq = self._next_seq

    def max_seq(self) -> int:
        """Highest seq stamped so far (0 if the log is empty)."""
        return self._next_seq

    def since(self, seen_seq: int) -> list[RunEvent]:
        """Rows stamped after ``seen_seq``. Inflight sort does not hide them."""
        return [row for row in self.events if row.seq > seen_seq]

    def latest(self) -> RunEvent | None:
        """Newest row by ``seq``, not list tail (inflight sort moves the tail)."""
        if not self.events:
            return None
        return max(self.events, key=lambda row: row.seq)

    def append(self, record: RunEvent) -> None:
        if self.events and record.correlation_id < self.events[-1].correlation_id:
            raise ValueError("run ledger correlation ids must be monotonic")
        self._stamp(record)
        self.events.append(record)
        self._sync_allocated(record.correlation_id)

    def append_inflight(self, record: RunEvent) -> None:
        if any(r.correlation_id == record.correlation_id for r in self.events):
            raise ValueError("duplicate run ledger correlation id")
        self._stamp(record)
        self.events.append(record)
        self.events.sort(key=lambda row: row.correlation_id)
        self._sync_allocated(record.correlation_id)

    def append_placeholder(
        self,
        *,
        text: str = "",
        next_step: str = "LLM_CALL",
        cid: int | None = None,
        tool_name: str = "",
        tool_arguments: dict | None = None,
        call_id: str = "",
        response_kind: str = "TOOL_RESULT",
    ) -> RunEvent:
        """Tool result for a call that did not run (skip, block, blacklist)."""
        if cid is None:
            cid = self.next_correlation_id()
        record = RunEvent(
            correlation_id=cid,
            response_kind=response_kind,
            next_step=next_step,
            text=text,
            call_id=call_id,
            tool_name=tool_name,
            tool_arguments=dict(tool_arguments or {}),
            placeholder=True,
        )
        if self.events and cid < self.events[-1].correlation_id:
            self.append_inflight(record)  # the answer to an earlier call of a batch goes in by id
        else:
            self.append(record)
        return record


# Where a field of the product state lives, declared per field (``metadata["scope"]``):
#   turn    - cleared by ``reset_turn_scope`` when a user input starts a turn, for every design pattern;
#   session - kept across turns;
#   flow    - machine and in-flight progress, moved only by transitions (idle between turns);
#   pattern - owned by the design pattern plugin, which sets it itself when a turn starts.
# A new field must name one: tests/test_state_scope.py fails otherwise.
_TURN = {"scope": "turn"}
_SESSION = {"scope": "session"}
_FLOW = {"scope": "flow"}
_PATTERN = {"scope": "pattern"}


@dataclass
class QProduct:
    ctrl: LifecycleState = field(default=LifecycleState.RUNNING, metadata=_SESSION)
    dp: DpState = field(default=DpState.IDLE, metadata=_FLOW)
    model: ModelState = field(default=ModelState.IDLE, metadata=_FLOW)
    tool: ToolState = field(default=ToolState.IDLE, metadata=_FLOW)
    ctx: CtxState = field(default=CtxState.IDLE, metadata=_FLOW)
    memory: MemoryState = field(default=MemoryState.IDLE, metadata=_FLOW)
    session: SessionState = field(default=SessionState.IDLE, metadata=_FLOW)
    transport: TransportState = field(default=TransportState.IDLE, metadata=_FLOW)
    scheduled_egress: ScheduledEgress = field(default="NONE", metadata=_FLOW)
    inflight_kind: InflightKind = field(default="NONE", metadata=_FLOW)
    # Human answers pending, by request id: concurrent holds coexist and each resolves on its own.
    hitl_holds: dict[int, HitlHold] = field(default_factory=dict, metadata=_FLOW)
    hitl_gov_override: bool = field(default=False, metadata=_FLOW)
    # The id a call is judged under when it is judged again (after a human answer, or a retry): 0 = a new call.
    call_cid: int = field(default=0, metadata=_FLOW)
    cot_pass: int = field(default=0, metadata=_TURN)
    gov_retry_count: int = field(default=0, metadata=_TURN)
    # Tools skipped for the rest of the session by a BLACKLIST verdict, by name: other tools are unaffected.
    tool_blacklist: list[str] = field(default_factory=list, metadata=_SESSION)
    gov_state: str = field(default="IDLE", metadata=_FLOW)
    obs_state: str = field(default="IDLE", metadata=_FLOW)
    hitl_skip_committed: bool = field(default=False, metadata=_TURN)
    hitl_block_committed: bool = field(default=False, metadata=_TURN)
    hitl_tools_approved_turn: bool = field(default=False, metadata=_TURN)
    hitl_results_approved_turn: bool = field(default=False, metadata=_TURN)
    control_phase: str = field(default="IDLE", metadata=_FLOW)
    pending_engine_correlation_id: int = field(default=0, metadata=_FLOW)
    outbound_waits: list[OutboundWait] = field(default_factory=list, metadata=_FLOW)
    inflight_correlation_ids: list[int] = field(default_factory=list, metadata=_FLOW)
    pending_tool_name: str = field(default="", metadata=_FLOW)
    pending_tool_args: dict = field(default_factory=dict, metadata=_FLOW)
    pending_tools_by_cid: dict[int, tuple[str, dict]] = field(default_factory=dict, metadata=_FLOW)
    # Engine call id -> the declared tool call it runs, for the calls of a batch: a result is bound to its call by this.
    call_slots: dict[int, str] = field(default_factory=dict, metadata=_FLOW)
    # The declared call about to be judged (set before a batch call is judged, taken by the chokepoint).
    pending_call_id: str = field(default="", metadata=_FLOW)
    parallel_tool_batch: list[dict] = field(default_factory=list, metadata=_FLOW)
    # Calls from one model reply still to run, one at a time, when parallel is off.
    queued_tools: list[dict] = field(default_factory=list, metadata=_FLOW)
    # Last LLM call's OpenAI ``tools`` names (EngineIoReturn.offered_tools).
    # None until an LLM return records them; kept across tool results so
    # egress governance can compare the pending tool to what the model saw.
    offered_tools: tuple[str, ...] | None = field(default=None, metadata=_SESSION)
    # The session's running spec as of the last step (not part of the snapshot: the session keeps it).
    # Governance reads it to tell a tool or peer that was never declared from one that is declared but unavailable.
    running_spec: dict | None = field(default=None, repr=False, compare=False, metadata=_SESSION)
    dp_data: dict = field(default_factory=dict, metadata=_PATTERN)


def reset_turn_scope(q: QProduct) -> None:
    """Put every turn-scoped field of ``q`` back to its initial value."""
    for f in fields(QProduct):
        if f.metadata.get("scope") == "turn":
            setattr(q, f.name, f.default_factory() if f.default_factory is not MISSING else f.default)


def end_turn(q: QProduct, *, keep_holds: bool = False) -> None:
    """A turn ended (answer, block, terminate, pause): put every flow-scoped field back to its default.

    The kernel is idle between turns, so nothing that moves a call along (waits, held approvals,
    pending tool, machine states) may outlive one. Turn and session fields are not touched here.
    The governance machine's ERROR is the one thing kept, so the failure of a plugin that raised stays visible
    after the turn it ended. It is not a latch: the next turn starts from IDLE and evaluates the plugins again.

    ``keep_holds`` is for a pause: the human answers still pending survive it (see ``restore_holds``).
    """
    holds = dict(q.hitl_holds) if keep_holds else {}
    # The rest of a batch whose call is held belongs to the held state: it runs after the held call.
    queued = list(q.queued_tools) if holds else []
    by_cid = dict(q.pending_tools_by_cid) if holds else {}
    slots = dict(q.call_slots) if holds else {}
    gov_failed = q.gov_state == "ERROR"
    for f in fields(QProduct):
        if f.metadata.get("scope") == "flow" and not (gov_failed and f.name == "gov_state"):
            setattr(q, f.name, f.default_factory() if f.default_factory is not MISSING else f.default)
    if holds:
        restore_holds(q, holds)
        q.queued_tools = queued
        q.pending_tools_by_cid = by_cid
        q.call_slots = slots


def restore_holds(q: QProduct, holds: dict[int, HitlHold]) -> None:
    """Put pending human answers back on an otherwise idle state, coherent with their kind.

    A hold is a gov-machine wait: gov is HITL_PENDING, each hold has its HITL wait, the dp waits on ingress and a
    held tool call keeps the tool machine in WAIT_GOV with the control pipeline at AUTHZ.
    """
    q.hitl_holds = holds
    q.gov_state = "HITL_PENDING"
    for hold in holds.values():
        q.outbound_waits.append(OutboundWait(correlation_id=hold.request_id, kind="HITL", op=hold.pending_schedule))
    q.inflight_correlation_ids = [w.correlation_id for w in q.outbound_waits]
    q.dp = DpState.AWAITING_INGRESS
    q.inflight_kind = "TOOL"
    if any(not hold.is_ingress for hold in holds.values()):
        q.tool = ToolState.WAIT_GOV
        q.control_phase = "AUTHZ"


def copy_state(q: QProduct) -> QProduct:
    """A deep copy of the product state for a rollback. The running spec is shared: it is large and read-only."""
    return copy.deepcopy(q, memo={id(q.running_spec): q.running_spec})


def restore_state(q: QProduct, saved: QProduct) -> None:
    """Put ``q`` back to ``saved`` in place, so holders of ``q`` see the rollback."""
    for f in fields(QProduct):
        setattr(q, f.name, getattr(saved, f.name))

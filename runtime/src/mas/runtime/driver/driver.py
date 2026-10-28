#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Runtime driver — closes the kernel loop with mock HITL, engine, and ctx."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from mas.runtime.boundary.coordination.chokepoint import ChokepointCoordinator
from mas.runtime.boundary.hitl.responders import HitlResponder
from mas.runtime.boundary.ingress_validate import validate_ingress
from mas.runtime.boundary.obs.exchange_plugin import ExchangePlugin
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.contracts.tool_semantics import bound_tool_semantics, existing_attr
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.exchange_preview import ExchangeSnapshot
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.engine.worker_pool import DEFAULT_ENGINE_QUEUE_DEPTH, EngineWorkerPool
from mas.runtime.kernel.inflight import pending_for_validate, register_inflight
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.kernel.run_event import max_event_seq, new_run_events
from mas.runtime.kernel.runtime_context import runtime_binding
from mas.runtime.kernel.state import RunLedger
from mas.runtime.machines.gov import gov_is_hitl_pending
from mas.runtime.schema.egress import (
    EgressKind,
    EgressSymbol,
    EmitClientResponse,
    EmitHitlRequest,
    InvokeEngineIo,
    RaiseBoundaryError,
    RequestCtxAssembly,
)
from mas.runtime.schema.ingress import EngineIoReturn, IngressSymbol, OperatorSteerReceived, UserInputReceived
from mas.runtime.schema.observability import ObsEventKind
from mas.runtime.spec.defaults import DEFAULT_MAX_AUTO_STEPS

_logger = logging.getLogger(__name__)


def _inbound_scope(event: IngressSymbol):
    sender = str(getattr(event, "sender", "") or "")
    intent = str(getattr(event, "intent", "") or "")
    if not sender and not intent:
        return nullcontext()
    from mas.runtime.boundary.agentcomm.protocol import bound_inbound

    return bound_inbound(sender=sender, intent=intent)


def _within_auto_step_budget(auto_steps: int, max_auto_steps: int | None) -> bool:
    """True while the driver may take another dispatch step.

    ``max_auto_steps is None`` means no cap (lab default). A production
    overlay such as ``with-hardened`` sets an integer.
    """
    return max_auto_steps is None or auto_steps < max_auto_steps


ExchangeKind = Literal[
    "user_in",
    "user_out",
    "llm_request",
    "llm_response",
    "tool_call",
    "tool_result",
    "gov_block",
]


@dataclass
class ExchangeRecord:
    """One USER↔AGENT↔LLM↔TOOL hop. Typed fields only; pretty-print is a view."""

    kind: ExchangeKind
    text: str = ""
    ts_mono: float = 0.0
    ts_wall: str = ""
    agent_id: str = ""
    correlation_id: int | None = None
    op: str | None = None
    response_kind: str | None = None
    finish_reason: str | None = None
    next_step: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    semantics: dict[str, Any] | None = None
    destructive: bool = False
    model: str | None = None
    messages: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    tools_note: str = ""
    engine_raw: str = ""
    policy_name: str | None = None
    # Set on user_in/user_out when this exchange is actually a delegated turn
    # (a peer agent, not the human user, is the real other end) -- see
    # KernelDriver.caller_agent_id. None means the plain USER/AGENT boundary.
    caller_agent_id: str | None = None
    cache_status: str | None = None
    cache_layer: str = ""


def engine_model_id(engine: Any) -> str:
    """Model id the engine will actually call (unwraps infra middleware).

    Uses :func:`existing_attr` so a spec-less ``MagicMock`` cannot auto-create
    ``.inner`` / ``.model`` and allocate an unbounded mock tree.
    """
    seen: set[int] = set()
    cur = engine
    for _ in range(8):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        model = existing_attr(cur, "model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        inner = existing_attr(cur, "inner")
        if inner is None or inner is cur:
            break
        cur = inner
    return ""


def engine_manifest(engine: Any) -> dict[str, Any] | None:
    """Agent manifest on the engine, unwrapping infra ``.inner`` wrappers."""
    seen: set[int] = set()
    cur = engine
    for _ in range(8):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        manifest = existing_attr(cur, "manifest")
        if isinstance(manifest, dict):
            return manifest
        inner = existing_attr(cur, "inner")
        if inner is None or inner is cur:
            break
        cur = inner
    return None


def _exchange_timestamp() -> tuple[float, str]:
    return (
        time.perf_counter(),
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    )


def _engine_payload_json(obj: object) -> str:
    if hasattr(obj, "model_dump"):
        data = obj.model_dump(mode="json")  # type: ignore[union-attr]
    elif isinstance(obj, dict):
        data = obj
    else:
        return str(obj)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _engine_snapshot(engine: Any, op: str, *, correlation_id: int = 0) -> ExchangeSnapshot | None:
    snap_fn = getattr(engine, "exchange_snapshot", None)
    if not callable(snap_fn):
        return None
    snap = snap_fn(op, correlation_id=correlation_id)
    return snap if isinstance(snap, ExchangeSnapshot) else None


def _engine_invoke_record(
    sym: InvokeEngineIo,
    *,
    engine: Any,
    q: Any,
    agent_id: str,
    ts_mono: float,
    ts_wall: str,
    engine_raw: str,
    ctx: Any = None,
) -> tuple[ExchangeRecord, str]:
    """Build the outbound engine ExchangeRecord. Returns (record, tool_name to track)."""
    if sym.op == "TOOL_CALL":
        tool_name = ""
        tool_arguments: dict[str, Any] | None = None
        by_cid = q.pending_tools_by_cid.get(sym.correlation_id)
        if by_cid is not None:
            tool_name, raw_args = by_cid[0], by_cid[1]
            tool_arguments = dict(raw_args or {})
        elif q.pending_tool_name:
            tool_name = q.pending_tool_name
            tool_arguments = dict(q.pending_tool_args or {})
        return (
            ExchangeRecord(
                kind="tool_call",
                ts_mono=ts_mono,
                ts_wall=ts_wall,
                agent_id=agent_id,
                correlation_id=sym.correlation_id,
                op=sym.op,
                tool_name=tool_name or None,
                tool_arguments=tool_arguments,
                semantics=bound_tool_semantics(engine, tool_name or None, tool_arguments, ctx=ctx),
                destructive=sym.destructive,
                model=engine_model_id(engine) or None,
                engine_raw=engine_raw,
            ),
            tool_name,
        )
    snap = _engine_snapshot(engine, sym.op, correlation_id=sym.correlation_id) if engine is not None else None
    return (
        ExchangeRecord(
            kind="llm_request",
            text=(snap.note if snap is not None else ""),
            ts_mono=ts_mono,
            ts_wall=ts_wall,
            agent_id=agent_id,
            correlation_id=sym.correlation_id,
            op=sym.op,
            messages=snap.messages if snap is not None else None,
            tools=snap.tools if snap is not None else None,
            tools_note=snap.tools_note if snap is not None else "",
            model=engine_model_id(engine) or None,
            engine_raw=engine_raw,
        ),
        "",
    )


@dataclass
class DriverStep:
    ingress: IngressSymbol
    egress: list[EgressSymbol]


@dataclass
class DriverTrace:
    steps: list[DriverStep] = field(default_factory=list)
    client_responses: list[EmitClientResponse] = field(default_factory=list)
    hitl_requests: list[EmitHitlRequest] = field(default_factory=list)
    boundary_errors: list[RaiseBoundaryError] = field(default_factory=list)
    rejected_ingress: list[IngressSymbol] = field(default_factory=list)
    awaiting_hitl: bool = False
    observability_events: list = field(default_factory=list)
    exchanges: list[ExchangeRecord] = field(default_factory=list)


@dataclass
class KernelDriver:
    """Feed ingress, auto-dispatch egress to mock HITL/engine/ctx until quiescent."""

    kernel: RuntimeKernel
    hitl: HitlResponder | None = None
    engine: SimulatedEngine | None = None
    engine_pool: EngineWorkerPool | None = None
    ctx: AutoCtxAssembler | None = field(default_factory=AutoCtxAssembler)
    observability: ObservabilityOperator | None = field(default_factory=ObservabilityOperator)
    coordination: ChokepointCoordinator | None = field(default_factory=ChokepointCoordinator)
    max_auto_steps: int | None = DEFAULT_MAX_AUTO_STEPS
    agent_id: str = "agent"
    # When this driver is running a delegated turn, the specialist's final
    # text goes back to this caller — not to the user. Empty means this
    # agent is the MAS entry and EmitClientResponse is AGENT->USER.
    caller_agent_id: str = ""
    # Read-only, additive subscribers for the driver's own display-oriented
    # exchange log (see ExchangeRecord/ExchangePlugin) — mirrors
    # ObservabilityOperator._subscribers: subscribe_exchange()/
    # unsubscribe_exchange() append/remove entries in place, so registering
    # a new plugin (e.g. mas-ctl's own CLI trace plugin, or an external
    # chat-UI plugin) never silently discards another one already
    # registered — unlike the single mutable callback this replaced, which
    # a caller re-running setup every turn could (and did) overwrite.
    exchange_plugins: list[ExchangePlugin] = field(default_factory=list)
    capture_engine_io: bool = False
    # One id for the whole run. Defaults to a fresh id per driver (correct
    # for a single-agent run); re-adopted from UserInputReceived.session_id
    # on EVERY UserInputReceived fed (see the isinstance(ingress,
    # UserInputReceived) branch below), not just the first — in practice
    # this only matters once, since the one production caller
    # (SessionController._run_user_turn) always passes its own fixed
    # self.session_id on every turn, so the value never actually changes
    # after the first assignment. A caller that skips SessionController and
    # calls RuntimeInstance.run_user_text() directly without threading
    # session_id through every call gets a FRESH uuid minted per call
    # instead (UserInputReceived.session_id's own default_factory) — this
    # field does not itself enforce "one value for the run," it only
    # reflects whatever the caller last passed in. A caller that wants
    # every agent in a multi-agent MAS to share one value passes the SAME
    # session_id into every RuntimeInstance.run_user_text()/UserInputReceived
    # it constructs (see mas.ctl.session.controller.SessionController.session_id
    # and mas.ctl.executor.mas_session.make_workflow_send, which do exactly
    # this for delegation). Deliberately independent of observability — this
    # is a core runtime identifier, not something an optional plugin should
    # own or gate.
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _tool_by_correlation_id: dict[int, str] = field(default_factory=dict)  # Track tool name per correlation_id
    _semantics_by_correlation_id: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Task id of the turn currently being processed — set from
    # UserInputReceived.task_id and carried onto every transition (ingress
    # and egress alike) produced while that turn runs, until the next
    # UserInputReceived replaces it. See GovTransition.task_id.
    _current_task_id: str = field(default="", repr=False)
    _upstream_correlation_id: int | None = field(default=None, repr=False)
    _gov_exchange_seq: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.engine_pool is None and self.engine is not None:
            depth = self.kernel.config.engine_queue_depth if self.kernel is not None else DEFAULT_ENGINE_QUEUE_DEPTH
            self.engine_pool = EngineWorkerPool(worker=self._invoke_engine, max_depth=depth)
        if self.ctx is not None:
            if self.observability is not None:
                self.ctx.observability = self.observability
            if self.engine is not None:
                self.ctx.engine = self.engine
                bound_manifest = engine_manifest(self.engine)
                if bound_manifest is not None:
                    self.ctx.manifest = bound_manifest

    def feed(self, event: IngressSymbol) -> DriverTrace:
        with runtime_binding(self.coordination, self.observability), _inbound_scope(event):
            return self._feed_bounded(event)

    async def afeed(self, event: IngressSymbol) -> DriverTrace:
        with runtime_binding(self.coordination, self.observability), _inbound_scope(event):
            return await self._afeed_bounded(event)

    def _working_memory_store(self) -> Any | None:
        ctx = self.ctx
        if ctx is None:
            return None
        return getattr(ctx, "working_memory", None)

    def _run_ledger(self) -> RunLedger | None:
        run = getattr(self.kernel, "run", None)
        return run if isinstance(run, RunLedger) else None

    def _ledger_events(self) -> list[Any]:
        events = getattr(getattr(self.kernel, "run", None), "events", None)
        return list(events) if isinstance(events, (list, tuple)) else []

    def _after_kernel_step(self, ingress: IngressSymbol, seen_seq: int) -> None:
        """Copy facts this kernel step just logged into the chat transcript."""
        run = self._run_ledger()
        rows = run.since(seen_seq) if run is not None else new_run_events(self._ledger_events(), seen_seq)
        self._project_log_events(rows)
        if self.coordination is not None:
            self.coordination.on_internal_mutation(self.kernel.q, label=ingress.kind.value)

    def _kernel_step(self, ingress: IngressSymbol):
        run = self._run_ledger()
        seen_seq = run.max_seq() if run is not None else max_event_seq(self._ledger_events())
        with _inbound_scope(ingress):
            result = self.kernel.transition(ingress)
        self._after_kernel_step(ingress, seen_seq)
        return result

    def _feed_bounded(self, event: IngressSymbol) -> DriverTrace:
        trace = DriverTrace()
        queue: deque[IngressSymbol] = deque([event])
        auto_steps = 0

        while queue and _within_auto_step_budget(auto_steps, self.max_auto_steps):
            ingress = queue.popleft()
            if not validate_ingress(
                ingress,
                last_correlation_id=(self.kernel.run.records[-1].correlation_id if self.kernel.run.records else 0),
                pending_correlation_id=self.kernel.q.pending_engine_correlation_id,
                inflight_correlation_ids=pending_for_validate(self.kernel.q),
            ):
                trace.rejected_ingress.append(ingress)
                continue

            if isinstance(ingress, UserInputReceived):
                self._current_task_id = ingress.task_id
                self._upstream_correlation_id = ingress.upstream_correlation_id
                # Adopt whatever this turn's UserInputReceived carries as the
                # session id from here on — freshly minted for a genuinely
                # new session (nothing set one yet), or the propagated value
                # for a continuing session/delegated call. Either way this
                # replaces the placeholder session_id this driver was built
                # with (see KernelDriver.session_id).
                self.session_id = ingress.session_id
                # Sync session_id and agent_id to ctx for system tools
                if self.ctx is not None:
                    self.ctx.session_id = self.session_id
                    self.ctx.agent_id = self.agent_id
                if self.observability is not None:
                    # Same values, same source, as what governance sees on
                    # this and every subsequent transition this turn (see
                    # _notify_governance) — so observability logs match.
                    self.observability.set_context(
                        session_id=self.session_id,
                        task_id=self._current_task_id,
                        upstream_correlation_id=self._upstream_correlation_id,
                    )
                if self.ctx is not None:
                    # Emit USER->AGENT exchange for trace visibility -- unless
                    # this is a delegated turn, in which case it is really
                    # from the caller agent, not the user.
                    ts_mono, ts_wall = _exchange_timestamp()
                    caller = str(self.caller_agent_id or "").strip()
                    is_delegated = bool(caller) and caller != str(self.agent_id or "").strip()
                    self._emit_exchange(
                        trace,
                        ExchangeRecord(
                            kind="user_in",
                            text=ingress.text,
                            ts_mono=ts_mono,
                            ts_wall=ts_wall,
                            agent_id=self.agent_id,
                            caller_agent_id=caller if is_delegated else None,
                        ),
                    )
                    note = getattr(self.ctx, "note_user_input", None)
                    if callable(note):
                        note(ingress.text)

            err = self._notify_governance("ingress", ingress)
            if err is not None:
                self._dispatch_egress(err, trace)
                break

            result = self._kernel_step(ingress)
            trace.steps.append(DriverStep(ingress=ingress, egress=list(result.egress)))

            halt_err: RaiseBoundaryError | None = None
            for sym in result.egress:
                err = self._notify_governance("egress", sym)
                if err is not None:
                    halt_err = err
                    break
            if halt_err is not None:
                self._dispatch_egress(halt_err, trace)
                break

            if self.observability is not None:
                self.observability.record_ingress(ingress, self.kernel.q)
                for sym in result.egress:
                    if self.coordination is not None:
                        if sym.kind == EgressKind.INVOKE_ENGINE_IO and isinstance(sym, InvokeEngineIo):
                            self.coordination.after_egress_allowed(self.kernel.q)
                        elif sym.kind == EgressKind.EMIT_HITL_REQUEST:
                            self.coordination.on_egress_hitl(self.kernel.q)
                    if sym.kind != EgressKind.INVOKE_ENGINE_IO:
                        self.observability.record_egress(sym, self.kernel.q)
            self._emit_gov_block_exchanges(trace)
            auto_steps += 1

            engine_ios: list[InvokeEngineIo] = []
            for sym in result.egress:
                if sym.kind == EgressKind.INVOKE_ENGINE_IO and isinstance(sym, InvokeEngineIo):
                    engine_ios.append(sym)
                else:
                    queue.extend(self._dispatch_egress(sym, trace))
            if engine_ios:
                queue.extend(self._dispatch_engine_batch(engine_ios, trace))

        if queue:
            # auto_steps hit max_auto_steps while items were still queued —
            # anything left in the queue will be abandoned: exit with error
            trace.boundary_errors.append(
                RaiseBoundaryError(
                    code="STEP_BUDGET_EXHAUSTED",
                    recoverable=False,
                    message=(
                        f"max_auto_steps ({self.max_auto_steps}) exhausted with "
                        f"{len(queue)} item(s) still queued and undispatched "
                    ),
                )
            )
        trace.awaiting_hitl = gov_is_hitl_pending(self.kernel.q)
        if self.observability is not None:
            trace.observability_events = list(self.observability.events)
        return trace

    async def _afeed_bounded(self, event: IngressSymbol) -> DriverTrace:
        """Async twin of :meth:`_feed_bounded` — only the engine batch awaits."""
        trace = DriverTrace()
        queue: deque[IngressSymbol] = deque([event])
        auto_steps = 0

        while queue and _within_auto_step_budget(auto_steps, self.max_auto_steps):
            ingress = queue.popleft()
            if not validate_ingress(
                ingress,
                last_correlation_id=(self.kernel.run.records[-1].correlation_id if self.kernel.run.records else 0),
                pending_correlation_id=self.kernel.q.pending_engine_correlation_id,
                inflight_correlation_ids=pending_for_validate(self.kernel.q),
            ):
                trace.rejected_ingress.append(ingress)
                continue

            if isinstance(ingress, UserInputReceived):
                self._current_task_id = ingress.task_id
                self._upstream_correlation_id = ingress.upstream_correlation_id
                self.session_id = ingress.session_id
                if self.ctx is not None:
                    self.ctx.session_id = self.session_id
                    self.ctx.agent_id = self.agent_id
                if self.observability is not None:
                    self.observability.set_context(
                        session_id=self.session_id,
                        task_id=self._current_task_id,
                        upstream_correlation_id=self._upstream_correlation_id,
                    )
                if self.ctx is not None:
                    ts_mono, ts_wall = _exchange_timestamp()
                    caller = str(self.caller_agent_id or "").strip()
                    is_delegated = bool(caller) and caller != str(self.agent_id or "").strip()
                    self._emit_exchange(
                        trace,
                        ExchangeRecord(
                            kind="user_in",
                            text=ingress.text,
                            ts_mono=ts_mono,
                            ts_wall=ts_wall,
                            agent_id=self.agent_id,
                            caller_agent_id=caller if is_delegated else None,
                        ),
                    )
                    note = getattr(self.ctx, "note_user_input", None)
                    if callable(note):
                        note(ingress.text)

            err = self._notify_governance("ingress", ingress)
            if err is not None:
                self._dispatch_egress(err, trace)
                break

            result = self._kernel_step(ingress)
            trace.steps.append(DriverStep(ingress=ingress, egress=list(result.egress)))

            halt_err: RaiseBoundaryError | None = None
            for sym in result.egress:
                err = self._notify_governance("egress", sym)
                if err is not None:
                    halt_err = err
                    break
            if halt_err is not None:
                self._dispatch_egress(halt_err, trace)
                break

            if self.observability is not None:
                self.observability.record_ingress(ingress, self.kernel.q)
                for sym in result.egress:
                    if self.coordination is not None:
                        if sym.kind == EgressKind.INVOKE_ENGINE_IO and isinstance(sym, InvokeEngineIo):
                            self.coordination.after_egress_allowed(self.kernel.q)
                        elif sym.kind == EgressKind.EMIT_HITL_REQUEST:
                            self.coordination.on_egress_hitl(self.kernel.q)
                    if sym.kind != EgressKind.INVOKE_ENGINE_IO:
                        self.observability.record_egress(sym, self.kernel.q)
            self._emit_gov_block_exchanges(trace)
            auto_steps += 1

            engine_ios: list[InvokeEngineIo] = []
            for sym in result.egress:
                if sym.kind == EgressKind.INVOKE_ENGINE_IO and isinstance(sym, InvokeEngineIo):
                    engine_ios.append(sym)
                else:
                    queue.extend(self._dispatch_egress(sym, trace))
            if engine_ios:
                queue.extend(await self._adispatch_engine_batch(engine_ios, trace))

        if queue:
            trace.boundary_errors.append(
                RaiseBoundaryError(
                    code="STEP_BUDGET_EXHAUSTED",
                    recoverable=False,
                    message=(
                        f"max_auto_steps ({self.max_auto_steps}) exhausted with "
                        f"{len(queue)} item(s) still queued and undispatched "
                    ),
                )
            )
        trace.awaiting_hitl = gov_is_hitl_pending(self.kernel.q)
        if self.observability is not None:
            trace.observability_events = list(self.observability.events)
        return trace

    def run(self, events: list[IngressSymbol]) -> DriverTrace:
        merged = DriverTrace()
        for event in events:
            part = self.feed(event)
            merged.steps.extend(part.steps)
            merged.client_responses.extend(part.client_responses)
            merged.hitl_requests.extend(part.hitl_requests)
            merged.boundary_errors.extend(part.boundary_errors)
            merged.rejected_ingress.extend(part.rejected_ingress)
            merged.exchanges.extend(part.exchanges)
            merged.awaiting_hitl = part.awaiting_hitl
        return merged

    def _dispatch_egress(self, sym: EgressSymbol, trace: DriverTrace) -> list[IngressSymbol]:
        if sym.kind == EgressKind.INVOKE_ENGINE_IO:
            assert isinstance(sym, InvokeEngineIo)
            return self._dispatch_engine_batch([sym], trace)

        if sym.kind == EgressKind.REQUEST_CTX_ASSEMBLY and self.ctx is not None:
            assert isinstance(sym, RequestCtxAssembly)
            if hasattr(self.ctx, "q_product"):
                self.ctx.q_product = self.kernel.q
            return [self.ctx.complete(sym)]

        if sym.kind == EgressKind.EMIT_HITL_REQUEST:
            assert isinstance(sym, EmitHitlRequest)
            trace.hitl_requests.append(sym)
            if self.hitl is not None:
                return [self.hitl.resolve(sym)]
            return []

        if sym.kind == EgressKind.EMIT_CLIENT_RESPONSE:
            assert isinstance(sym, EmitClientResponse)
            ts_mono, ts_wall = _exchange_timestamp()
            caller = str(self.caller_agent_id or "").strip()
            is_delegated = bool(caller) and caller != str(self.agent_id or "").strip()
            self._emit_exchange(
                trace,
                ExchangeRecord(
                    kind="user_out",
                    text=sym.content,
                    finish_reason=sym.finish_reason,
                    ts_mono=ts_mono,
                    ts_wall=ts_wall,
                    agent_id=self.agent_id,
                    caller_agent_id=caller if is_delegated else None,
                ),
            )
            trace.client_responses.append(sym)
            return []

        if sym.kind == EgressKind.RAISE_BOUNDARY_ERROR:
            assert isinstance(sym, RaiseBoundaryError)
            self._emit_gov_block_from_symbol(trace, sym)
            trace.boundary_errors.append(sym)
            return []

        return []

    def subscribe_exchange(self, plugin: ExchangePlugin) -> None:
        """Register a read-only ExchangePlugin (additive; never overwrites others)."""
        if plugin not in self.exchange_plugins:
            self.exchange_plugins.append(plugin)

    def unsubscribe_exchange(self, plugin: ExchangePlugin) -> None:
        """Remove a previously subscribed ExchangePlugin, if still present."""
        if plugin in self.exchange_plugins:
            self.exchange_plugins.remove(plugin)

    def _emit_exchange(self, trace: DriverTrace, record: ExchangeRecord) -> None:
        trace.exchanges.append(record)
        for plugin in self.exchange_plugins:
            plugin.on_exchange(record)

    def _gov_block_exchange(self, *, policy_name: str, text: str) -> ExchangeRecord:
        ts_mono, ts_wall = _exchange_timestamp()
        return ExchangeRecord(
            kind="gov_block",
            text=text,
            ts_mono=ts_mono,
            ts_wall=ts_wall,
            agent_id=self.agent_id,
            policy_name=policy_name or None,
        )

    def _emit_gov_block_from_symbol(self, trace: DriverTrace, sym: RaiseBoundaryError) -> None:
        if sym.code not in {"GOV_BLOCK", "GOV_TERMINATE"}:
            return
        if any(ex.kind == "gov_block" and ex.policy_name == (sym.policy_name or None) for ex in trace.exchanges):
            return
        self._emit_exchange(
            trace,
            self._gov_block_exchange(policy_name=sym.policy_name, text=sym.message or sym.code),
        )

    def _emit_gov_block_exchanges(self, trace: DriverTrace) -> None:
        """Print AGENT->GOV on --trace when egress governance BLOCKs, including
        the TOOL_CALL recovery path that never raises RaiseBoundaryError."""
        if self.observability is None:
            return
        for ev in self.observability.events:
            if ev.seq <= self._gov_exchange_seq:
                continue
            self._gov_exchange_seq = ev.seq
            if ev.kind != ObsEventKind.GOVERNANCE_DECISION:
                continue
            payload = ev.payload or {}
            if payload.get("hook") != "egress" or payload.get("checkpoint") != "after":
                continue
            if payload.get("decision") not in {"BLOCK", "TERMINATE"}:
                continue
            policy_name = ev.policy_name or str(payload.get("policy_name") or "")
            text = str(payload.get("reason") or payload.get("decision") or "")
            if any(
                ex.kind == "gov_block" and (ex.policy_name or "") == policy_name and ex.text == text
                for ex in trace.exchanges
            ):
                continue
            self._emit_exchange(trace, self._gov_block_exchange(policy_name=policy_name, text=text))

    def _notify_governance(
        self, hook: Literal["ingress", "egress"], symbol: IngressSymbol | EgressSymbol
    ) -> RaiseBoundaryError | None:
        """Give every governance plugin the ingress/egress symbol, read-only.

        Single hook (``on_transition(GovTransition)``), not one bespoke
        ``note_*`` method per symbol kind — replaces the old
        ``note_user_input``-only wiring, which only ever told the plugin
        about ``UserInputReceived`` and nothing else, and carried just the
        text, with none of the session/task/spec/state context a real
        decision needs. See ``mas.runtime.boundary.gov.transition`` for the
        full shape.

        A plugin narrows what it receives by implementing
        ``transition_filters() -> list[GovTransitionFilter]`` — matched
        with OR semantics, i.e. any filter matching is enough. No filters
        (the method absent, or returning empty) means "everything",
        matching ``GovTransitionFilter``'s own "empty matches all" rule.

        Called synchronously (unlike observability's ``on_transition``,
        which may be dispatched on a background worker once
        ``enable_async_plugins`` is on) — a later ``evaluate_egress`` may
        depend on state an earlier observer set (e.g. Casa stashing this
        turn's user query). An observer bug must not fail open: exceptions
        move M_gov to ERROR and return ``RaiseBoundaryError(code="GOV_PLUGIN_ERROR")``.
        """
        from mas.runtime.boundary.gov.transition import build_gov_transition
        from mas.runtime.kernel.coupling import apply_gov_error, gov_plugin_boundary_error

        gov = getattr(getattr(self.kernel, "config", None), "egress_governance_plugin", None)
        on_transition = getattr(gov, "on_transition", None)
        if callable(on_transition):
            try:
                transition = build_gov_transition(
                    hook,
                    symbol,
                    q=self.kernel.q,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    task_id=self._current_task_id,
                    agent_spec=getattr(self.kernel.config, "agent_spec", None),
                )
                get_filters = getattr(gov, "transition_filters", None)
                if callable(get_filters):
                    filters = get_filters()
                    if not (filters and not any(f.matches(transition) for f in filters)):
                        on_transition(transition)
                else:
                    on_transition(transition)
            except Exception as exc:
                apply_gov_error(self.kernel.q)
                return gov_plugin_boundary_error(exc)
        return self._notify_runtime_plugins(hook, symbol)

    def _notify_runtime_plugins(
        self, hook: Literal["ingress", "egress"], symbol: IngressSymbol | EgressSymbol
    ) -> RaiseBoundaryError | None:
        """gdb and other runtime plugins observe; they never BLOCK."""
        from mas.runtime.boundary.gov.transition import build_gov_transition

        plugins = tuple(getattr(getattr(self.kernel, "config", None), "runtime_plugins", ()) or ())
        if not plugins:
            return None
        try:
            transition = build_gov_transition(
                hook,
                symbol,
                q=self.kernel.q,
                agent_id=self.agent_id,
                session_id=self.session_id,
                task_id=self._current_task_id,
                agent_spec=getattr(self.kernel.config, "agent_spec", None),
            )
        except Exception:
            _logger.debug("runtime plugin transition build failed", exc_info=True)
            return None
        for plugin in plugins:
            on_transition = getattr(plugin, "on_transition", None)
            if not callable(on_transition):
                continue
            get_filters = getattr(plugin, "transition_filters", None)
            if callable(get_filters):
                filters = get_filters()
                if filters and not any(f.matches(transition) for f in filters):
                    continue
            try:
                on_transition(transition)
            except Exception:
                _logger.debug("runtime plugin failed", exc_info=True)
        return None

    def _invoke_engine(self, io: InvokeEngineIo) -> EngineIoReturn:
        """Run one engine op. An exception still becomes an ERROR return so the
        envelope can close the matching start event (tool_call_end / llm_call_end).
        """
        if self.engine is None:
            raise RuntimeError("no engine configured")
        try:
            return self.engine.invoke(io)
        except Exception as exc:
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=str(exc),
            )

    async def _ainvoke_engine(self, io: InvokeEngineIo) -> EngineIoReturn:
        """Async twin of :meth:`_invoke_engine` — awaits ``engine.ainvoke``.

        Hard interrupt cancels the inner LLM task only. Tools are not
        rolled back. An outer cancellation of this driver still propagates.
        """
        if self.engine is None:
            raise RuntimeError("no engine configured")
        from mas.runtime.engine.inflight_llm import clear as clear_inflight
        from mas.runtime.engine.inflight_llm import register as register_inflight_llm

        inner = asyncio.create_task(self.engine.ainvoke(io))
        register_inflight_llm(self.session_id, inner)
        try:
            return await inner
        except asyncio.CancelledError:
            if inner.cancelled():
                from mas.runtime.engine.inflight_llm import peek_preempt, peek_replace, take_partial

                if peek_preempt(self.session_id) is not None:
                    return EngineIoReturn(
                        correlation_id=io.correlation_id,
                        response_kind="MODEL_TEXT",
                        next_step="STOP",
                        text=take_partial(self.session_id),
                        finish_reason="preempted",
                    )
                if peek_replace(self.session_id) is not None:
                    take_partial(self.session_id)
                    return EngineIoReturn(
                        correlation_id=io.correlation_id,
                        response_kind="MODEL_TEXT",
                        next_step="STOP",
                        text="",
                        finish_reason="replaced",
                    )
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="ERROR",
                    next_step="STOP",
                    text="cancelled",
                    finish_reason="cancelled",
                )
            inner.cancel()
            try:
                await inner
            except (asyncio.CancelledError, Exception):
                pass
            raise
        except Exception as exc:
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=str(exc),
            )
        finally:
            clear_inflight(self.session_id)

    def _dispatch_engine_batch(self, ios: list[InvokeEngineIo], trace: DriverTrace) -> list[IngressSymbol]:
        q = self.kernel.q
        engine = self.engine
        pool = self.engine_pool
        if pool is None and engine is None:
            return []
        direct: list[IngressSymbol] = []
        parallel_group_id: str | None = None
        if len(ios) > 1 and all(sym.op == "TOOL_CALL" for sym in ios):
            parallel_group_id = str(uuid.uuid4())
            self._record_parallel_group_obs(ios, q, boundary="start", group_id=parallel_group_id)
        for sym in ios:
            ts_mono, ts_wall = _exchange_timestamp()
            engine_raw = _engine_payload_json(sym) if self.capture_engine_io else ""
            record, tracked_tool = _engine_invoke_record(
                sym,
                engine=engine,
                q=q,
                agent_id=self.agent_id,
                ts_mono=ts_mono,
                ts_wall=ts_wall,
                engine_raw=engine_raw,
                ctx=self.ctx,
            )
            self._emit_exchange(trace, record)
            if tracked_tool:
                self._tool_by_correlation_id[sym.correlation_id] = tracked_tool
                if record.semantics:
                    self._semantics_by_correlation_id[sym.correlation_id] = record.semantics
            if sym.op == "TOOL_CALL" and engine is not None:
                by_cid = q.pending_tools_by_cid.get(sym.correlation_id)
                if by_cid is not None:
                    setter = getattr(engine, "set_tool_for_correlation", None)
                    if callable(setter):
                        setter(sym.correlation_id, by_cid[0], by_cid[1])
                    else:
                        setter = getattr(engine, "set_scheduled_tool", None)
                        if callable(setter):
                            setter(by_cid[0], by_cid[1])
                elif q.pending_tool_name:
                    setter = getattr(engine, "set_scheduled_tool", None)
                    if callable(setter):
                        setter(q.pending_tool_name, q.pending_tool_args)
            egress_kind = "MODEL" if sym.op == "LLM_CALL" else "TOOL"
            register_inflight(q, sym.correlation_id, kind=egress_kind, op=sym.op)
            from mas.runtime.boundary.gov.telemetry import get_bound_observability
            from mas.runtime.kernel.envelope import (
                EnvelopeContext,
                contract_kind_for_op,
                run_contract_execute_obs,
            )

            # q.pending_tool_name/pending_tool_args are single shared fields that
            # only ever hold the LAST tool spec set by schedule_parallel_tools_egress's
            # loop — by the time this per-sym loop runs, every sym would read the
            # same stale (name, args) unless we look each one up by its own
            # correlation_id via pending_tools_by_cid (populated per-spec in
            # parallel_tools.py). Fall back to the shared fields for the
            # single-tool-call case, where pending_tools_by_cid is never populated.
            by_cid_obs = q.pending_tools_by_cid.get(sym.correlation_id)
            obs_tool_name, obs_tool_args = (
                by_cid_obs if by_cid_obs is not None else (q.pending_tool_name, q.pending_tool_args)
            )
            env_ctx = EnvelopeContext(
                q=q,
                correlation_id=sym.correlation_id,
                contract=contract_kind_for_op(sym.op),
                scheduled_op=sym.op,
                observability=get_bound_observability() or self.observability,
                tool_name=obs_tool_name,
                tool_arguments=dict(obs_tool_args or {}),
                destructive=sym.destructive,
            )
            run_contract_execute_obs(env_ctx)
            self._record_wait_state_obs(sym, q, boundary="start")
            # Attach this op's own resolved call_id (and parent_call_id) to
            # the invocation itself (see InvokeEngineIo.call_id/parent_call_id
            # docstrings) — the observability boundary has, by the
            # CONTRACT_EXECUTE step just above, already assigned this
            # (correlation_id, op) pair its stable call_id AND resolved its
            # parent (correctly, even for a batch of true siblings — see
            # ObservabilityOperator.begin_sibling_batch). Reading both back
            # here means the engine (and, transitively, a delegate_to_* tool
            # call) always has its own real identity AND its parent's to
            # forward — a real contract field, not a closure-captured lookup
            # done later by whichever session-orchestration code happens to
            # dispatch the delegate, and not something any downstream
            # observability plugin needs to reconstruct from a call stack of
            # its own.
            if env_ctx.observability is not None:
                own_call_id = env_ctx.observability.call_id_for(sym.correlation_id, sym.op)
                if own_call_id:
                    updates: dict = {"call_id": own_call_id}
                    parent_call_id = env_ctx.observability.parent_call_id_for(own_call_id)
                    if parent_call_id:
                        updates["parent_call_id"] = parent_call_id
                    sym = sym.model_copy(update=updates)
            if pool is not None:
                pool.submit(sym)
            else:
                ret = self._invoke_engine(sym)
                self._record_engine_return(trace, sym, ret)
                self._record_wait_state_obs(sym, q, boundary="end")
                direct.append(ret)
        if pool is None:
            if parallel_group_id is not None:
                self._record_parallel_group_obs(ios, q, boundary="end", group_id=parallel_group_id)
            return self._with_pending_steer(direct)
        results = pool.drain()
        out: list[IngressSymbol] = []
        for sym, ret in zip(ios, results, strict=True):
            self._record_engine_return(trace, sym, ret)
            self._record_wait_state_obs(sym, q, boundary="end")
            out.append(ret)
        if parallel_group_id is not None:
            self._record_parallel_group_obs(ios, q, boundary="end", group_id=parallel_group_id)
        return self._with_pending_steer(out)

    async def _adispatch_engine_batch(self, ios: list[InvokeEngineIo], trace: DriverTrace) -> list[IngressSymbol]:
        """Async twin: bookkeeping is identical, engine calls run via ``asyncio.gather``."""
        q = self.kernel.q
        engine = self.engine
        if engine is None and self.engine_pool is None:
            return []
        parallel_group_id: str | None = None
        if len(ios) > 1 and all(sym.op == "TOOL_CALL" for sym in ios):
            parallel_group_id = str(uuid.uuid4())
            self._record_parallel_group_obs(ios, q, boundary="start", group_id=parallel_group_id)
        prepared: list[InvokeEngineIo] = []
        for orig in ios:
            sym = orig
            ts_mono, ts_wall = _exchange_timestamp()
            engine_raw = _engine_payload_json(sym) if self.capture_engine_io else ""
            record, tracked_tool = _engine_invoke_record(
                sym,
                engine=engine,
                q=q,
                agent_id=self.agent_id,
                ts_mono=ts_mono,
                ts_wall=ts_wall,
                engine_raw=engine_raw,
                ctx=self.ctx,
            )
            self._emit_exchange(trace, record)
            if tracked_tool:
                self._tool_by_correlation_id[sym.correlation_id] = tracked_tool
                if record.semantics:
                    self._semantics_by_correlation_id[sym.correlation_id] = record.semantics
            if orig.op == "TOOL_CALL" and engine is not None:
                by_cid = q.pending_tools_by_cid.get(orig.correlation_id)
                if by_cid is not None:
                    setter = getattr(engine, "set_tool_for_correlation", None)
                    if callable(setter):
                        setter(orig.correlation_id, by_cid[0], by_cid[1])
                    else:
                        setter = getattr(engine, "set_scheduled_tool", None)
                        if callable(setter):
                            setter(by_cid[0], by_cid[1])
                elif q.pending_tool_name:
                    setter = getattr(engine, "set_scheduled_tool", None)
                    if callable(setter):
                        setter(q.pending_tool_name, q.pending_tool_args)
            egress_kind = "MODEL" if orig.op == "LLM_CALL" else "TOOL"
            register_inflight(q, orig.correlation_id, kind=egress_kind, op=orig.op)
            from mas.runtime.boundary.gov.telemetry import get_bound_observability
            from mas.runtime.kernel.envelope import (
                EnvelopeContext,
                contract_kind_for_op,
                run_contract_execute_obs,
            )

            by_cid_obs = q.pending_tools_by_cid.get(orig.correlation_id)
            obs_tool_name, obs_tool_args = (
                by_cid_obs if by_cid_obs is not None else (q.pending_tool_name, q.pending_tool_args)
            )
            env_ctx = EnvelopeContext(
                q=q,
                correlation_id=orig.correlation_id,
                contract=contract_kind_for_op(orig.op),
                scheduled_op=orig.op,
                observability=get_bound_observability() or self.observability,
                tool_name=obs_tool_name,
                tool_arguments=dict(obs_tool_args or {}),
                destructive=orig.destructive,
            )
            run_contract_execute_obs(env_ctx)
            self._record_wait_state_obs(orig, q, boundary="start")
            if env_ctx.observability is not None:
                own_call_id = env_ctx.observability.call_id_for(orig.correlation_id, orig.op)
                if own_call_id:
                    updates: dict = {"call_id": own_call_id}
                    parent_call_id = env_ctx.observability.parent_call_id_for(own_call_id)
                    if parent_call_id:
                        updates["parent_call_id"] = parent_call_id
                    orig = orig.model_copy(update=updates)
            prepared.append(orig)
        results = await asyncio.gather(*(self._ainvoke_engine(sym) for sym in prepared))
        out: list[IngressSymbol] = []
        for sym, ret in zip(prepared, results, strict=True):
            self._record_engine_return(trace, sym, ret)
            self._record_wait_state_obs(sym, q, boundary="end")
            out.append(ret)
        if parallel_group_id is not None:
            self._record_parallel_group_obs(ios, q, boundary="end", group_id=parallel_group_id)
        return self._with_pending_steer(out)

    def _with_pending_steer(self, returns: list[IngressSymbol]) -> list[IngressSymbol]:
        from mas.runtime.engine.inflight_llm import pop_preempt, pop_replace

        pending = pop_preempt(self.session_id)
        if pending is not None:
            steered = next(
                (item for item in returns if isinstance(item, EngineIoReturn) and item.finish_reason == "preempted"),
                None,
            )
            if steered is None:
                return returns
            return [
                *returns,
                OperatorSteerReceived(
                    steer_id=f"steer{steered.correlation_id}",
                    context_text=pending.text,
                    source=pending.source,
                    sender=pending.sender,
                    intent=pending.intent,
                ),
            ]
        replacement = pop_replace(self.session_id)
        if replacement is None:
            return returns
        replaced = next(
            (item for item in returns if isinstance(item, EngineIoReturn) and item.finish_reason == "replaced"),
            None,
        )
        if replaced is None:
            return returns
        return [
            *returns,
            UserInputReceived(
                user_turn_id=f"replace{replaced.correlation_id}",
                text=replacement.text,
                session_id=self.session_id,
                source=replacement.source,
                sender=replacement.sender,
                intent=replacement.intent,
            ),
        ]

    def _record_engine_return(
        self,
        trace: DriverTrace,
        io: InvokeEngineIo,
        ret: IngressSymbol,
    ) -> None:
        from mas.runtime.schema.ingress import EngineIoReturn

        if not isinstance(ret, EngineIoReturn):
            return
        # Do not write the chat transcript here. The engine return is not an
        # accepted fact yet; SKIP/BLOCK must not leak into the next prompt.
        # The observability end-event (ENGINE_IO_RETURN) for every op — LLM,
        # tool, and memory alike — is now recorded uniformly by
        # ObsEnvelopeMachine.step() on OBSERVABILITY_POST_EXECUTE (see
        # obs_envelope.py), driven by the kernel's own ingress processing of
        # this same return. A hand-written LLM-only call used to live here;
        # keeping it would double-record llm_call_end now that the envelope
        # machine's path actually runs for every op instead of being
        # permanently shadowed.
        tool_name: str | None = None
        tool_arguments: dict[str, Any] | None = None
        semantics: dict[str, Any] | None = None
        if io.op == "TOOL_CALL":
            tool_name = self._tool_by_correlation_id.pop(ret.correlation_id, None)
            semantics = self._semantics_by_correlation_id.pop(ret.correlation_id, None)
        elif io.op == "LLM_CALL" and ret.next_step == "TOOL_CALL" and ret.tool_name:
            tool_name = ret.tool_name
            tool_arguments = dict(ret.tool_arguments or {})
            semantics = bound_tool_semantics(self.engine, tool_name, tool_arguments, ctx=self.ctx)
        ts_mono, ts_wall = _exchange_timestamp()
        engine_raw = _engine_payload_json(ret) if self.capture_engine_io else ""
        model = engine_model_id(self.engine) or None
        if io.op == "LLM_CALL":
            self._emit_exchange(
                trace,
                ExchangeRecord(
                    kind="llm_response",
                    text=ret.text or "",
                    ts_mono=ts_mono,
                    ts_wall=ts_wall,
                    agent_id=self.agent_id,
                    correlation_id=ret.correlation_id,
                    op=io.op,
                    response_kind=ret.response_kind,
                    next_step=ret.next_step,
                    tool_name=tool_name,
                    tool_arguments=tool_arguments,
                    semantics=semantics,
                    model=model,
                    engine_raw=engine_raw,
                    cache_status=ret.cache_status,
                    cache_layer=ret.cache_layer or "",
                ),
            )
        elif io.op == "TOOL_CALL" and ret.text:
            self._emit_exchange(
                trace,
                ExchangeRecord(
                    kind="tool_result",
                    text=ret.text,
                    ts_mono=ts_mono,
                    ts_wall=ts_wall,
                    agent_id=self.agent_id,
                    correlation_id=ret.correlation_id,
                    op=io.op,
                    response_kind=ret.response_kind,
                    tool_name=tool_name,
                    semantics=semantics,
                    model=model,
                    engine_raw=engine_raw,
                ),
            )

    def _project_log_events(self, new_rows: Sequence[Any] | None) -> None:
        """Copy this step's new log rows into the chat transcript.

        ``new_rows`` is ``run.since(snapshot)``. The log keeps the whole
        session; the transcript is emptied at each turn.
        """
        ctx = self.ctx
        store = self._working_memory_store()
        if ctx is None or store is None:
            return
        extra = list(getattr(ctx, "committed_messages", None) or [])
        project_new = getattr(store, "project_new", None)
        if not callable(project_new):
            return
        from mas.runtime.boundary.context.telemetry import record_context_mutation

        for row, call_id in project_new(new_rows, extra_messages=extra, ctx=ctx):
            kind = str(getattr(row, "response_kind", "") or "")
            next_step = str(getattr(row, "next_step", "") or "")
            # Same events as before the copier: tool_call and tool result.
            # Assistant prose was never a context_mutation.
            if kind == "TOOL_RESULT":
                role, op, content = "tool", "TOOL_CALL", str(getattr(row, "text", "") or "")
            elif next_step in {"TOOL_CALL", "PARALLEL_TOOL_CALLS"}:
                role, op, content = (
                    "assistant",
                    "LLM_CALL",
                    f"tool_call:{getattr(row, 'tool_name', '') or 'tool'}",
                )
            else:
                continue
            record_context_mutation(
                getattr(ctx, "observability", None),
                action="wm_append",
                turn_index=int(getattr(ctx, "turn_index", 0) or 0),
                correlation_id=int(getattr(row, "correlation_id", 0) or 0),
                role=role,
                call_id=call_id,
                content=content,
                wm_count=len(store.messages),
                committed_count=len(extra),
                op=op,
            )

    def _record_parallel_group_obs(
        self,
        ios: list[InvokeEngineIo],
        q: Any,
        *,
        boundary: str,
        group_id: str,
    ) -> None:
        from mas.runtime.boundary.gov.telemetry import get_bound_observability

        obs = get_bound_observability() or self.observability
        record = getattr(obs, "record_parallel_group", None)
        if not callable(record):
            return
        tools: list[dict] = []
        for sym in ios:
            by_cid = q.pending_tools_by_cid.get(sym.correlation_id)
            if by_cid is None:
                continue
            name, args = by_cid
            tool_call_id = ""
            call_id_for = getattr(obs, "call_id_for", None)
            if callable(call_id_for):
                tool_call_id = str(call_id_for(sym.correlation_id, "TOOL_CALL") or "")
            tools.append(
                {
                    "correlation_id": sym.correlation_id,
                    "call_id": tool_call_id,
                    "tool_name": name,
                    "arguments": dict(args),
                }
            )
        if not tools:
            return
        record(
            boundary=boundary,
            group_id=group_id,
            tools=tools,
            correlation_id=ios[0].correlation_id if ios else 0,
        )

    @staticmethod
    def _tool_spec_for_correlation(q: Any, correlation_id: int) -> tuple[str, dict]:
        by_cid = q.pending_tools_by_cid.get(correlation_id)
        if by_cid is not None:
            return str(by_cid[0] or ""), dict(by_cid[1] or {})
        return str(q.pending_tool_name or ""), dict(q.pending_tool_args or {})

    def _record_wait_state_obs(self, sym: InvokeEngineIo, q: Any, *, boundary: str) -> None:
        """Emit WAIT/RESUME boundary events for delegation tool calls.

        Architecture: runtime emits a native ``envelope.activity`` marker at
        wait-entry (boundary=start) and wait-exit (boundary=end), and the
        native transform maps each marker into a tiny ProcessingCall span so
        the plot can render clickable WAIT/RESUME nodes without reconstructing
        timing heuristically from overlapping tool/peer calls.
        """
        if sym.op != "TOOL_CALL":
            return
        tool_name, _tool_args = self._tool_spec_for_correlation(q, sym.correlation_id)
        if not tool_name.startswith("delegate_to_"):
            return
        from mas.runtime.boundary.gov.telemetry import get_bound_observability
        from mas.runtime.schema.observability import ObsPhase

        obs = get_bound_observability() or self.observability
        record = getattr(obs, "record_envelope_activity", None)
        if not callable(record):
            return

        role = "WAIT" if boundary == "start" else "RESUME"
        note = (
            "agent wait on tool call; tool wait on delegated reply"
            if role == "WAIT"
            else "delegated reply received; resume caller"
        )
        record(
            symbol=f"WAIT_STATE_{role}",
            activity="wait_state",
            boundary=boundary,
            phase=ObsPhase.EXECUTE,
            correlation_id=sym.correlation_id,
            machine_id="M_coord",
            payload={
                "op": "TOOL_CALL",
                "tool_name": tool_name,
                "wait_link_id": f"delegate-{sym.correlation_id}",
                "wait_role": role,
                "wait_scope": "delegation",
                "wait_note": note,
            },
        )

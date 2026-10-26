#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observability operator — records boundary crossings for 3As verification."""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field

from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.egress import (
    EgressKind,
    EgressSymbol,
    EmitClientResponse,
    EmitHitlRequest,
    InvokeEngineIo,
    RaiseBoundaryError,
    RequestCtxAssembly,
)
from mas.runtime.schema.ingress import HitlResolve, IngressSymbol
from mas.runtime.schema.observability import AuditReport, ObservabilityEvent, ObsEventKind, ObsPhase

_logger = logging.getLogger(__name__)


def _machine_for_op(op: str) -> str:
    """Map a scheduled op to its owning Mealy machine id, for the live
    TransitionEvent stream's contract_id (see transition.py's
    _CONTRACT_BY_MACHINE) — the native trace-export layer keys off
    payload["op"] directly and doesn't depend on this."""
    if op == "TOOL_CALL":
        return "M_tool"
    if op == "SPAWN":
        return "M_tool"
    if op == "MEMORY_OP":
        return "M_memory"
    return "M_model"


class _CallFrames:
    """Per-asyncio-task (and per-thread) call stack + sibling-batch state.

    A plain shared stack can't distinguish true nesting from sibling
    concurrency: N calls scheduled together (parallel tool calls, or a
    moderator delegating to N agents in one turn) are opened sequentially on
    one thread before any of them execute (see ``begin_sibling_batch``).

    State lives in ``contextvars.ContextVar``, not ``threading.local``, so
    each ``asyncio.Task`` gets its own view automatically — ``create_task``
    and ``gather`` copy the current context at task-creation time, with no
    manual propagation. The vars are created per instance so two operators
    on one task still keep separate stacks, as they did per thread before.

    The stack is stored as a ``tuple`` and replaced on every mutation. A
    list would be shared by reference across the context copies that
    ``create_task`` makes, which would silently reintroduce exactly the
    cross-task bleed this class exists to prevent.
    """

    def __init__(self) -> None:
        self._stack_var: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
            "mas_obs_call_stack", default=()
        )
        self._batch_parent_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "mas_obs_batch_parent", default=None
        )
        self._batch_remaining_var: contextvars.ContextVar[int] = contextvars.ContextVar(
            "mas_obs_batch_remaining", default=0
        )

    @property
    def stack(self) -> tuple[str, ...]:
        return self._stack_var.get()

    @property
    def top(self) -> str | None:
        stack = self._stack_var.get()
        return stack[-1] if stack else None

    def push(self, call_id: str) -> None:
        self._stack_var.set((*self._stack_var.get(), call_id))

    def pop(self) -> None:
        stack = self._stack_var.get()
        if stack:
            self._stack_var.set(stack[:-1])

    def remove(self, call_id: str) -> None:
        """Drop one frame wherever it sits — siblings close out of order."""
        stack = self._stack_var.get()
        if call_id not in stack:
            return
        index = len(stack) - 1 - stack[::-1].index(call_id)
        self._stack_var.set(stack[:index] + stack[index + 1 :])

    @property
    def batch_parent(self) -> str | None:
        return self._batch_parent_var.get()

    @batch_parent.setter
    def batch_parent(self, value: str | None) -> None:
        self._batch_parent_var.set(value)

    @property
    def batch_remaining(self) -> int:
        return self._batch_remaining_var.get()

    @batch_remaining.setter
    def batch_remaining(self, value: int) -> None:
        self._batch_remaining_var.set(value)


@dataclass
class ObservabilityOperator:
    """M_obs — append-only event log with attribution metadata."""

    events: list[ObservabilityEvent] = field(default_factory=list)
    _seq: int = 0
    _agent_id: str = "agent"
    _run_id: str = ""
    # Kept in sync with KernelDriver.session_id / _current_task_id (see
    # KernelDriver._notify_governance, which updates both this operator and
    # the governance plugin from the same source on every UserInputReceived)
    # so observability logs the same session/task identifiers governance
    # sees for the same transition.
    _session_id: str = ""
    _task_id: str = ""
    _upstream_correlation_id: int | None = None
    _subscribers: list = field(default_factory=list)
    _frames: _CallFrames = field(default_factory=_CallFrames, repr=False)
    _interval_call_ids: dict[tuple[int, str], str] = field(default_factory=dict)
    _call_parents: dict[str, str | None] = field(default_factory=dict)
    # call_ids the ENGINE_IO fallback pushed itself (no contract_call wraps
    # this call, e.g. enable_envelope_observability=False) — ENGINE_IO_RETURN
    # must pop these itself since no contract_call/end is coming to do it.
    _bare_engine_calls: set[str] = field(default_factory=set, repr=False)
    _async_plugins: bool = False
    _plugin_queue: queue.Queue | None = field(default=None, repr=False)
    _plugin_worker: threading.Thread | None = field(default=None, repr=False)

    def subscribe(self, plugin: object) -> None:
        """Register read-mode ObservabilityPlugin (or compatible listener)."""
        if plugin not in self._subscribers:
            self._subscribers.append(plugin)

    def unsubscribe(self, plugin: object) -> None:
        """Drop a previously subscribed plugin. No-op if it is not registered."""
        if plugin in self._subscribers:
            self._subscribers.remove(plugin)

    def set_context(
        self,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        upstream_correlation_id: int | None = None,
    ) -> None:
        if agent_id is not None:
            self._agent_id = agent_id
        if run_id is not None:
            self._run_id = run_id
        if session_id is not None:
            self._session_id = session_id
        if task_id is not None:
            self._task_id = task_id
        self._upstream_correlation_id = upstream_correlation_id

    def push_call_frame(self, call_id: str) -> None:
        """Push an open execution frame (e.g. agent turn) onto the CURRENT task's stack."""
        if call_id:
            self._frames.push(call_id)

    def pop_call_frame(self, call_id: str | None = None) -> None:
        """Pop the innermost frame on the current task, or the named frame if provided."""
        stack = self._frames.stack
        if not stack:
            return
        if call_id is None or stack[-1] == call_id:
            self._frames.pop()
            return
        self._frames.remove(call_id)

    def begin_sibling_batch(self, count: int) -> None:
        """Mark the next ``count`` calls opened on this thread as true siblings.

        Call this right before scheduling N calls together (parallel tool
        calls, or a moderator delegating to N agents in one turn) — before
        any of their own contract_call/start or bare-ENGINE_IO events fire.
        Without it, each sibling's own open reads "whatever's currently on
        top of the stack" as its parent, which — since all N opens happen
        sequentially on the same thread before any of them execute — is the
        PREVIOUS sibling, not the enclosing frame; siblings end up chained to
        each other. This snapshots the enclosing frame once and hands it to
        the next ``count`` opens, regardless of what they push in between.
        """
        if count <= 1:
            return
        frames = self._frames
        frames.batch_parent = frames.top
        frames.batch_remaining = count

    def _current_frame(self) -> str | None:
        return self._frames.top

    def call_id_for(self, correlation_id: int, op: str) -> str:
        """Public accessor for the stable call_id assigned to a (correlation_id, op) pair.

        Used by delegation call sites (e.g. ``make_workflow_send``, via
        ``LlmDelegator``) to look up the delegating TOOL_CALL's own call_id —
        on the DELEGATING agent's own operator, before switching to the
        delegate's — so it can be passed through as the delegate's
        ``execution_start.parent_call_id``. Without this, every agent's own
        execution_start defaults to the top-level ``mas_call_id`` (see
        ``_sh_user_input``), so the call tree never learns which tool call
        actually spawned which delegate.

        Deliberately keyed by (correlation_id, op) rather than "whatever's on
        top of the call stack right now": when an assistant message contains
        several ``delegate_to_*`` tool calls at once, every one of their
        CONTRACT_START steps fires — pushing all N frames — before any of
        them actually *executes* (dispatched one at a time from the tool
        loop). Reading the stack top at execution time would return
        whichever sibling was pushed last for every one of them; this
        resolves the specific sibling by its own correlation_id instead.
        """
        return self._interval_call_id(correlation_id, op)

    def parent_call_id_for(self, call_id: str) -> str | None:
        """Public accessor for call_id's already-resolved parent, if any.

        Used by the driver (see driver.py's dispatch loop, right after
        ``call_id_for``) to stamp ``InvokeEngineIo.parent_call_id`` on the
        outgoing invocation itself \u2014 by the time ``call_id_for`` has run,
        this call's own contract_call/start has already opened its frame via
        ``_open_call``, which resolves the correct parent even for a batch of
        true siblings (see ``begin_sibling_batch``). Putting this on the wire
        means the engine, a delegate, or an external observability plugin can
        read a self-describing parent-span id directly, instead of every
        consumer needing its own copy of this stack-reconstruction logic.
        """
        return self._call_parents.get(call_id)

    def _open_call(self, call_id: str) -> str | None:
        """Register call_id's parent and make it the active frame for its own descendants."""
        frames = self._frames
        if frames.batch_remaining > 0:
            parent = frames.batch_parent
            frames.batch_remaining -= 1
        else:
            parent = frames.top
        self._call_parents[call_id] = parent
        frames.push(call_id)
        return parent

    def _close_call(self, call_id: str) -> None:
        """Remove call_id from the current thread's stack, wherever it sits.

        Siblings opened in the same batch don't necessarily close in the
        order they opened (whichever finishes first ends first) — an
        LIFO-only pop would leave earlier siblings stuck on the stack
        forever once a later one closes out of order.
        """
        stack = self._frames.stack
        if not stack:
            return
        if stack[-1] == call_id:
            self._frames.pop()
        else:
            self._frames.remove(call_id)

    def record_kernel_snapshot(self, q: QProduct, *, label: str = "snapshot") -> None:
        self._emit(
            ObsEventKind.BOUNDARY_EGRESS,
            ObsPhase.REQUEST,
            "M_dp",
            payload={
                "label": label,
                "dp": q.dp.value,
                "ctrl": q.ctrl.value,
                "scheduled_egress": q.scheduled_egress,
            },
        )

    def record_ingress(self, event: IngressSymbol, q: QProduct) -> None:
        cid = getattr(event, "correlation_id", 0)
        kind = ObsEventKind.BOUNDARY_INGRESS
        phase = ObsPhase.RESULT
        machine = "execution_engine"
        attribution = ""
        if isinstance(event, HitlResolve):
            kind = ObsEventKind.HITL_RESOLVE
            phase = ObsPhase.AUTHZ
            machine = "M_gov"
            attribution = event.resolution.value
            cid = event.request_id
        self._emit(
            kind,
            phase,
            machine,
            correlation_id=cid,
            actor_id=event.operator_id if isinstance(event, HitlResolve) else "engine",
            attribution_code=attribution,
            payload={
                "ingress_kind": event.kind.value,
                "product": {
                    "dp": q.dp.value,
                    "ctx": q.ctx.value,
                    "model": q.model.value,
                    "tool": q.tool.value,
                    "gov": q.gov_state,
                    "scheduled_egress": q.scheduled_egress,
                },
                **(
                    {
                        "resolution": event.resolution.value,
                        "answer": event.answer,
                        "operator_id": event.operator_id,
                        "steering": event.steering_text,
                    }
                    if isinstance(event, HitlResolve)
                    else {}
                ),
            },
        )

    def record_egress(self, sym: EgressSymbol, q: QProduct) -> None:
        if sym.kind == EgressKind.INVOKE_ENGINE_IO:
            assert isinstance(sym, InvokeEngineIo)
            self._emit(
                ObsEventKind.ENGINE_IO,
                ObsPhase.EXECUTE,
                _machine_for_op(sym.op),
                correlation_id=sym.correlation_id,
                payload={"op": sym.op, "destructive": sym.destructive},
            )
            return
        if sym.kind == EgressKind.EMIT_HITL_REQUEST:
            assert isinstance(sym, EmitHitlRequest)
            _hitl_policy_name = sym.context_data.get("policy_name", "scheduler")
            self._emit(
                ObsEventKind.HITL_REQUEST,
                ObsPhase.AUTHZ,
                "M_gov",
                correlation_id=sym.request_id,
                policy_name=_hitl_policy_name,
                payload={
                    "question": sym.question,
                    "pending_schedule": sym.pending_schedule,
                    "offered_actions": [a.value for a in sym.offered_actions],
                    # Also carried in payload (not just the top-level
                    # ObservabilityEvent field) — the live TransitionEvent
                    # pipeline (boundary_dict_from_transition) only forwards
                    # payload/attributes. See record_governance_decision for
                    # the same pattern.
                    "policy_name": _hitl_policy_name,
                },
            )
            return
        if sym.kind == EgressKind.REQUEST_CTX_ASSEMBLY:
            assert isinstance(sym, RequestCtxAssembly)
            self._emit(
                ObsEventKind.CONTEXT_STEER,
                ObsPhase.INGRESS,
                "M_ctx",
                payload={
                    "collect_id": sym.collect_id,
                    "operator_context": bool(sym.operator_context),
                    "product": {
                        "dp": q.dp.value,
                        "ctx": q.ctx.value,
                    },
                },
            )
            return
        if sym.kind == EgressKind.EMIT_CLIENT_RESPONSE:
            assert isinstance(sym, EmitClientResponse)
            self._emit(
                ObsEventKind.CLIENT_RESPONSE,
                ObsPhase.END,
                "M_dp",
                payload={"finish_reason": sym.finish_reason},
            )
            return
        if sym.kind == EgressKind.RAISE_BOUNDARY_ERROR:
            assert isinstance(sym, RaiseBoundaryError)
            self._emit(
                ObsEventKind.BOUNDARY_ERROR,
                ObsPhase.AUTHZ,
                "M_gov",
                attribution_code=sym.code,
                payload={
                    "code": sym.code,
                    "recoverable": sym.recoverable,
                    "message": sym.message,
                    "parent_call_id": self._current_frame(),
                },
            )
            return
        self._emit(
            ObsEventKind.BOUNDARY_EGRESS,
            ObsPhase.EGRESS,
            "kernel",
            payload={"egress_kind": sym.kind.value},
        )

    def audit(self) -> AuditReport:
        cids = sorted({e.correlation_id for e in self.events if e.correlation_id > 0})
        machines = sorted({e.machine_id for e in self.events})
        phases = {e.phase for e in self.events}
        required = {
            ObsPhase.REQUEST,
            ObsPhase.AUTHZ,
            ObsPhase.EXECUTE,
            ObsPhase.RESULT,
            ObsPhase.END,
        }
        gaps: list[str] = []
        missing = required - phases
        if missing:
            gaps.append(f"missing phases: {', '.join(sorted(p.value for p in missing))}")
        hitl = any(e.kind == ObsEventKind.HITL_REQUEST for e in self.events)
        resolve = any(e.kind == ObsEventKind.HITL_RESOLVE for e in self.events)
        if hitl and not resolve:
            gaps.append("HITL request without resolve — incomplete accountability chain")
        if not cids:
            gaps.append("no correlation ids — weak attribution")
        return AuditReport(
            event_count=len(self.events),
            correlation_ids=cids,
            machines_touched=machines,
            has_full_envelope=not missing,
            auditability=len(self.events) > 0 and len(cids) > 0,
            accountability=hitl == resolve or not hitl,
            attribution=all(e.machine_id for e in self.events),
            gaps=gaps,
        )

    def record_context_mutation(
        self,
        *,
        action: str,
        turn_index: int = 0,
        correlation_id: int = 0,
        role: str = "",
        call_id: str = "",
        content_preview: str = "",
        committed_count: int = 0,
        wm_count: int = 0,
        op: str = "",
    ) -> ObservabilityEvent:
        return self._emit(
            ObsEventKind.CONTEXT_MUTATION,
            ObsPhase.INGRESS,
            "M_ctx",
            correlation_id=correlation_id,
            payload={
                "action": action,
                "turn_index": turn_index,
                "role": role,
                "call_id": call_id,
                "content_preview": content_preview,
                "committed_count": committed_count,
                "wm_count": wm_count,
                "op": op,
            },
        )

    def record_context_assembled(
        self,
        *,
        correlation_id: int,
        turn_index: int = 0,
        agent_id: str = "agent",
        messages: list | None = None,
        segments: list | None = None,
        total_tokens: int = 0,
        tools: list | None = None,
        context_usage: dict | None = None,
    ) -> ObservabilityEvent:
        payload: dict = {
            "agent_id": agent_id,
            "turn_index": turn_index,
            "messages": list(messages or []),
            "segments": list(segments or []),
            "total_tokens": total_tokens,
            "message_count": len(messages or []),
        }
        if tools is not None:
            payload["tools"] = [str(name) for name in tools if str(name)]
        if context_usage is not None:
            payload["context_usage"] = dict(context_usage)
        return self._emit(
            ObsEventKind.CONTEXT_ASSEMBLED,
            ObsPhase.EXECUTE,
            "M_ctx",
            correlation_id=correlation_id,
            payload={
                **payload,
                # Context is only ever assembled for one specific LLM_CALL
                # dispatch — never a guess, always this op. Lets
                # _resolve_transition_ids resolve the SAME call_id that
                # dispatch's own llm_call_start/end use, via
                # _interval_call_id(correlation_id, "LLM_CALL").
                "op": "LLM_CALL",
            },
        )

    def record_cache_lookup(
        self,
        *,
        correlation_id: int,
        cache_layer: str,
        cache_status: str,
    ) -> ObservabilityEvent:
        """Record a cache probe that has no engine return (for example a strict miss)."""
        return self._emit(
            ObsEventKind.CACHE_LOOKUP,
            ObsPhase.RESULT,
            "M_model",
            correlation_id=correlation_id,
            payload={
                "op": "LLM_CALL",
                "cache_layer": cache_layer,
                "cache_status": cache_status,
            },
        )

    def record_envelope_activity(
        self,
        *,
        symbol: str,
        activity: str,
        boundary: str,
        phase: ObsPhase,
        correlation_id: int = 0,
        machine_id: str = "M_obs",
        payload: dict | None = None,
    ) -> ObservabilityEvent:
        return self._emit(
            ObsEventKind.ENVELOPE_ACTIVITY,
            phase,
            machine_id,
            correlation_id=correlation_id,
            payload={
                "symbol": symbol,
                "activity": activity,
                "boundary": boundary,
                **(payload or {}),
            },
        )

    def record_engine_io(
        self,
        *,
        correlation_id: int,
        op: str,
        destructive: bool = False,
        tool_name: str = "",
        tool_arguments: dict | None = None,
        category: str = "",
        **extra: object,
    ) -> ObservabilityEvent:
        machine = _machine_for_op(op)
        resolved_tool = str(tool_name or "").strip()
        if not resolved_tool and op == "TOOL_CALL":
            resolved_tool = "tool"
        payload: dict = {
            "op": op,
            "destructive": destructive,
            "tool_name": resolved_tool,
            "envelope": True,
        }
        if category:
            payload["category"] = category
        if tool_arguments:
            payload["tool_arguments"] = dict(tool_arguments)
        payload.update({k: v for k, v in extra.items() if v is not None})
        return self._emit(
            ObsEventKind.ENGINE_IO,
            ObsPhase.EXECUTE,
            machine,
            correlation_id=correlation_id,
            payload=payload,
        )

    def record_engine_io_return(
        self,
        *,
        correlation_id: int,
        op: str,
        text: str = "",
        next_step: str = "STOP",
        response_kind: str = "",
        tool_name: str = "",
        usage: dict | None = None,
        finish_reason: str = "",
        tools: list | None = None,
        model: str = "",
        category: str = "",
        **extra: object,
    ) -> ObservabilityEvent:
        machine = _machine_for_op(op)
        resolved_tool = str(tool_name or "").strip()
        if not resolved_tool and op == "TOOL_CALL":
            resolved_tool = "tool"
        payload: dict = {
            "op": op,
            "text": text,
            "next_step": next_step,
            "response_kind": response_kind,
            "tool_name": resolved_tool,
            "envelope": True,
        }
        if category:
            payload["category"] = category
        if usage:
            payload["usage"] = dict(usage)
        if finish_reason:
            payload["finish_reason"] = finish_reason
        if model:
            payload["model"] = model
        if tools is not None:
            payload["tools"] = [str(name) for name in tools if str(name)]
        payload.update({k: v for k, v in extra.items() if v is not None})
        return self._emit(
            ObsEventKind.ENGINE_IO_RETURN,
            ObsPhase.RESULT,
            machine,
            correlation_id=correlation_id,
            payload=payload,
        )

    def record_engine_llm_return(
        self,
        *,
        correlation_id: int,
        text: str = "",
        next_step: str = "STOP",
    ) -> ObservabilityEvent:
        return self.record_engine_io_return(
            correlation_id=correlation_id,
            op="LLM_CALL",
            text=text,
            next_step=next_step,
            response_kind="MODEL_TEXT",
        )

    def record_governance_decision(
        self,
        *,
        hook: str,
        phase: str,
        decision: str = "",
        reason: str = "",
        correlation_id: int = 0,
        policy_name: str = "",
        obs_phase: ObsPhase = ObsPhase.AUTHZ,
        op: str = "",
        failure_class: str = "",
        failure_code: str = "",
        retry_attempts: int = 0,
    ) -> ObservabilityEvent:
        """Emit a ``governance_decision`` event for one egress or ingress
        check (``hook``) at one lifecycle point (``phase``) on a call,
        identified by ``correlation_id``. Consumed by the multilevel
        trajectory plot's Governance lane and per-call badges.

        ``op`` (e.g. "TOOL_CALL"/"LLM_CALL"/"MEMORY_OP") lets
        ``_resolve_transition_ids`` resolve this event's call_id/parent via
        the same ``_interval_call_id``/``_call_parents`` mechanism the
        associated ENGINE_IO/ENGINE_IO_RETURN pair uses, instead of the
        generic ``f"call-{correlation_id}"`` fallback (non-unique across
        agents, no real parent)."""
        return self._emit(
            ObsEventKind.GOVERNANCE_DECISION,
            obs_phase,
            "M_gov",
            correlation_id=correlation_id,
            policy_name=policy_name or "kernel",
            payload={
                "hook": hook,
                "checkpoint": phase,
                "decision": decision,
                "reason": reason,
                "op": op,
                # Also carried in payload (not just the top-level ObservabilityEvent
                # field) because the live TransitionEvent pipeline
                # (boundary_dict_from_transition) only forwards payload/attributes.
                "policy_name": policy_name or "kernel",
                **{
                    key: value
                    for key, value in {
                        "failure_class": failure_class,
                        "failure_code": failure_code,
                        "retry_attempts": retry_attempts,
                    }.items()
                    if value not in (None, "", 0)
                },
            },
        )

    def record_session(self, session_kind: str, **fields: object) -> None:
        """Session-level transition (mas_call, execution, …) → export plugins."""
        from mas.runtime.boundary.obs.transition import TransitionEvent

        session_id = str(fields.pop("session_id", "") or self._session_id)
        self._dispatch_transition(
            TransitionEvent(
                contract_id="orchestrator" if session_kind != "control" else "control",
                mealy_symbol=session_kind,
                phase="event",
                agent_id=self._agent_id,
                run_id=self._run_id,
                session_id=session_id,
                attributes={k: v for k, v in fields.items()},
                boundary_kind="session",
            )
        )

    def record_control(
        self,
        *,
        method: str,
        category: str = "",
        control_kind: str = "",
        actor: str = "",
        surface: str = "",
        denied: bool = False,
        reason: str = "",
        session_id: str = "",
        **payload: object,
    ) -> ObservabilityEvent:
        """Every ControlContract verb reaches M_obs; export sinks may filter by category."""
        body = {
            "method": method,
            "category": category or f"control.{method}",
            "control_kind": control_kind or method,
            "actor": actor,
            "surface": surface,
            "denied": denied,
            "reason": reason,
            "session_id": session_id or self._session_id,
            **payload,
        }
        return self._emit(
            ObsEventKind.CONTROL,
            ObsPhase.REQUEST,
            "M_control",
            actor_id=str(actor or "operator"),
            payload=body,
        )

    def record_parallel_group(
        self,
        *,
        boundary: str,
        group_id: str,
        tools: list[dict],
        correlation_id: int = 0,
    ) -> ObservabilityEvent:
        """Parallel tool fork/join — T-09 trajectory boundary."""
        return self._emit(
            ObsEventKind.ENVELOPE_ACTIVITY,
            ObsPhase.EXECUTE,
            "M_obs",
            correlation_id=correlation_id,
            payload={
                "activity": "parallel_group",
                "boundary": boundary,
                "group_id": group_id,
                "tools": list(tools),
                "tool_count": len(tools),
            },
        )

    def enable_async_plugins(self) -> None:
        """Dispatch export plugins on a background worker (core never waits)."""
        self._async_plugins = True

    def _ensure_plugin_worker(self) -> None:
        if self._plugin_queue is None:
            self._plugin_queue = queue.Queue()
        if self._plugin_worker is not None and self._plugin_worker.is_alive():
            return
        self._plugin_worker = threading.Thread(
            target=self._plugin_worker_loop,
            name="obs-plugin-worker",
            daemon=True,
        )
        self._plugin_worker.start()

    def _plugin_worker_loop(self) -> None:
        assert self._plugin_queue is not None
        while True:
            transition = self._plugin_queue.get()
            try:
                if transition is None:
                    return
                self._invoke_plugins(transition)
            finally:
                self._plugin_queue.task_done()

    def _invoke_plugins(self, transition) -> None:
        for plugin in self._subscribers:
            try:
                on_transition = getattr(plugin, "on_transition", None)
                if callable(on_transition):
                    on_transition(transition)
            except Exception:
                _logger.debug("observability plugin failed", exc_info=True)

    def _dispatch_transition(self, transition) -> None:
        try:
            from mas.runtime.boundary.obs.event_stream import publish_event

            payload = transition.to_dict() if hasattr(transition, "to_dict") else {}
            payload.setdefault("source", "transition")
            publish_event(payload)
        except Exception:
            _logger.debug("event stream publish failed", exc_info=True)
        if not self._subscribers:
            return
        if self._async_plugins:
            self._ensure_plugin_worker()
            assert self._plugin_queue is not None
            self._plugin_queue.put(transition)
            return
        self._invoke_plugins(transition)

    def drain_plugin_queue(self, *, timeout: float = 30.0) -> None:
        """Block until async plugin dispatch queue is empty."""
        if not self._async_plugins or self._plugin_queue is None:
            return
        self._plugin_queue.join()

    def shutdown_plugin_worker(self) -> None:
        """Stop background plugin worker (pipeline close)."""
        if self._plugin_queue is None:
            return
        self.drain_plugin_queue()
        self._plugin_queue.put(None)
        if self._plugin_worker is not None:
            self._plugin_worker.join(timeout=5.0)
            self._plugin_worker = None

    def _emit(
        self,
        kind: ObsEventKind,
        phase: ObsPhase,
        machine_id: str,
        *,
        correlation_id: int = 0,
        policy_name: str = "",
        actor_id: str = "kernel",
        attribution_code: str = "",
        payload: dict | None = None,
    ) -> ObservabilityEvent:
        self._seq += 1
        ev = ObservabilityEvent(
            seq=self._seq,
            kind=kind,
            phase=phase,
            machine_id=machine_id,
            correlation_id=correlation_id,
            policy_name=policy_name,
            actor_id=actor_id,
            attribution_code=attribution_code,
            payload=payload or {},
        )
        self.events.append(ev)
        self._notify_subscribers(ev)
        return ev

    def _interval_call_id(self, correlation_id: int, op: str) -> str:
        key = (correlation_id, op)
        if key not in self._interval_call_ids:
            self._interval_call_ids[key] = str(uuid.uuid4())
        return self._interval_call_ids[key]

    def _interval_call_and_parent(self, correlation_id: int, key: str) -> tuple[str, str | None]:
        """call_id for (correlation_id, key) + its parent, falling back to the
        current frame when this call hasn't opened its own frame yet (several
        egress-side checks — governance decisions, context assembly, HITL
        requests — fire before CONTRACT_START opens the call's own frame; the
        enclosing execution is the correct parent in that case, and nothing
        else pushes a frame in between). Shared by every _resolve_transition_ids
        branch that reduces to "this call's own id, parented to whatever's
        already resolved for it or else the current call in progress."
        """
        call_id = self._interval_call_id(correlation_id, key)
        return call_id, self._call_parents.get(call_id, self._current_frame())

    def _resolve_transition_ids(self, ev: ObservabilityEvent) -> tuple[str | None, str | None]:
        """Return (call_id, parent_call_id) using a per-thread call stack.

        The stack push/pop is owned exclusively by the contract_call envelope
        activity (CONTRACT_START/CONTRACT_END) — the one signal guaranteed to
        fire exactly once per call, even for a call a governance decision blocks
        before execution.  ENGINE_IO / ENGINE_IO_RETURN (fired separately, from
        CONTRACT_EXECUTE / OBSERVABILITY_POST_EXECUTE for the SAME call) reuse
        the same (correlation_id, op)-keyed call_id and look up its
        already-established parent instead of pushing/popping a second frame.

        Calls scheduled together as true siblings — parallel tool calls, or a
        moderator delegating to N agents in one turn — all fire their own
        contract_call/start SEQUENTIALLY on the scheduling thread, before any
        of them execute (``schedule_parallel_tools_egress`` loops over the
        whole batch synchronously). Reading "whatever's on top of the stack"
        for parent would chain each subsequent sibling onto the previous one
        instead of the enclosing frame. ``begin_sibling_batch`` (called by the
        kernel right before that loop, once it knows the batch has 2+ members)
        snapshots the enclosing frame ONCE and hands it to the next N opens
        via ``_open_call``, regardless of what those N opens push in between.
        """
        payload = ev.payload or {}
        op = str(payload.get("op") or "")
        cid = ev.correlation_id
        parent: str | None = None

        if ev.kind == ObsEventKind.ENGINE_IO and op:
            call_id = self._interval_call_id(cid, op)
            if call_id in self._call_parents:
                return call_id, self._call_parents[call_id]
            # No contract_call/start established this call (a bare engine call
            # not wrapped by the envelope) — establish it here as a fallback.
            # Remember it as bare so ENGINE_IO_RETURN below closes it itself;
            # no contract_call/end is coming to pop it otherwise.
            parent = self._open_call(call_id)
            self._bare_engine_calls.add(call_id)
            return call_id, parent

        if ev.kind == ObsEventKind.ENGINE_IO_RETURN and op:
            call_id = self._interval_call_ids.get((cid, op), f"call-{cid}" if cid else None)
            parent = self._call_parents.get(call_id) if call_id else None
            if call_id and call_id in self._bare_engine_calls:
                self._bare_engine_calls.discard(call_id)
                self._close_call(call_id)
            return call_id, parent

        if ev.kind == ObsEventKind.GOVERNANCE_DECISION and op:
            # Reuse the SAME (correlation_id, op)-keyed call_id the call's own
            # ENGINE_IO/ENGINE_IO_RETURN pair uses, instead of a synthetic
            # f"call-{cid}" that collides across agents (correlation_id resets
            # per-agent). Egress decisions fire before CONTRACT_START opens
            # this call's own frame — _call_parents has nothing for it yet,
            # so fall back to _current_frame(), the exact value _open_call
            # would assign moments later (nothing else pushes a frame in
            # between). Ingress decisions fire after CONTRACT_END, by which
            # point _call_parents[call_id] is already set and never deleted.
            return self._interval_call_and_parent(cid, op)

        if ev.kind == ObsEventKind.CONTEXT_ASSEMBLED and op:
            # Same (correlation_id, "LLM_CALL") cache key that call's own
            # llm_call_start/end use — context_assembled always fires
            # synchronously inside that exact LLM_CALL dispatch (see
            # assemble_llm_messages/llm_live.py's _assembly_correlation_id),
            # so this is never a guess. Egress-side CONTRACT_START may not
            # have opened this call's frame yet when assembly runs (context is
            # assembled before the LLM_CALL is dispatched) — fall back to
            # _current_frame(), the enclosing execution, same reasoning as
            # governance_decision's egress case.
            return self._interval_call_and_parent(cid, op)

        if ev.kind == ObsEventKind.CONTEXT_MUTATION and op:
            # Working-memory mutations caused by one specific engine-op result
            # (see driver.py's wm_append call sites) are a CHILD of that call
            # (its result being committed to working memory), so the call's
            # own resolved id — not that call's parent — is the mutation's
            # real parent. Same (correlation_id, op)-keyed cache
            # governance_decision uses, so this is always the SAME call_id
            # that call's own ENGINE_IO/ENGINE_IO_RETURN pair used.
            # Turn/session-scoped mutations (turn_start, wm_clear, ...) pass no
            # op and correctly fall through to the generic fallback below,
            # which resolves no parent for them — accurate, not a regression.
            # Own call_id is a distinct key (never reused by any other kind of
            # event) so a live consumer never sees two different spans sharing
            # one id; the native JSONL keeps its own state_update_start/end
            # id scheme regardless (see _boundary_context_mutation) and only
            # reads the parent this returns.
            owning_call_id = self._interval_call_id(cid, op)
            mutation_call_id = self._interval_call_id(cid, f"{op}:mutation")
            return mutation_call_id, owning_call_id

        if ev.kind in (ObsEventKind.HITL_REQUEST, ObsEventKind.HITL_RESOLVE):
            # request_id (carried as this event's correlation_id) is just
            # another RunLedger counter, not a UUID — it collides across
            # agents the same way correlation_id does. Mint a real,
            # globally-unique id via _interval_call_id (same uuid4-backed
            # cache used everywhere else); keying on (cid, "HITL") means the
            # request and its resolve share one call_id, since both carry the
            # same request_id. Parent is whatever frame is active when the
            # gate fires — the enclosing call/execution the human was asked
            # about.
            return self._interval_call_and_parent(cid, "HITL")

        if ev.kind == ObsEventKind.ENVELOPE_ACTIVITY:
            activity = str(payload.get("activity") or "")
            boundary = str(payload.get("boundary") or "")
            if activity == "parallel_group" and boundary == "start":
                # Secondary safety net for configurations where contract_call
                # never fires (enable_envelope_observability=False) — the
                # primary defense is begin_sibling_batch, called by the kernel
                # before contract_call/start ever runs for this batch.
                group_parent = self._current_frame()
                for tool in payload.get("tools") or []:
                    t_cid = tool.get("correlation_id")
                    if t_cid is None:
                        continue
                    t_call_id = self._interval_call_id(int(t_cid), "TOOL_CALL")
                    self._call_parents.setdefault(t_call_id, group_parent)
            if activity == "contract_call" and boundary == "start" and op:
                call_id = self._interval_call_id(cid, op)
                if call_id in self._call_parents:
                    parent = self._call_parents[call_id]
                    self._frames.push(call_id)
                else:
                    parent = self._open_call(call_id)
                return call_id, parent
            if activity == "contract_call" and boundary == "end" and op:
                call_id = self._interval_call_ids.get((cid, op))
                parent = self._call_parents.get(call_id) if call_id else None
                if call_id:
                    self._close_call(call_id)
                return call_id, parent

        if cid and op:
            if op == "TOOL_CALL":
                prefix = "tool"
            elif op == "MEMORY_OP":
                prefix = "memory"
            else:
                prefix = "llm"
            return f"{prefix}-{cid}", parent
        if cid:
            return f"call-{cid}", parent
        return None, parent

    def _notify_subscribers(self, ev: ObservabilityEvent) -> None:
        if not self._subscribers:
            return
        from mas.runtime.boundary.obs.transition import boundary_event_to_transition

        call_id, parent_call_id = self._resolve_transition_ids(ev)
        self._dispatch_transition(
            boundary_event_to_transition(
                ev,
                agent_id=self._agent_id,
                run_id=self._run_id,
                session_id=self._session_id,
                task_id=self._task_id,
                call_id=call_id,
                parent_call_id=parent_call_id,
                upstream_correlation_id=self._upstream_correlation_id,
            )
        )

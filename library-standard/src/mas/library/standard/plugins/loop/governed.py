#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance and observability at the boundary of an agent loop.

A loop that is not the kernel loop has no per-tool control: what it does inside is its own. What the
host can still do is judge the call as a whole, as it judges a tool call. This wrapper does that with
the chokepoints that exist, and adds nothing to the kernel:

* egress: an ``EgressIntentView`` for a ``TOOL_CALL`` named ``loop:<id>`` whose arguments carry the task,
  evaluated by ``evaluate_egress_at_chokepoint`` against the agent's own governance (its Controls);
* ingress: ``evaluate_ingress_chain`` with a ``TOOL_RESULT`` after the loop replied. An ingress plugin
  sees the kind of the response, not its text, so a reply cannot be inspected here;
* events: the ``user_input`` and ``agent_response`` session events a kernel turn records, the
  ``governance_decision`` events the kernel records, and ``loop_call`` / ``loop_event`` session events.
  The session events carry a ``loop`` field.

Only ALLOW and LOG pass. A boundary has no operator to ask and no model to retry, so HITL, RETRY and
MODIFY refuse the call, like BLOCK, SKIP, TERMINATE and BLACKLIST do. A governance plugin that raises
refuses it too.
"""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict

from mas.runtime.boundary.gov.ingress_chain import evaluate_ingress_chain
from mas.runtime.boundary.gov.ingress_plugin import IngressIntentView
from mas.runtime.boundary.gov.plugin import evaluate_egress_at_chokepoint
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.boundary.loop import AgentLoop, LoopDenied, LoopError, LoopReply, LoopTask
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.governance import GovernanceAction
from mas.runtime.schema.observability import ObsPhase

_MAX_TRACKED_TASKS = 4096  # repeated task ids are told apart only within this many recent tasks
_PASS = {GovernanceAction.ALLOW.value, GovernanceAction.LOG.value}


class GovernedLoop:
    """Wrap ``inner`` so every task is judged by ``config``'s governance and recorded by ``observer``."""

    def __init__(
        self,
        inner: AgentLoop,
        *,
        config: KernelConfig,
        observer: ObservabilityOperator | None = None,
        agent_id: str = "agent",
    ) -> None:
        self._inner = inner
        self._agent_id = agent_id
        self._uses: OrderedDict[str, int] = OrderedDict()
        self._config = config
        self._observer = observer
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.loop_id = inner.loop_id

    @property
    def tool_name(self) -> str:
        """The name governance sees for this loop, as for a tool: ``loop:<id>``."""
        return f"loop:{self._inner.loop_id}"

    def close(self) -> None:
        self._inner.close()

    def run(self, task: LoopTask) -> LoopReply:
        with self._lock:
            cid = next(self._ids)
            base = f"{self._agent_id}-{task.task_id or f'u{cid}'}"
            self._uses[base] = self._uses.get(base, 0) + 1
            self._uses.move_to_end(base)
            while len(self._uses) > _MAX_TRACKED_TASKS:
                self._uses.popitem(last=False)
            exec_id = f"{base}-exec" if self._uses[base] == 1 else f"{base}-{self._uses[base]}-exec"
        if self._observer is not None:
            self._observer.push_call_frame(exec_id)
            fields: dict[str, object] = {"text": task.text, "call_id": exec_id, "turn_id": task.task_id}
            if task.parent_call_id:
                fields["parent_call_id"] = task.parent_call_id
            self._session("user_input", task, **fields)
        try:
            reply = self._judged(task, cid)
            if reply.text:
                self._session("agent_response", task, text=reply.text, finish_reason="stop")
            return reply
        finally:
            if self._observer is not None:
                self._observer.pop_call_frame(exec_id)

    def _judged(self, task: LoopTask, cid: int) -> LoopReply:
        name = self.tool_name
        self._session("loop_call", task, phase="start", correlation_id=cid)
        view = EgressIntentView(
            op="TOOL_CALL",
            destructive=self._config.gov_trigger_destructive,
            correlation_id=cid,
            tool_name=name,
            tool_arguments={
                "task": task.text,
                "session_id": task.session_id,
                "task_id": task.task_id,
                "caller": task.caller,
            },
            offered_tools=(name,),
        )
        try:
            decision, policy, reason = evaluate_egress_at_chokepoint(view, config=self._config)
            verdict = decision.value
        except Exception as exc:  # noqa: BLE001 - a governance plugin that fails refuses the call
            verdict, policy, reason = "BLOCK", "governance-error", f"{type(exc).__name__}: {exc}"
        self._decision("egress", verdict, reason, cid, policy, ObsPhase.AUTHZ)
        if verdict not in _PASS:
            self._session("loop_call", task, phase="denied", correlation_id=cid, decision=verdict, policy=policy)
            raise LoopDenied(verdict, policy, reason)
        try:
            reply = self._inner.run(task)
        except LoopError as exc:
            self._session("loop_call", task, phase="error", correlation_id=cid, error=str(exc))
            raise
        for event in reply.events:
            self._session("loop_event", task, correlation_id=cid, event=dict(event))
        ingress = evaluate_ingress_chain(
            IngressIntentView(response_kind="TOOL_RESULT", profile=self._config.gov_ingress_profile),
            config=self._config,
        )
        action = ingress.action.value
        self._decision("ingress", action, ingress.message, cid, "ingress", ObsPhase.VALID)
        if action not in _PASS:
            self._session("loop_call", task, phase="denied", correlation_id=cid, decision=action, policy="ingress")
            raise LoopDenied(action, "ingress", ingress.message)
        self._session("loop_call", task, phase="end", correlation_id=cid)
        return reply

    def _decision(self, hook: str, decision: str, reason: str, cid: int, policy: str, obs_phase: ObsPhase) -> None:
        if self._observer is None:
            return
        self._observer.record_governance_decision(
            hook=hook,
            phase="after",
            decision=decision,
            reason=reason,
            correlation_id=cid,
            policy_name=policy,
            obs_phase=obs_phase,
            op="TOOL_CALL",
        )

    def _session(self, kind: str, task: LoopTask, **fields: object) -> None:
        if self._observer is None:
            return
        self._observer.record_session(
            kind, loop=self._inner.loop_id, session_id=task.session_id, task_id=task.task_id, **fields
        )

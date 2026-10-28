#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process agent_comm plugin — implementations live here, not in runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.headers import _bound_validated, merge_headers
from mas.library.standard.lib.comm.payload import (
    INFORM_ASK_INTENTS,
    MAX_MESSAGE_SIZE,
    STEER_INTENT,
    bound_inbound,
    check_message_size,
    dispatch_steer,
    inbound_payload,
    require_honoured_intent,
    send_result,
    steer_peer_sessions,
)
from mas.library.standard.lib.comm.send_context import align_send
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, SendContext
from mas.runtime.boundary.control.contract import ControlContract

RunTurnFn = Callable[[str, str, int, str, str], str]


class LocalAgentComm(AgentCommContract):
    """Agent communication over the current in-process workflow bus.

    The turn runs in this process, so ``headers`` are the ambient headers of
    the target turn. Only hop-safe keys ride a later send from that turn.
    ``inform`` and ``ask`` still run that turn and wait for it. The peer
    sees a text label as a hint; sender and intent are also on
    ``inbound_send()``. ``steer`` calls ``ControlContract.steer`` when this
    link was built with ``control`` and the target is on ``steer_peers``.
    ``steer_peers`` maps the target name to that agent's control session;
    the send ``context_id`` is not the session. The ``control`` object is
    re-scoped to this sender, surface ``peer``, ``steer`` only, and those
    sessions, even if the caller passed an admin handle. A steer also
    needs the target on SendTool ``allowed_peers``.
    """

    CAPABILITIES = capability_set(unicast=True)
    HONOURED_SEND_INTENTS = INFORM_ASK_INTENTS

    def __init__(
        self,
        run_turn: RunTurnFn,
        *,
        agent_id: str = "",
        max_message_size: int = MAX_MESSAGE_SIZE,
        control: ControlContract | None = None,
        steer_peers: Mapping[str, str] | Sequence[str] | None = None,
    ) -> None:
        self._run_turn = run_turn
        self.agent_id = agent_id
        self._max_message_size = max_message_size
        self._control = control
        self._steer_sessions = steer_peer_sessions(steer_peers)

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)

    def honoured_send_intents(self) -> frozenset[str]:
        if self._control is None:
            return self.HONOURED_SEND_INTENTS
        return self.HONOURED_SEND_INTENTS | {STEER_INTENT}

    def close(self) -> None:
        return None

    def _plan(
        self,
        task: str,
        headers: dict[str, str] | None,
        context: SendContext | None,
    ) -> tuple[dict[str, str], str, str, str, str]:
        check_message_size(task, max_size=self._max_message_size)
        headers, context = align_send(headers, context, sender=self.agent_id)
        kind = require_honoured_intent(self, context.intent)
        payload = inbound_payload(task, sender=context.sender, intent=kind)
        return headers, kind, payload, context.sender, context.mode

    def _steer(self, target_agent_id: str, text: str, *, mode: str) -> str:
        name = str(target_agent_id)
        return dispatch_steer(
            self._control,
            session_id=self._steer_sessions.get(name, ""),
            text=text,
            mode=mode,
            target=name,
            allowed=self._steer_sessions,
            sender=self.agent_id,
        )

    def _deliver(
        self,
        target_agent_id: str,
        headers: dict[str, str],
        kind: str,
        payload: str,
        sender: str,
        *,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
    ) -> str:
        with bound_inbound(sender=sender, intent=kind), _bound_validated(merge_headers(headers)):
            result = self._run_turn(
                target_agent_id,
                payload,
                correlation_id,
                caller_call_id,
                context_id,
            )
        return send_result(kind, result)

    def send(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        headers, kind, payload, sender, mode = self._plan(task, headers, context)
        if kind == STEER_INTENT:
            return self._steer(target_agent_id, task, mode=mode)
        return self._deliver(
            target_agent_id,
            headers,
            kind,
            payload,
            sender,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
        )

    async def asend(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        headers, kind, payload, sender, mode = self._plan(task, headers, context)
        if kind == STEER_INTENT:
            return self._steer(target_agent_id, task, mode=mode)
        asend_fn = getattr(self._run_turn, "asend", None)
        if callable(asend_fn):
            with bound_inbound(sender=sender, intent=kind), _bound_validated(merge_headers(headers)):
                result = await asend_fn(
                    target_agent_id,
                    payload,
                    correlation_id,
                    caller_call_id,
                    context_id,
                )
            return send_result(kind, result)
        return self._deliver(
            target_agent_id,
            headers,
            kind,
            payload,
            sender,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
        )

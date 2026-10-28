#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process agent_comm plugin — implementations live here, not in runtime."""

from __future__ import annotations

from collections.abc import Callable

from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.headers import _bound_validated, merge_headers
from mas.library.standard.lib.comm.payload import (
    INFORM_ASK_INTENTS,
    MAX_MESSAGE_SIZE,
    bound_inbound,
    check_message_size,
    inbound_payload,
    require_honoured_intent,
    send_result,
)
from mas.library.standard.lib.comm.send_context import align_send
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, SendContext

RunTurnFn = Callable[[str, str, int, str, str], str]


class LocalAgentComm(AgentCommContract):
    """Agent communication over the current in-process workflow bus.

    The turn runs in this process, so ``headers`` are the ambient headers of
    the target turn. Only hop-safe keys ride a later send from that turn.
    ``inform`` and ``ask`` still run that turn and wait for it. The peer
    sees a text label as a hint; sender and intent are also on
    ``inbound_send()``. ``steer`` is not supported here.
    """

    CAPABILITIES = capability_set(unicast=True)
    HONOURED_SEND_INTENTS = INFORM_ASK_INTENTS

    def __init__(
        self,
        run_turn: RunTurnFn,
        *,
        agent_id: str = "",
        max_message_size: int = MAX_MESSAGE_SIZE,
    ) -> None:
        self._run_turn = run_turn
        self.agent_id = agent_id
        self._max_message_size = max_message_size

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)

    def honoured_send_intents(self) -> frozenset[str]:
        return self.HONOURED_SEND_INTENTS

    def close(self) -> None:
        return None

    def _plan(
        self,
        task: str,
        headers: dict[str, str] | None,
        context: SendContext | None,
    ) -> tuple[dict[str, str], str, str, str]:
        check_message_size(task, max_size=self._max_message_size)
        headers, context = align_send(headers, context, sender=self.agent_id)
        kind = require_honoured_intent(self, context.intent)
        payload = inbound_payload(task, sender=context.sender, intent=kind)
        return headers, kind, payload, context.sender

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
        headers, kind, payload, sender = self._plan(task, headers, context)
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
        headers, kind, payload, sender = self._plan(task, headers, context)
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

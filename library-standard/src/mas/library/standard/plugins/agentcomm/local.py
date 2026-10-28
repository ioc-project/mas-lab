#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process agent_comm plugin — implementations live here, not in runtime."""

from __future__ import annotations

from collections.abc import Callable

from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.headers import _bound_validated, merge_headers
from mas.library.standard.lib.comm.send_context import align_send
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, SendContext

RunTurnFn = Callable[[str, str, int, str, str], str]


class LocalAgentComm(AgentCommContract):
    """Agent communication over the current in-process workflow bus.

    The turn runs in this process, so ``headers`` are the ambient headers of
    the target turn. Only hop-safe keys ride a later send from that turn.
    """

    CAPABILITIES = capability_set(unicast=True)

    def __init__(self, run_turn: RunTurnFn, *, agent_id: str = "") -> None:
        self._run_turn = run_turn
        self.agent_id = agent_id

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)

    def close(self) -> None:
        return None

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
        headers, _context = align_send(headers, context, sender=self.agent_id)
        with _bound_validated(merge_headers(headers)):
            return self._run_turn(
                target_agent_id,
                task,
                correlation_id,
                caller_call_id,
                context_id,
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
        asend_fn = getattr(self._run_turn, "asend", None)
        if callable(asend_fn):
            headers, context = align_send(headers, context, sender=self.agent_id)
            with _bound_validated(merge_headers(headers)):
                return await asend_fn(
                    target_agent_id,
                    task,
                    correlation_id,
                    caller_call_id,
                    context_id,
                )
        return self.send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
        )
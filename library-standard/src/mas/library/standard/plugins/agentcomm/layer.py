#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared bind/forward helpers for agent_comm decorator layers."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, ClassVar, cast

from mas.library.standard.lib.comm.capabilities import capability_set, read_capabilities
from mas.library.standard.lib.comm.send_context import align_send
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext


class BoundLayer:
    """Forwards unknown methods to the lower layer after ``bind``.

    ``start`` / ``stop`` run at most once and do not forward. The stack
    starts and stops every layer itself, so the link is not started twice.
    """

    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set()

    def __init__(self, **_params: Any) -> None:
        self._lower: Any = None
        self._started = False
        self._stopped = False

    def bind(self, lower: object) -> None:
        self._lower = lower

    def _require(self) -> Any:
        if self._lower is None:
            raise AgentCommError(f"{type(self).__name__} is unbound")
        return self._lower

    @property
    def agent_id(self) -> str:
        value = getattr(self._require(), "agent_id", None)
        if not value:
            raise AttributeError("lower layer has no agent_id")
        return str(value)

    def capabilities(self) -> dict[str, bool]:
        return read_capabilities(self._require()) or capability_set()

    def start(self) -> None:
        if self._started:
            return
        self._started = True

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True

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
        if headers is not None or context is not None:
            headers, context = align_send(headers, context)
        return self._require().send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
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
        if headers is not None or context is not None:
            headers, context = align_send(headers, context)
        fn = getattr(self._require(), "asend", None)
        if callable(fn):
            return await cast(Awaitable[str], fn(
                target_agent_id,
                task,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
                context_id=context_id,
                headers=headers,
                context=context,
            ))
        return self.send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or self._lower is None:
            raise AttributeError(name)
        return getattr(self._lower, name)

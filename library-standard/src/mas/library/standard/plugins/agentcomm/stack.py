#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Optional agent_comm stack: compose layers bottom → top."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any, cast

from mas.library.standard.lib.comm.capabilities import declared_capabilities, merge_capabilities
from mas.library.standard.lib.comm.compose import bind_bottom_up, instantiate_layers
from mas.library.standard.lib.comm.send_context import merge_send_context
from mas.runtime.boundary.agentcomm.protocol import SendContext


def _layer_caps(layer: Any) -> dict[str, bool] | None:
    fn = getattr(layer, "capabilities", None)
    if callable(fn):
        return dict(fn())
    flags = getattr(type(layer), "CAPABILITIES", None)
    if isinstance(flags, Mapping):
        return dict(flags)
    return None


class StackAgentComm:
    """Bind ``layers`` bottom-up; calls enter at the top.

    Starting the stack starts every layer, bottom first. Each layer's
    start runs at most once. The link at the bottom stamps
    ``SendContext.sender`` from its ``agent_id``.
    """

    def __init__(
        self,
        layers: list[Any] | None = None,
        *,
        run_turn: Any = None,
        registry: Any = None,
        **params: Any,
    ) -> None:
        raw = list(layers or params.get("layers") or [])
        extra = {k: v for k, v in params.items() if k != "layers"}
        if run_turn is not None:
            extra["run_turn"] = run_turn
        if registry is not None:
            extra["registry"] = registry
        self._layers = instantiate_layers("agent_comm", raw, extra)
        if not self._layers:
            raise ValueError("agent_comm/stack requires at least one layer")
        bind_bottom_up(self._layers)
        self._started = False
        self._stopped = False

    @classmethod
    def declared_capabilities(cls, params: dict[str, Any], *, registry: Any = None) -> dict[str, bool] | None:
        """OR-merge of the layers' declared flags; unknown when any layer is unknown."""
        sets: list[dict[str, bool]] = []
        for layer in params.get("layers") or []:
            caps = declared_capabilities(layer, registry=registry)
            if caps is None:
                return None
            sets.append(caps)
        return merge_capabilities(*sets) if sets else None

    @property
    def agent_id(self) -> str:
        for layer in self._layers:
            value = getattr(layer, "agent_id", None)
            if value:
                return str(value)
        raise AttributeError("stack layers have no agent_id")

    @property
    def _top(self) -> Any:
        return self._layers[-1]

    def capabilities(self) -> dict[str, bool]:
        sets = [_layer_caps(layer) for layer in self._layers]
        merged = merge_capabilities(*sets)
        return merged if merged is not None else {}

    def bind(self, lower: object) -> None:
        bind = getattr(self._layers[0], "bind", None)
        if callable(bind):
            bind(lower)

    def start(self) -> None:
        if self._started:
            return
        for layer in self._layers:
            start = getattr(layer, "start", None)
            if callable(start):
                start()
        self._started = True

    def stop(self) -> None:
        if self._stopped:
            return
        for layer in reversed(self._layers):
            stop = getattr(layer, "stop", None)
            if callable(stop):
                stop()
        self._stopped = True

    def _link_sender(self) -> str:
        value = getattr(self._layers[0], "agent_id", "") or ""
        return str(value)

    def _stamp(self, headers: dict[str, str] | None, context: SendContext | None) -> SendContext:
        return merge_send_context(headers, context, sender=self._link_sender())

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
        stamped = self._stamp(headers, context)
        return self._top.send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=stamped.headers,
            context=stamped,
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
        stamped = self._stamp(headers, context)
        fn = getattr(self._top, "asend", None)
        if callable(fn):
            return await cast(Awaitable[str], fn(
                target_agent_id,
                task,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
                context_id=context_id,
                headers=stamped.headers,
                context=stamped,
            ))
        return self.send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=stamped.headers,
            context=stamped,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._top, name)

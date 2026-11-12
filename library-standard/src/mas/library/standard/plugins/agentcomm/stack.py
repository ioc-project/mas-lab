#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""agent_comm/stack: the agent-communication adapter over the generic ``Stack``."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any, cast

from mas.library.standard.lib.comm.capabilities import declared_capabilities, merge_capabilities, read_capabilities
from mas.library.standard.lib.comm.compose import instantiate_layers, layer_binding, resolve_layer_class
from mas.library.standard.lib.comm.payload import TASK_ONLY_INTENTS
from mas.library.standard.lib.comm.send_context import merge_send_context
from mas.library.standard.lib.stack import Envelope, Handler, Layer, Stack, StackError, StackSendError
from mas.library.standard.lib.stack.agent_comm import to_envelope
from mas.library.standard.lib.stack.layer import LinkIdentity
from mas.library.standard.lib.stack.legacy import adapt_legacy
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext


def _declared_layer_capabilities(layer: Any, registry: Any) -> dict[str, bool] | None:
    """The flags one layer entry declares; a ``{type, params}`` id resolves as a link, then a ``layer``."""
    if not isinstance(layer, Mapping):
        return declared_capabilities(layer, registry=registry)
    binding = layer_binding(dict(layer))
    try:
        cls = resolve_layer_class("agent_comm", binding, registry)
    except Exception:
        return None
    return read_capabilities(cls, params=binding["params"], registry=registry)


def _as_comm_error(exc: StackError) -> AgentCommError:
    """A layer error as the contract's ``AgentCommError``; one that already is stays as it is."""
    if isinstance(exc, AgentCommError):
        return exc
    return StackSendError(str(exc))


class StackAgentComm(LinkIdentity):
    """Compose ``layers`` bottom to top; calls enter at the top.

    The first layer is a link: a ``Layer`` that sends, or any object with
    ``send(target, task, ...)``. The layers above are generic ``Layer`` objects
    (for example the ``a2a-framing`` layer) or ``BoundLayer`` decorators, a
    deprecated form; ``adapt_legacy`` turns a link or decorator into a ``Layer``.
    The generic ``Stack`` underneath starts the layers bottom first, merges
    their capabilities, and stamps a child ``traceparent`` on every send. The
    link at the bottom supplies ``SendContext.sender`` from its ``agent_id``.
    A stack says who it is from that link (``agent_id``, ``plugin_id``,
    ``endpoint``); ``fabric_id`` is set by the ``StackHandle`` that built it.
    It forwards no other attribute.

    ``send`` and ``asend`` raise ``AgentCommError`` for every failure, as the
    agent-call contract says: a layer's ``StackError`` comes out as
    ``StackSendError`` (an ``AgentCommError``), with the layer's error as the
    cause. A send intent is honoured when the bottom link
    and every layer that declares intents honour it.
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
        if not raw:
            raise ValueError("agent_comm/stack requires at least one layer")
        built = instantiate_layers("agent_comm", raw, extra)
        self._stack = Stack([adapt_legacy(item, bottom=index == 0) for index, item in enumerate(built)])

    @classmethod
    def declared_capabilities(cls, params: dict[str, Any], *, registry: Any = None) -> dict[str, bool] | None:
        """OR-merge of the layers' declared flags; unknown when any layer is unknown."""
        sets: list[dict[str, bool]] = []
        for layer in params.get("layers") or []:
            caps = _declared_layer_capabilities(layer, registry)
            if caps is None:
                return None
            sets.append(caps)
        return merge_capabilities(*sets) if sets else None

    @property
    def _layers(self) -> tuple[Layer, ...]:
        return self._stack.layers

    @property
    def agent_id(self) -> str:  # type: ignore[override]
        value = self._layers[0].agent_id
        if not value:
            raise AttributeError("the bottom layer has no agent_id")
        return value

    @property
    def plugin_id(self) -> str:  # type: ignore[override]
        return self._layers[0].plugin_id

    @property
    def endpoint(self) -> str:  # type: ignore[override]
        return self._layers[0].endpoint

    def capabilities(self) -> dict[str, bool]:
        return self._stack.capabilities()

    def honoured_send_intents(self) -> frozenset[str]:
        """The bottom link's intents, narrowed by every layer above it that declares some."""
        bottom = self._layers[0].honoured_send_intents()
        honoured = TASK_ONLY_INTENTS if bottom is None else bottom
        for layer in self._layers[1:]:
            declared = layer.honoured_send_intents()
            if declared is not None:
                honoured = honoured & declared
        return honoured

    @property
    def wraps_inbound(self) -> bool:
        return self._stack.wraps_inbound

    def serve(self, handler: Handler) -> Handler:
        """Wrap ``handler`` in every layer, top first, and give the chain to the bottom link."""
        return self._stack.serve(handler)

    def bind(self, lower: object) -> None:
        self._stack.bind(lower)

    def start(self) -> None:
        self._stack.start()

    def stop(self) -> None:
        self._stack.stop()

    def _link_sender(self) -> str:
        return self._layers[0].agent_id

    def _stamp(self, headers: dict[str, str] | None, context: SendContext | None) -> SendContext:
        return merge_send_context(headers, context, sender=self._link_sender())

    def _envelope(
        self,
        target_agent_id: str,
        task: str,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
        headers: dict[str, str] | None,
        context: SendContext | None,
    ) -> Envelope:
        stamped = self._stamp(headers, context)
        return to_envelope(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=stamped.headers,
            context=stamped,
        )

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
        envelope = self._envelope(target_agent_id, task, correlation_id, caller_call_id, context_id, headers, context)
        try:
            return cast(str, self._stack.send(envelope))
        except StackError as exc:
            raise _as_comm_error(exc) from exc

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
        envelope = self._envelope(target_agent_id, task, correlation_id, caller_call_id, context_id, headers, context)
        try:
            return cast(str, await cast(Awaitable[Any], self._stack.asend(envelope)))
        except StackError as exc:
            raise _as_comm_error(exc) from exc

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The layer contract: ``bind(lower)``, ``send``, ``asend``, ``serve``, ``start``, ``stop``, ``capabilities``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from mas.library.standard.lib.comm.capabilities import capability_set, merge_capabilities
from mas.library.standard.lib.stack.envelope import Envelope
from mas.library.standard.lib.stack.errors import StackError

Handler = Callable[[Envelope], Any]
"""An inbound handler: one received request envelope in, the reply payload out."""


class LinkIdentity:
    """How a link or layer says who it is, so a caller can tell whether two of them ride the same fabric.

    Every attribute has a default, so a link that is nothing in particular needs to declare none of them:
    ``plugin_id`` is the plugin id, ``endpoint`` the address it reaches, ``fabric_id`` the id of the stack
    that built it (set by ``StackHandle``), ``agent_id`` the agent it speaks for.
    """

    plugin_id: str = ""
    endpoint: str = ""
    fabric_id: int | None = None
    agent_id: str = ""


class ChainMember(LinkIdentity):
    """What a class says about itself when it joins a chain a Flavour binds.

    Every hook has a default, read directly: a class that overrides none is a plain member.
    A link plugin that is not a ``Layer`` (a node transport, ``local-group``) subclasses this to take part in
    ``agent_name``, ``check_agent_id`` and ``shared_service``; a link that does not (the A2A SDK client)
    is a direct link, one agent at its own url.
    """

    #: Why an agent cannot be served from this process on this transport, or ``""``.
    LISTEN_REFUSAL: ClassVar[str] = ""

    @classmethod
    def shared_service(cls, params: Mapping[str, Any]) -> Any:
        """One service for every agent of a stack (it has ``start()`` and ``close()``), or ``None``."""
        return None

    @classmethod
    def agent_name(cls, params: Mapping[str, Any], agent_id: str) -> str:
        """The address of ``agent_id`` on this layer; by default the agent id itself."""
        return agent_id

    @classmethod
    def check_agent_id(cls, params: Mapping[str, Any], agent_id: str) -> None:
        """Raise ``AgentCommError`` when this layer's grammar refuses ``agent_id``; by default it takes any."""
        return None


class Layer(ChainMember):
    """Base for every layer: a transport at the bottom, or a protocol above it.

    A layer above the bottom has ``needs_lower = True`` and forwards to the layer
    it was bound to. A transport sits at the bottom, sets ``needs_lower = False``
    and overrides ``send``. ``start`` and ``stop`` run at most once and never
    forward: the stack starts every layer itself, bottom first.

    A layer cannot turn a capability off: ``capabilities`` OR-merges the flags
    this layer declares with the ones below it.

    ``serve(handler)`` is the inbound side. It takes the handler of the layers
    above and returns the handler the layer below should call with what it
    received. The default returns ``handler`` unchanged: a layer that only acts
    on sends leaves inbound requests alone. A protocol layer
    returns a handler that decodes the request, calls ``handler``, and encodes
    the reply. A transport keeps the handler it is given and calls it for every
    request it receives. A layer that overrides ``serve`` ``wraps_inbound``.

    ``honoured_send_intents`` is ``None`` for a layer that does not limit the intents
    of a send; a layer that does returns the set it honours.
    """

    needs_lower: ClassVar[bool] = True
    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set()

    def __init__(self, **_params: Any) -> None:
        self._lower: Layer | None = None
        self._started = False
        self._stopped = False

    def bind(self, lower: Layer) -> None:
        self._lower = lower

    def _require(self) -> Layer:
        if self._lower is None:
            raise StackError(f"{type(self).__name__} is unbound")
        return self._lower

    def capabilities(self) -> dict[str, bool]:
        own = dict(type(self).CAPABILITIES)
        if not self.needs_lower:
            return own
        below = self._require().capabilities()
        merged = merge_capabilities(own, below)
        return merged if merged is not None else capability_set()

    def honoured_send_intents(self) -> frozenset[str] | None:
        return None

    @property
    def wraps_inbound(self) -> bool:
        """True when this layer changes what the layers above receive (it overrides ``serve``)."""
        return type(self).serve is not Layer.serve

    def start(self) -> None:
        if not self._started:
            self._started = True

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True

    def serve(self, handler: Handler) -> Handler:
        return handler

    def send(self, envelope: Envelope) -> Any:
        return self._require().send(envelope)

    async def asend(self, envelope: Envelope) -> Any:
        if not self.needs_lower:
            return self.send(envelope)
        return await self._require().asend(envelope)

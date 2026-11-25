#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A layer over one call kind. Send keeps :class:`Layer`; llm/tool keep invoke ports."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import httpx
from mas.library.standard.lib.stack.session import SessionHook
from mas.runtime.layers.base import LayerBase
from mas.runtime.layers.lifecycle import Lifecycle, acall_tick, call_start, call_stop

Req = TypeVar("Req")
Resp = TypeVar("Resp")

__all__ = ["AuthorizableTransport", "CallLayer", "LayerLifecycle", "call_start", "call_stop"]


class LayerLifecycle(LayerBase):
    """bind / start / stop / capabilities for llm and tool wrap layers."""

    inner: Any = None
    _started: bool = False
    _stopped: bool = False

    def bind(self, inner: object) -> None:
        self.inner = inner

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        call_start(self.inner)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        call_stop(self.inner)

    async def atick(self) -> None:
        await acall_tick(self.inner)

    def own_capabilities(self) -> dict[str, bool]:
        """What this layer adds. A layer that adds a capability overrides this."""
        return {}

    def session_hooks(self) -> list[SessionHook]:
        """The session hooks of the layers below, for a protocol layer that opens sessions (MCP) to call.

        A layer that contributes a hook overrides this and adds its own to what the layer below reports.
        """
        return self.inner.session_hooks() if isinstance(self.inner, LayerLifecycle) else []

    def capabilities(self) -> dict[str, bool]:
        below = self.inner.capabilities() if isinstance(self.inner, Lifecycle) else {}
        return {**below, **self.own_capabilities()}


class AuthorizableTransport(LayerLifecycle):
    """A call transport whose requests a layer above it may authorize and whose sessions it may take part in.

    ``use_auth`` installs an ``httpx.Auth`` that every request of the transport's clients passes through: it
    can add headers, and it sees the response, so it can answer a 401 once. ``add_session_hook`` hands a
    :class:`~mas.library.standard.lib.stack.session.SessionHook` to the protocol layer that sits above.
    Both must be called before the first request.
    """

    @abstractmethod
    def use_auth(self, auth: httpx.Auth, *, guard: Callable[[httpx.Request], None] | None = None) -> None:
        """Pass every request through ``auth``; ``guard``, when given, sees every request, each redirect hop included.

        A second call is refused: two layers would overwrite each other."""

    @abstractmethod
    def add_session_hook(self, hook: SessionHook) -> None:
        """Offer ``hook`` to the protocol layer above."""


class CallLayer(Protocol[Req, Resp]):
    """Wrap the next call: invoke once, again (retry), not at all (cache, breaker open), or after a delay.

    Ports stay typed per kind: ``InvokeEngineIo -> EngineIoReturn`` for llm/tool,
    ``Envelope`` for send. Send specialises this as :class:`~mas.library.standard.lib.stack.layer.Layer`.
    """

    def bind(self, inner: object) -> None: ...

    async def ainvoke(self, request: Req) -> Resp: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def capabilities(self) -> dict[str, bool]: ...

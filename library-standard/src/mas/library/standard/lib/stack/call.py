#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A layer over one call kind. Send keeps :class:`Layer`; llm/tool keep invoke ports."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from mas.runtime.layers.base import LayerBase
from mas.runtime.layers.lifecycle import Lifecycle, call_start, call_stop, call_tick

Req = TypeVar("Req")
Resp = TypeVar("Resp")

__all__ = ["CallLayer", "LayerLifecycle", "call_start", "call_stop"]


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

    def tick(self) -> None:
        call_tick(self.inner)

    def own_capabilities(self) -> dict[str, bool]:
        """What this layer adds. A layer that adds a capability overrides this."""
        return {}

    def capabilities(self) -> dict[str, bool]:
        below = self.inner.capabilities() if isinstance(self.inner, Lifecycle) else {}
        return {**below, **self.own_capabilities()}


class CallLayer(Protocol[Req, Resp]):
    """Wrap the next call: invoke once, again (retry), not at all (cache, breaker open), or after a delay.

    Ports stay typed per kind: ``InvokeEngineIo -> EngineIoReturn`` for llm/tool,
    ``Envelope`` for send. Send specialises this as :class:`~mas.library.standard.lib.stack.layer.Layer`.
    """

    def bind(self, inner: object) -> None: ...

    def invoke(self, request: Req) -> Resp: ...

    async def ainvoke(self, request: Req) -> Resp: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def capabilities(self) -> dict[str, bool]: ...

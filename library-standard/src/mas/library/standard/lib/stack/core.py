#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The stack: owns its layers, bottom to top."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mas.library.standard.lib.comm.capabilities import merge_capabilities
from mas.library.standard.lib.stack.envelope import Envelope
from mas.library.standard.lib.stack.layer import Handler, Layer
from mas.library.standard.lib.stack.trace import child_traceparent


class Stack:
    """Composes ``layers`` bottom to top and enters every call at the top.

    The stack has the overall view that no single layer has:

    * one lifecycle: ``start`` runs the layers bottom first, ``stop`` top first;
    * one capability view: the OR-merge of every layer;
    * one stamp: every send leaves with a child W3C ``traceparent``;
    * one inbound chain: ``serve(handler)`` wraps ``handler`` in every layer, top first,
      and gives the result to the bottom layer.
    """

    def __init__(self, layers: Sequence[Layer]) -> None:
        self._layers = list(layers)
        if not self._layers:
            raise ValueError("a stack requires at least one layer")
        for index, layer in enumerate(self._layers):
            if not isinstance(layer, Layer):
                raise TypeError(f"layer {index} ({type(layer).__name__}) is not a Layer")
        if self._layers[0].needs_lower:
            raise ValueError(f"the bottom layer ({type(self._layers[0]).__name__}) must be a transport")
        for index in range(1, len(self._layers)):
            layer = self._layers[index]
            if not layer.needs_lower:
                raise ValueError(f"layer {index} ({type(layer).__name__}) is a transport; only the first can be one")
            layer.bind(self._layers[index - 1])
        self._started = False
        self._stopped = False

    @property
    def layers(self) -> tuple[Layer, ...]:
        return tuple(self._layers)

    @property
    def _top(self) -> Layer:
        return self._layers[-1]

    def bind(self, lower: Layer) -> None:
        """Hand ``lower`` to the bottom layer, for a bottom link that takes one."""
        self._layers[0].bind(lower)

    def capabilities(self) -> dict[str, bool]:
        merged = merge_capabilities(*(layer.capabilities() for layer in self._layers))
        return merged if merged is not None else {}

    @property
    def wraps_inbound(self) -> bool:
        """True when a layer above the bottom changes what an inbound request looks like."""
        return any(layer.wraps_inbound for layer in self._layers[1:])

    def serve(self, handler: Handler) -> Handler:
        """Build the inbound chain top to bottom and hand the result to the bottom layer.

        ``handler`` answers a request as the top of this stack sees it. Each
        layer, from the top down, wraps the handler of the layers above it;
        the bottom layer gets the outermost one. A transport keeps it; the
        default ``Layer.serve`` returns it unchanged.
        """
        chain = handler
        for layer in reversed(self._layers):
            chain = layer.serve(chain)
        return chain

    def start(self) -> None:
        """Start every layer, bottom first. A layer that fails to start stops the stack, then its error is raised."""
        if self._started:
            return
        try:
            for layer in self._layers:
                layer.start()
        except BaseException:
            try:
                self.stop()
            except Exception:  # the start error is the one to report
                pass
            raise
        self._started = True

    def stop(self) -> None:
        """Stop every layer, top first. A layer that fails to stop does not keep the ones below it running:
        the first error is raised after the last layer stopped."""
        if self._stopped:
            return
        self._stopped = True
        first: Exception | None = None
        for layer in reversed(self._layers):
            try:
                layer.stop()
            except Exception as exc:
                first = first or exc
        if first is not None:
            raise first

    @staticmethod
    def _stamped(envelope: Envelope) -> Envelope:
        return envelope.with_(headers={"traceparent": child_traceparent(envelope.headers.get("traceparent"))})

    def send(self, envelope: Envelope) -> Any:
        return self._top.send(self._stamped(envelope))

    async def asend(self, envelope: Envelope) -> Any:
        return await self._top.asend(self._stamped(envelope))

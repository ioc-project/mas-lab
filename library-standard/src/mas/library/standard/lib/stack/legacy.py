#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The one place that duck-types: links and decorators that were written before layers.

A released ``agent_comm`` link (``send(target, task, ...)``) or ``BoundLayer`` decorator never
subclassed ``Layer``, so what it offers beyond ``send`` is found by name. ``adapt_legacy`` turns
one into a ``Layer`` and every other module of the stack reads layers by their declared contract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from mas.library.standard.lib.comm.capabilities import read_capabilities
from mas.library.standard.lib.comm.payload import intents_honoured_by
from mas.library.standard.lib.stack.agent_comm import call_kwargs, to_envelope
from mas.library.standard.lib.stack.envelope import Envelope
from mas.library.standard.lib.stack.errors import StackError
from mas.library.standard.lib.stack.layer import Handler, Layer, LinkIdentity
from mas.library.standard.plugins.agentcomm.layer import BoundLayer


def _probe(target: Any, name: str, default: Any = None) -> Any:
    """``target.<name>``, or ``default``: the single lookup by name a legacy object needs."""
    return getattr(target, name, default)


def _method(target: Any, name: str) -> Callable[..., Any] | None:
    fn = _probe(target, name)
    return fn if callable(fn) else None


def _call_if_present(target: Any, name: str) -> None:
    fn = _method(target, name)
    if fn is not None:
        fn()


def adapt_legacy(item: Any, *, bottom: bool) -> Layer:
    """``item`` as a stack layer: a ``Layer`` as it is, a legacy link or decorator through an adapter.

    A legacy link goes at the bottom and needs ``send``; a ``BoundLayer`` decorator goes above it and
    needs ``bind``. Anything else is refused with the reason.
    """
    if isinstance(item, Layer):
        return item
    if bottom:
        if isinstance(item, BoundLayer):
            raise ValueError("a bind layer cannot sit at the bottom; put a link first")
        if _method(item, "send") is None:
            raise ValueError("the bottom layer must be a link with send")
        return LinkLayer(item)
    if _method(item, "bind") is None:
        raise ValueError("layers above the bottom must bind to the layer below")
    return LegacyLayer(item)


class LinkLayer(Layer):
    """A link with ``send(target, task, ...)`` as the bottom layer of a stack. ``link`` is the link itself."""

    needs_lower = False

    def __init__(self, link: Any) -> None:
        super().__init__()
        self.link = link

    @property
    def plugin_id(self) -> str:  # type: ignore[override]
        return str(_probe(self.link, "plugin_id", "") or "")

    @property
    def endpoint(self) -> str:  # type: ignore[override]
        return str(_probe(self.link, "endpoint", "") or "")

    @property
    def fabric_id(self) -> int | None:  # type: ignore[override]
        value = _probe(self.link, "fabric_id")
        return value if isinstance(value, int) else None

    @property
    def agent_id(self) -> str:  # type: ignore[override]
        return str(_probe(self.link, "agent_id", "") or "")

    def bind(self, lower: Layer) -> None:
        super().bind(lower)
        fn = _method(self.link, "bind")
        if fn is not None:
            fn(lower)

    def capabilities(self) -> dict[str, bool] | None:  # type: ignore[override]
        return read_capabilities(self.link)

    def honoured_send_intents(self) -> frozenset[str] | None:
        return intents_honoured_by(self.link)

    def start(self) -> None:
        if not self._started:
            _call_if_present(self.link, "start")
            self._started = True

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            _call_if_present(self.link, "stop")

    def serve(self, handler: Handler) -> Handler:
        """The link takes the inbound chain when it has ``serve``; otherwise the layers above cannot decode."""
        fn = _method(self.link, "serve")
        if fn is None:
            raise StackError(f"{type(self.link).__name__} cannot pass inbound requests to the layers above it")
        result = fn(handler)
        return handler if result is None else result

    def send(self, envelope: Envelope) -> Any:
        return self.link.send(str(envelope.meta["target"]), envelope.payload, **call_kwargs(envelope))

    async def asend(self, envelope: Envelope) -> Any:
        fn = _method(self.link, "asend")
        if fn is not None:
            target = str(envelope.meta["target"])
            return await cast(Awaitable[Any], fn(target, envelope.payload, **call_kwargs(envelope)))
        return self.send(envelope)


class _View(LinkIdentity):
    """What a ``BoundLayer`` sees below it: the layer below, as ``send(target, task, ...)``.

    It offers ``send``, ``asend``, ``capabilities`` and the link's identity, nothing found by name.
    """

    def __init__(self, below: Layer) -> None:
        self._below = below

    @property
    def plugin_id(self) -> str:  # type: ignore[override]
        return self._below.plugin_id

    @property
    def endpoint(self) -> str:  # type: ignore[override]
        return self._below.endpoint

    @property
    def fabric_id(self) -> int | None:  # type: ignore[override]
        return self._below.fabric_id

    @property
    def agent_id(self) -> str:  # type: ignore[override]
        return self._below.agent_id

    def send(self, target_agent_id: str, task: str, **kwargs: Any) -> Any:
        return self._below.send(to_envelope(target_agent_id, task, **kwargs))

    async def asend(self, target_agent_id: str, task: str, **kwargs: Any) -> Any:
        return await self._below.asend(to_envelope(target_agent_id, task, **kwargs))

    def capabilities(self) -> dict[str, bool] | None:
        return self._below.capabilities()


class LegacyLayer(Layer):
    """A ``BoundLayer`` decorator running as a layer. Kept so existing decorators keep working."""

    def __init__(self, decorator: Any) -> None:
        super().__init__()
        self.decorator = decorator

    def bind(self, lower: Layer) -> None:
        super().bind(lower)
        self.decorator.bind(_View(lower))

    def capabilities(self) -> dict[str, bool] | None:  # type: ignore[override]
        return read_capabilities(self.decorator)

    def honoured_send_intents(self) -> frozenset[str] | None:
        """The intents the decorator declares, or ``None`` when it declares none."""
        declares = _method(self.decorator, "honoured_send_intents") or _probe(self.decorator, "HONOURED_SEND_INTENTS")
        if declares is None:
            return None
        return intents_honoured_by(self.decorator)

    def start(self) -> None:
        if not self._started:
            _call_if_present(self.decorator, "start")
            self._started = True

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            _call_if_present(self.decorator, "stop")

    def send(self, envelope: Envelope) -> Any:
        return self.decorator.send(str(envelope.meta["target"]), envelope.payload, **call_kwargs(envelope))

    async def asend(self, envelope: Envelope) -> Any:
        return await self.decorator.asend(str(envelope.meta["target"]), envelope.payload, **call_kwargs(envelope))

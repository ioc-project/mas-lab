#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Checks every layer must pass, whatever it carries. Used by each layer's own tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from mas.library.standard.lib.comm.capabilities import CAPABILITY_FLAGS, capability_set
from mas.library.standard.lib.stack import Envelope, Layer, StackError


class RecordingLower(Layer):
    """A bottom layer that records what it received and answers ``"ok"``."""

    needs_lower = False

    def __init__(self, answer: Any = "ok", **flags: bool) -> None:
        super().__init__()
        self.seen: list[Envelope] = []
        self.answer = answer
        self._flags = capability_set(**flags)

    def capabilities(self) -> dict[str, bool]:
        return dict(self._flags)

    def send(self, envelope: Envelope) -> Any:
        self.seen.append(envelope)
        return self.answer


def check_layer(make: Callable[[], Layer], *, lower_answer: Any = "ok", expect: Any = "ok") -> None:
    """A layer above the bottom: unbound send fails, lifecycle is idempotent, capabilities only grow,
    a plain send reaches the layer below, sync and async agree.

    The layer below answers ``lower_answer``; the layer must return ``expect``. A
    protocol layer passes the framed answer its decoder reads (for ``a2a``, a
    JSON-RPC response) and the reply text it should decode to."""
    unbound = make()
    try:
        unbound.send(Envelope(payload="x"))
    except StackError:
        pass
    else:
        raise AssertionError("an unbound layer must raise StackError on send")

    lower = RecordingLower(lower_answer, unicast=True, e2e=True)
    layer = make()
    layer.bind(lower)

    layer.start()
    layer.start()
    layer.stop()
    layer.stop()

    caps = layer.capabilities()
    assert set(caps) == set(CAPABILITY_FLAGS)
    for name, value in lower.capabilities().items():
        assert caps[name] or not value, f"layer turned capability {name!r} off"

    env = Envelope(payload="hello", headers={"h": "1"}, meta={"target": "b"})
    answer = layer.send(env)
    assert answer == expect
    assert lower.seen, "the send never reached the layer below"
    assert lower.seen[-1].meta["target"] == "b"

    seen_before = len(lower.seen)
    assert asyncio.run(layer.asend(env)) == expect
    assert len(lower.seen) > seen_before


def check_transport(
    make: Callable[[], Layer],
    *,
    envelope: Envelope,
    reply: Any,
    delivered: Callable[[], int] | None = None,
) -> None:
    """A bottom layer: it needs nothing below, start and stop are idempotent, its capability flags
    are the known set of booleans, a send reaches the peer and returns ``reply``, and ``asend`` agrees.

    ``make`` builds the transport; ``envelope`` is a send its peer answers with
    ``reply``. ``delivered`` counts what the peer received, so the check can see
    that each call reached it.
    """
    layer = make()
    assert not layer.needs_lower, "a transport must not need a layer below it"
    layer.start()
    layer.start()

    caps = layer.capabilities()
    assert caps is not None, "a transport declares its capabilities"
    assert set(caps) == set(CAPABILITY_FLAGS), f"unknown or missing flags: {sorted(set(caps) ^ set(CAPABILITY_FLAGS))}"
    assert all(isinstance(value, bool) for value in caps.values())

    before = delivered() if delivered is not None else 0
    assert layer.send(envelope) == reply
    if delivered is not None:
        assert delivered() == before + 1, "the send did not reach the peer exactly once"
    assert asyncio.run(layer.asend(envelope)) == reply
    if delivered is not None:
        assert delivered() == before + 2, "the async send did not reach the peer exactly once"

    layer.stop()
    layer.stop()


class FailingLayer(Layer):
    """A layer above the bottom that fails every send whose ``target`` is in ``targets``.

    Test support for the error path: what a stack does when a layer raises.
    """

    def __init__(self, *, targets: tuple[str, ...] = (), message: str = "layer failed") -> None:
        super().__init__()
        self.targets = frozenset(targets)
        self.message = message

    def send(self, envelope: Envelope) -> Any:
        if str(envelope.meta.get("target") or "") in self.targets:
            raise StackError(self.message)
        return super().send(envelope)

    async def asend(self, envelope: Envelope) -> Any:
        if str(envelope.meta.get("target") or "") in self.targets:
            raise StackError(self.message)
        return await super().asend(envelope)

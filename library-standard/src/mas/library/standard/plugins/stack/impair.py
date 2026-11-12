#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Impair layers: delay, drop, fail, corrupt, replay.

Each layer sits above the next call. ``seed`` (default 0) makes the random
ones repeatable; two layers impaired with the same seed see the same draws.
``delay``, ``drop`` and ``fail`` also wrap llm and tool invokes.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any, ClassVar

from mas.library.standard.lib.stack import Envelope, Layer
from mas.library.standard.lib.stack.errors import StackSendError
from mas.runtime.layers.base import LayerBase
from mas.runtime.layers.lifecycle import Lifecycle, call_start, call_stop
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


def _finite_float(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number")
    return number


class ImpairLayer(Layer, LayerBase):
    """A layer above the next call. Subclasses override the hook they impair."""

    needs_lower: ClassVar[bool] = True
    kind: ClassVar[str] = ""
    allowed: ClassVar[frozenset[str]] = frozenset({"seed"})
    per: ClassVar[str] = "destination"
    call_kinds: ClassVar[tuple[str, ...]] = ("send",)
    priority: ClassVar[int] = 10
    inner: Any = None

    def __init__(self, **params: Any) -> None:
        extra = sorted(set(params) - self.allowed)
        if extra:
            raise ValueError(f"impair {self.kind} unknown params {extra}")
        self._check(params)
        super().__init__(**params)
        self._params = dict(params)
        self._rng = random.Random(params.get("seed", 0))
        self.inner = None
        self.middleware_id = self.kind

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "ImpairLayer":
        wrapped = cls(**dict(params or {}))
        wrapped.bind(inner)
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    def bind(self, lower: Any) -> None:
        self._lower = lower
        self.inner = lower

    def capabilities(self) -> dict[str, bool]:
        if isinstance(self.inner, Layer):
            return super().capabilities()
        below = self.inner.capabilities() if isinstance(self.inner, Lifecycle) else {}
        return {**below, **type(self).CAPABILITIES}

    def start(self) -> None:
        if self._started:
            return
        call_start(self.inner)  # a lower Layer is not a Lifecycle: the stack starts it
        super().start()

    def stop(self) -> None:
        if self._stopped:
            return
        super().stop()
        call_stop(self.inner)

    def _check(self, params: dict[str, Any]) -> None:
        """Refuse params this kind cannot use. The default accepts any allowed key."""

    def _before(self) -> None:
        """Runs before the send on the sync path; may raise or block."""

    async def _abefore(self) -> None:
        """Runs before the send on the async path; may raise or wait."""

    def _dropped(self) -> bool:
        return False

    def _transform(self, envelope: Envelope) -> Envelope:
        return envelope

    def _repeats(self) -> int:
        return 1

    def _drop_error(self) -> Exception:
        return StackSendError("dropped")

    def send(self, envelope: Envelope) -> Any:
        self._before()
        if self._dropped():
            raise self._drop_error()
        env = self._transform(envelope)
        result = None
        for _ in range(self._repeats()):
            result = self._require().send(env)
        return result

    async def asend(self, envelope: Envelope) -> Any:
        await self._abefore()
        if self._dropped():
            raise self._drop_error()
        env = self._transform(envelope)
        result = None
        for _ in range(self._repeats()):
            result = await self._require().asend(env)
        return result

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        from mas.runtime.engine.call_kinds import call_kind_matches

        if not call_kind_matches(io.op, self.call_kinds):
            return self.inner.invoke(io)
        self._before()
        if self._dropped():
            raise self._drop_error()
        result = None
        for _ in range(self._repeats()):
            result = self.inner.invoke(io)
        return result

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        from mas.runtime.engine.call_kinds import call_kind_matches

        if not call_kind_matches(io.op, self.call_kinds):
            return await self.inner.ainvoke(io)
        await self._abefore()
        if self._dropped():
            raise self._drop_error()
        result = None
        for _ in range(self._repeats()):
            result = await self.inner.ainvoke(io)
        return result


class DelayLayer(ImpairLayer):
    """Waits before every call: ``seconds`` or ``ms`` (not both), 50 ms when neither is given."""

    kind = "delay"
    allowed = frozenset({"seconds", "ms", "seed"})
    call_kinds = ("send", "llm", "tool")

    def _check(self, params: dict[str, Any]) -> None:
        if "seconds" in params and "ms" in params:
            raise ValueError("impair delay takes seconds or ms, not both")
        for key in ("seconds", "ms"):
            if key in params and _finite_float(params[key], f"impair delay {key}") < 0:
                raise ValueError(f"impair delay {key} must be a number >= 0")

    def _delay_s(self) -> float:
        if "seconds" in self._params:
            return float(self._params["seconds"])
        if "ms" in self._params:
            return float(self._params["ms"]) / 1000.0
        return 0.05

    def _before(self) -> None:
        time.sleep(self._delay_s())

    async def _abefore(self) -> None:
        await asyncio.sleep(self._delay_s())


class DropLayer(ImpairLayer):
    """Loses a call with ``probability`` (default 1): the caller sees a delivery failure."""

    kind = "drop"
    allowed = frozenset({"probability", "seed"})
    call_kinds = ("send", "llm", "tool")

    def _check(self, params: dict[str, Any]) -> None:
        if "probability" in params:
            probability = _finite_float(params["probability"], "impair drop probability")
            if probability < 0 or probability > 1:
                raise ValueError("impair drop probability must be a number between 0 and 1")

    def _dropped(self) -> bool:
        return self._rng.random() < float(self._params.get("probability", 1.0))


class FailLayer(ImpairLayer):
    """Fails every call with ``message`` (default ``impaired``)."""

    kind = "fail"
    allowed = frozenset({"message", "seed"})
    call_kinds = ("send", "llm", "tool")

    def _fail(self) -> None:
        raise StackSendError(str(self._params.get("message") or "impaired"))

    def _before(self) -> None:
        self._fail()

    async def _abefore(self) -> None:
        self._fail()


class CorruptLayer(ImpairLayer):
    """Appends ``suffix`` (default ``#``) to a text or bytes payload. Any other payload passes untouched."""

    kind = "corrupt"
    allowed = frozenset({"suffix", "seed"})
    call_kinds = ("send",)

    def _transform(self, envelope: Envelope) -> Envelope:
        payload = envelope.payload
        if not isinstance(payload, (str, bytes)):
            return envelope
        suffix = str(self._params["suffix"] if "suffix" in self._params else "#")
        if isinstance(payload, bytes):
            return envelope.with_(payload=payload + suffix.encode("utf-8"))
        return envelope.with_(payload=f"{payload}{suffix}")


class ReplayLayer(ImpairLayer):
    """Sends the same message ``times`` times (default 2, at most 32) and returns the last reply."""

    kind = "replay"
    allowed = frozenset({"times", "seed"})
    call_kinds = ("send",)

    def _check(self, params: dict[str, Any]) -> None:
        if "times" not in params:
            return
        times = params["times"]
        message = "impair replay times must be an integer from 1 to 32"
        if isinstance(times, bool):
            raise ValueError(message)
        try:
            count = int(times)
        except (TypeError, ValueError) as exc:
            raise ValueError(message) from exc
        if count != times or count < 1 or count > 32:
            raise ValueError(message)

    def _repeats(self) -> int:
        return int(self._params.get("times", 2))

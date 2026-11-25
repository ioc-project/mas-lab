#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra middleware: pace live LLM_CALL after cache / routing."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.runtime.engine.ainvoke import ainvoke_call, apreview_call
from mas.runtime.engine.call_kinds import call_kind_matches
from mas.runtime.engine.llm_scheduler import (
    SchedulerPolicy,
    acquire_async,
    infer_model,
    infer_provider,
    release,
)
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

_REFUSED_CODES = frozenset({"RATE_LIMIT", "HTTP_429", "429"})


def call_was_refused(*, result: Any = None, exc: BaseException | None = None) -> bool:
    """True for a 429 / rate-limit, or a call that succeeded only after the retry layer re-issued it after one."""
    if exc is not None:
        code = str(getattr(exc, "code", "") or "").upper()
        if code in _REFUSED_CODES:
            return True
        klass = getattr(exc, "failure_class", None)
        klass_s = str(getattr(klass, "value", klass) or "").lower()
        if klass_s in {"rate_limit", "refused"}:
            return True
    if result is None:
        return False
    code = str(getattr(result, "failure_code", "") or "").upper()
    if code in _REFUSED_CODES:
        return True
    # A call the retry layer had to repeat counts only when the failure it recovered from was a
    # refusal: a retried timeout says nothing about the endpoint's rate limit.
    klass = str(getattr(result, "failure_class", "") or "").lower()
    return int(getattr(result, "retry_attempts", 0) or 0) > 0 and klass in {"rate_limit", "refused"}


def _int_param(params: dict[str, Any], key: str, default: int, *, minimum: int) -> int:
    raw = params.get(key)
    if raw is None:
        return default
    return max(minimum, int(raw))


def _float_param(params: dict[str, Any], key: str, default: float, *, minimum: float) -> float:
    raw = params.get(key)
    if raw is None:
        return default
    return max(minimum, float(raw))


@dataclass
class LlmSchedulerMiddleware(LayerLifecycle):
    """Acquire a shared live-LLM slot, then call ``inner``.

    Stack this *inside* ``llm_cache`` (cache outermost in the infra pipeline)
    so replay / cache hits never wait on the limiter. ``max_in_flight`` is
    per leaf: each routed provider wraps its own instance.

    A refused call lowers this instance's in-flight cap; a clean call raises
    it toward ``max_in_flight``. A streak of refusals pauses the leaf.
    """

    per = "destination"
    call_kinds = ("llm",)
    priority = 40
    stores_llm_responses = False
    paces_llm_calls = True

    inner: Any
    middleware_id: str = "llm_scheduler"
    policy: SchedulerPolicy | None = None
    min_in_flight: int = 1
    increase: int = 1
    decrease: float = 2.0
    breaker_streak: int = 3
    cooldown_s: float = 5.0
    _effective: int = 0
    _streak: int = 0
    _paused_until: float = 0.0

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "LlmSchedulerMiddleware":
        raw = dict(params or {})
        policy = SchedulerPolicy.from_params(raw)
        wrapped = cls(
            inner=inner,
            policy=policy,
            min_in_flight=_int_param(raw, "min_in_flight", 1, minimum=0),
            increase=_int_param(raw, "increase", 1, minimum=1),
            decrease=_float_param(raw, "decrease", 2.0, minimum=1.0),
            breaker_streak=_int_param(raw, "breaker_streak", 3, minimum=1),
            cooldown_s=_float_param(raw, "cooldown_s", 5.0, minimum=0.0),
            _effective=policy.max_in_flight,
        )
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    def _active_policy(self) -> SchedulerPolicy | None:
        pol = self.policy
        if pol is None or pol.max_in_flight <= 0:
            return pol
        cap = max(1, min(pol.max_in_flight, self._effective if self._effective > 0 else self.min_in_flight))
        if cap == pol.max_in_flight:
            return pol
        return replace(pol, max_in_flight=cap)

    async def _wait_if_paused(self) -> None:
        import asyncio

        while self._paused_until > time.monotonic():
            await asyncio.sleep(min(0.05, self._paused_until - time.monotonic()))
        if self._paused_until:
            self._paused_until = 0.0
            self._streak = 0
            if self._effective <= 0:
                self._effective = max(1, self.min_in_flight)

    def observe(self, *, refused: bool) -> None:
        """Lower in-flight on a refusal; raise it while calls stay clean."""
        pol = self.policy
        if pol is None or pol.max_in_flight <= 0:
            return
        now = time.monotonic()
        if now < self._paused_until:
            return
        if refused:
            self._streak += 1
            lowered = int(self._effective / self.decrease) if self.decrease else self.min_in_flight
            self._effective = max(self.min_in_flight, lowered)
            if self._streak >= self.breaker_streak:
                self._paused_until = now + self.cooldown_s
                self._effective = 0
            return
        self._streak = 0
        current = self._effective if self._effective > 0 else max(1, self.min_in_flight)
        self._effective = min(pol.max_in_flight, current + self.increase)

    async def aexchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        pol = self.policy
        label = (
            f"[llm_scheduler max_in_flight={pol.max_in_flight} rpm={pol.requests_per_minute}]"
            if pol
            else "[llm_scheduler]"
        )
        head = await apreview_call(self.inner, op, correlation_id=correlation_id)
        return f"{label}\n{head}".strip() if head else label

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not call_kind_matches(io.op, self.call_kinds) or self.policy is None or not self.policy.enabled():
            return await ainvoke_call(self.inner, io)
        await self._wait_if_paused()
        pol = self._active_policy() or self.policy
        key = await acquire_async(
            model=infer_model(self.inner),
            provider=infer_provider(self.inner, self.policy.provider),
            user=self.policy.user,
            policy=pol,
        )
        try:
            result = await ainvoke_call(self.inner, io)
            self.observe(refused=call_was_refused(result=result))
            return result
        except Exception as exc:
            self.observe(refused=call_was_refused(exc=exc))
            raise
        finally:
            release(key, policy=pol)

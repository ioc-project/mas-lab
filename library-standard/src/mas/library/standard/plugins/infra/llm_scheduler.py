#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra middleware: pace live LLM_CALL after cache / routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mas.runtime.engine.call_kinds import call_kind_matches
from mas.runtime.engine.llm_scheduler import (
    SchedulerPolicy,
    acquire,
    acquire_async,
    infer_model,
    infer_provider,
    release,
)
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class LlmSchedulerMiddleware:
    """Acquire a shared live-LLM slot, then call ``inner``.

    Stack this *inside* ``llm_cache`` (cache outermost in the infra pipeline)
    so replay / cache hits never wait on the limiter. ``max_in_flight`` is
    per leaf: each routed provider wraps its own instance.
    """

    per = "destination"
    call_kinds = ("llm",)
    stores_llm_responses = False
    paces_llm_calls = True

    inner: Any
    middleware_id: str = "llm_scheduler"
    policy: SchedulerPolicy | None = None

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "LlmSchedulerMiddleware":
        policy = SchedulerPolicy.from_params(params)
        wrapped = cls(inner=inner, policy=policy)
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        pol = self.policy
        label = (
            f"[llm_scheduler max_in_flight={pol.max_in_flight} rpm={pol.requests_per_minute}]"
            if pol
            else "[llm_scheduler]"
        )
        head = str(self.inner.exchange_preview(op, correlation_id=correlation_id) or "")
        return f"{label}\n{head}".strip() if head else label

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not call_kind_matches(io.op, self.call_kinds) or self.policy is None or not self.policy.enabled():
            return self.inner.invoke(io)
        key = acquire(
            model=infer_model(self.inner),
            provider=infer_provider(self.inner, self.policy.provider),
            user=self.policy.user,
            policy=self.policy,
        )
        try:
            return self.inner.invoke(io)
        finally:
            release(key, policy=self.policy)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not call_kind_matches(io.op, self.call_kinds) or self.policy is None or not self.policy.enabled():
            return await self.inner.ainvoke(io)
        key = await acquire_async(
            model=infer_model(self.inner),
            provider=infer_provider(self.inner, self.policy.provider),
            user=self.policy.user,
            policy=self.policy,
        )
        try:
            return await self.inner.ainvoke(io)
        finally:
            release(key, policy=self.policy)

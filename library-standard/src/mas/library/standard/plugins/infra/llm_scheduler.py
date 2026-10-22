#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra middleware: pace live LLM_CALL after cache / routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mas.runtime.engine.llm_scheduler import (
    SchedulerPolicy,
    acquire,
    acquire_async,
    infer_model,
    infer_provider,
    install_policy,
    release,
)
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class LlmSchedulerMiddleware:
    """Acquire a shared live-LLM slot, then call ``inner``.

    Stack this *inside* ``llm_cache`` (cache outermost in the infra pipeline)
    so replay / cache hits never wait on the limiter.
    """

    inner: Any
    middleware_id: str = "llm_scheduler"
    policy: SchedulerPolicy | None = None

    @classmethod
    def wrap(cls, inner: Any, params: dict[str, Any] | None = None) -> "LlmSchedulerMiddleware":
        policy = SchedulerPolicy.from_params(params)
        install_policy(policy)
        return cls(inner=inner, policy=policy)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        preview = getattr(self.inner, "exchange_preview", None)
        pol = self.policy
        label = (
            f"[llm_scheduler max_in_flight={pol.max_in_flight} rpm={pol.requests_per_minute}]"
            if pol
            else "[llm_scheduler]"
        )
        if callable(preview):
            head = str(preview(op, correlation_id=correlation_id) or "")
            return f"{label}\n{head}".strip()
        return label

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op != "LLM_CALL" or self.policy is None or not self.policy.enabled():
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
        if io.op != "LLM_CALL" or self.policy is None or not self.policy.enabled():
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

    def reset_turn_state(self) -> None:
        reset_fn = getattr(self.inner, "reset_turn_state", None)
        if callable(reset_fn):
            reset_fn()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

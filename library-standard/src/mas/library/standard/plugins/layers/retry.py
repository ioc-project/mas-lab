#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Retry layer — re-issues llm and tool calls. The engine does not retry by itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.runtime.engine.call_kinds import call_kind_matches
from mas.runtime.layers.target import call_is_idempotent, call_target, classifier_for
from mas.runtime.reliability.policy import RetryPolicy
from mas.runtime.reliability.retry import acall_with_retry, call_with_retry
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


def _policy_for(params: dict[str, Any] | None, call_kinds: tuple[str, ...] | None) -> RetryPolicy:
    kinds = tuple(call_kinds or ())
    default = RetryPolicy.tools_default() if kinds == ("tool",) else RetryPolicy.llm_default()
    return RetryPolicy.from_mapping(params, field_name="retry", default=default)


@dataclass
class RetryLayer(LayerLifecycle):
    """Re-issue the inner call up to ``max_attempts``. Logs on ``mas.runtime.reliability``."""

    per = "destination"
    call_kinds = ("llm", "tool")
    priority = 80

    inner: Any = None
    policy: RetryPolicy = field(default_factory=RetryPolicy.llm_default)
    middleware_id: str = "retry"

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "RetryLayer":
        wrapped = cls(inner=inner, policy=_policy_for(params, call_kinds))
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        head = str(self.inner.exchange_preview(op, correlation_id=correlation_id) or "")
        label = f"[retry max_attempts={self.policy.max_attempts}]"
        return f"{label}\n{head}".strip() if head else label

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not call_kind_matches(io.op, self.call_kinds):
            return self.inner.invoke(io)
        return call_with_retry(
            lambda: self.inner.invoke(io),
            policy=self.policy,
            classify=classifier_for(io),
            target=call_target(self.inner, io) or "",
            idempotent=call_is_idempotent(self.inner, io),
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not call_kind_matches(io.op, self.call_kinds):
            return await self.inner.ainvoke(io)
        return await acall_with_retry(
            lambda: self.inner.ainvoke(io),
            policy=self.policy,
            classify=classifier_for(io),
            target=call_target(self.inner, io) or "",
            idempotent=call_is_idempotent(self.inner, io),
        )

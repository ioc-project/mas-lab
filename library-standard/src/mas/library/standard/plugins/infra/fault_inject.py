#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fault-injection infra middleware — chaos-lite in front of any provider."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from mas.runtime.engine.call_kinds import call_kind_matches
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class FaultInjectMiddleware:
    """Inject random LLM failures — chaos-lite in front of any provider."""

    per = "destination"
    call_kinds = ("llm",)
    stores_llm_responses = False

    inner: Any
    middleware_id: str = "fault_inject"
    rate: float = 0.0
    status_codes: list[int] = field(default_factory=lambda: [503])
    message: str = "injected fault"

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "FaultInjectMiddleware":
        params = dict(params or {})
        rate = float(params.get("rate") or params.get("failure_rate") or 0.0)
        codes_raw = params.get("status_codes") or params.get("errors") or [503]
        codes = [int(c) for c in codes_raw] if isinstance(codes_raw, list) else [503]
        msg = str(params.get("message") or "injected fault")
        wrapped = cls(
            inner=inner,
            rate=max(0.0, min(1.0, rate)),
            status_codes=codes,
            message=msg,
        )
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        head = str(self.inner.exchange_preview(op, correlation_id=correlation_id) or "")
        label = f"[fault_inject middleware rate={self.rate}]"
        return f"{label}\n{head}".strip() if head else label

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if call_kind_matches(io.op, self.call_kinds) and self.rate > 0 and random.random() < self.rate:
            code = self.status_codes[0] if self.status_codes else 503
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=f"{code}: {self.message}",
            )
        return self.inner.invoke(io)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if call_kind_matches(io.op, self.call_kinds) and self.rate > 0 and random.random() < self.rate:
            code = self.status_codes[0] if self.status_codes else 503
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=f"{code}: {self.message}",
            )
        return await self.inner.ainvoke(io)

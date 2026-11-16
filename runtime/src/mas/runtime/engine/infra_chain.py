#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bidirectional infra middleware chain (runtime-local; mirrors ctl pipeline_chain)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from uuid import uuid4

MiddlewareHandler = Callable[["InfraChainContext"], Optional[dict[str, Any]]]


@dataclass
class InfraChainContext:
    query: dict[str, Any]
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    target: str = "LLM_CALL"
    entry_id: Optional[str] = None


@dataclass
class BidirectionalInfraPipeline:
    forward_handlers: list[tuple[str, MiddlewareHandler]]
    reply_handlers: list[tuple[str, MiddlewareHandler]] = field(default_factory=list)

    def backward_reply(self, ctx: InfraChainContext, reply: dict[str, Any]) -> dict[str, Any]:
        current = reply
        handlers = self.reply_handlers or list(reversed(self.forward_handlers))
        for entry_id, handler in handlers:
            ctx.entry_id = entry_id
            ctx.query = dict(current)
            transformed = handler(ctx)
            if transformed is not None:
                current = transformed
        return current

    @classmethod
    def from_pipeline_steps(
        cls,
        steps: list[dict[str, Any]],
        *,
        handlers: Optional[dict[str, MiddlewareHandler]] = None,
    ) -> BidirectionalInfraPipeline:
        registry = handlers or {}
        forward: list[tuple[str, MiddlewareHandler]] = []
        for step in steps:
            name = str(step.get("layer") or step.get("ref") or "layer")
            forward.append((name, registry.get(name, _passthrough)))
        return cls(forward_handlers=forward)


def _passthrough(_ctx: InfraChainContext) -> None:
    return None

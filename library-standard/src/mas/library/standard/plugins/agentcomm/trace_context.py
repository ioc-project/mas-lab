#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Decorator: mint a child W3C ``traceparent`` on each send."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from typing import Any

from mas.library.standard.lib.comm.headers import merge_headers
from mas.library.standard.plugins.agentcomm.layer import BoundLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, SendContext

_TRACEPARENT = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
    re.IGNORECASE,
)


def _new_ids() -> tuple[str, str]:
    return secrets.token_hex(16), secrets.token_hex(8)


def child_traceparent(parent: str | None) -> str:
    """Same trace id as a valid ``parent`` with a new span id, or a new root.

    The span id is a correlation id for this send, not an OpenTelemetry span.
    Version ``ff`` and all-zero trace or span ids are not valid parents.
    """
    if parent:
        match = _TRACEPARENT.match(parent.strip())
        if match:
            version, trace_id, span_id, flags = (part.lower() for part in match.groups())
            if version != "ff" and trace_id != "0" * 32 and span_id != "0" * 16:
                return f"{version}-{trace_id}-{secrets.token_hex(8)}-{flags}"
    trace_id, span_id = _new_ids()
    return f"00-{trace_id}-{span_id}-01"


def _with_trace(headers: Mapping[str, str] | None) -> dict[str, str]:
    merged = merge_headers(headers)
    merged["traceparent"] = child_traceparent(merged.get("traceparent"))
    return merged


class TraceContextLayer(BoundLayer):
    """Wraps a lower link and injects a child correlation id."""

    def __init__(self, lower: AgentCommContract | None = None, **params: Any) -> None:
        super().__init__(**params)
        if lower is not None:
            self.bind(lower)

    def send(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        incoming = headers if headers is not None else (context.headers if context else None)
        return super().send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=_with_trace(incoming),
            context=context,
        )

    async def asend(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        incoming = headers if headers is not None else (context.headers if context else None)
        return await super().asend(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=_with_trace(incoming),
            context=context,
        )

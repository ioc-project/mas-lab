#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The agent-communication call signature of a stack: ``send(target, task, ...)`` as an ``Envelope`` and back.

Links and decorators written before layers reach a stack through ``legacy.adapt_legacy``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mas.library.standard.lib.comm.send_context import align_send
from mas.library.standard.lib.stack.envelope import Envelope
from mas.library.standard.lib.stack.layer import Handler
from mas.runtime.boundary.agentcomm.protocol import SendContext

_META_KEYS = ("correlation_id", "caller_call_id", "context_id")


def to_envelope(
    target_agent_id: str,
    task: str,
    *,
    correlation_id: int = 0,
    caller_call_id: str = "",
    context_id: str = "",
    headers: dict[str, str] | None = None,
    context: SendContext | None = None,
) -> Envelope:
    """The call, as the message a stack carries."""
    outgoing, ctx = align_send(headers, context)
    return Envelope(
        payload=task,
        headers=outgoing,
        meta={
            "target": target_agent_id,
            "correlation_id": correlation_id,
            "caller_call_id": caller_call_id,
            "context_id": context_id,
            "intent": ctx.intent,
            "sender": ctx.sender,
            "mode": ctx.mode,
        },
    )


def inbound_envelope(
    task: str,
    *,
    sender: str = "",
    intent: str = "task",
    correlation_id: int = 0,
    caller_call_id: str = "",
    context_id: str = "",
    headers: dict[str, str] | None = None,
) -> Envelope:
    """A received request, as the envelope a transport hands to its inbound chain."""
    return Envelope(
        payload=task,
        headers=dict(headers or {}),
        meta={
            "sender": sender,
            "intent": intent,
            "correlation_id": correlation_id,
            "caller_call_id": caller_call_id,
            "context_id": context_id,
        },
    )


def turn_handler(run_turn: Callable[..., Any], agent_id: str) -> Handler:
    """The top of an inbound chain: the session turn of ``agent_id``, called as a link calls it."""

    def handle(envelope: Envelope) -> Any:
        meta = envelope.meta
        return run_turn(
            agent_id,
            str(envelope.payload if envelope.payload is not None else ""),
            int(meta.get("correlation_id") or 0),
            str(meta.get("caller_call_id") or ""),
            str(meta.get("context_id") or ""),
        )

    return handle


def call_kwargs(envelope: Envelope) -> dict[str, Any]:
    """The ``send(target, task, **kwargs)`` keywords for an envelope."""
    meta = envelope.meta
    headers = dict(envelope.headers)
    return {
        **{key: meta.get(key, 0 if key == "correlation_id" else "") for key in _META_KEYS},
        "headers": headers,
        "context": SendContext(
            headers=headers,
            intent=str(meta.get("intent") or ""),
            sender=str(meta.get("sender") or ""),
            mode=str(meta.get("mode") or ""),
        ),
    }

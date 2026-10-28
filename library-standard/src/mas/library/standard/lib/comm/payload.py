#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound send payload: size bound, intent set, and a model-only label."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator

from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.boundary.delegation.policy import SEND_INTENTS

MAX_MESSAGE_SIZE = 1_048_576
TASK_ONLY_INTENTS = frozenset({"task"})
INFORM_ASK_INTENTS = frozenset({"task", "inform", "ask"})
PUBLISH_INTENT = "publish"
UNTRUSTED_INTENTS = frozenset({"inform", "ask", PUBLISH_INTENT})


@dataclass(frozen=True)
class InboundSend:
    """Structured inbound fields. The text label is a hint to the model only."""

    sender: str
    intent: str


_inbound: ContextVar[InboundSend | None] = ContextVar("mas_inbound_send", default=None)


def inbound_send() -> InboundSend | None:
    """Structured sender and intent for this hop. A seam: nothing reads it yet."""
    return _inbound.get()


@contextmanager
def bound_inbound(*, sender: str, intent: str) -> Iterator[InboundSend]:
    record = InboundSend(sender=str(sender or ""), intent=str(intent or ""))
    token: Token[InboundSend | None] = _inbound.set(record)
    try:
        yield record
    finally:
        _inbound.reset(token)


def normalize_send_intent(raw: object) -> str:
    kind = str(raw or "task").strip().lower() or "task"
    if kind not in SEND_INTENTS:
        raise AgentCommError(f"unknown intent {raw!r}")
    return kind


def intents_honoured_by(communication: Any) -> frozenset[str]:
    hook = getattr(communication, "honoured_send_intents", None)
    if callable(hook):
        return _as_intent_set(hook())
    flags = getattr(communication, "HONOURED_SEND_INTENTS", None)
    if flags is not None:
        return _as_intent_set(flags)
    return TASK_ONLY_INTENTS


def _as_intent_set(raw: Iterable[object]) -> frozenset[str]:
    return frozenset(str(item) for item in raw)


def require_honoured_intent(communication: Any, intent: str) -> str:
    kind = normalize_send_intent(intent)
    if kind not in intents_honoured_by(communication):
        raise AgentCommError(f"intent {kind!r} is not supported")
    return kind


def check_message_size(text: object, *, max_size: int = MAX_MESSAGE_SIZE) -> None:
    if not isinstance(text, str):
        raise AgentCommError("message must be a string")
    if len(text.encode("utf-8")) > max_size:
        raise AgentCommError("message exceeds max size")


def label_untrusted_peer(text: str, *, sender: str, intent: str = "") -> str:
    who = str(sender or "").strip() or "unknown"
    head = f"[untrusted peer message from {who}"
    if intent:
        head += f" intent={intent}"
    return f"{head}]\n{text}"


def inbound_payload(text: str, *, sender: str, intent: str) -> str:
    if intent in UNTRUSTED_INTENTS:
        return label_untrusted_peer(text, sender=sender, intent=intent)
    return text


def send_result(intent: str, result: str) -> str:
    return "delivered" if intent == "inform" else result

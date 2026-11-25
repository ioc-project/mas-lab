from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SendContext:
    """Per-send envelope. The caller may set headers, intent, and steer mode; only the link sets sender.

    ``metadata`` is protocol-level data a link may carry next to the message (A2A message metadata): namespaced
    keys, set by a layer on the way down, never by a caller. ``session_hook`` is an opaque object a layer hands to
    the link so it can take part in the peer's session setup (the library's ``SessionHook``); the runtime never
    reads it. ``message_id`` is the id of the message a layer above fixed, so a resent message keeps it.
    """

    headers: Mapping[str, str] = field(default_factory=dict)
    intent: str = ""
    sender: str = ""
    mode: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    session_hook: Any = None
    message_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def __deepcopy__(self, memo: dict[int, Any]) -> SendContext:
        copied = SendContext(
            headers=dict(self.headers),
            intent=self.intent,
            sender=self.sender,
            mode=self.mode,
            metadata=dict(self.metadata),
            session_hook=self.session_hook,
            message_id=self.message_id,
        )
        memo[id(self)] = copied
        return copied

    def __reduce__(self) -> tuple[type[SendContext], tuple[dict[str, str], str, str, str, dict[str, Any], None, str]]:
        # a session hook belongs to one live send and does not travel
        return (
            type(self),
            (dict(self.headers), self.intent, self.sender, self.mode, dict(self.metadata), None, self.message_id),
        )


_current_transport: ContextVar[str] = ContextVar(
    "mas_agent_comm_transport",
    default="",
)


def set_current_transport(protocol: str) -> None:
    _current_transport.set(protocol)


def consume_current_transport() -> str:
    protocol = _current_transport.get()
    _current_transport.set("")
    return protocol


class AgentCommError(RuntimeError):
    """Raised when an agent communication plugin cannot complete a send."""


class LayerRefusal(AgentCommError):
    """A layer refused a call before anything was sent. The text is a value-free code and is the whole story: a
    caller reports it as it is, with no traceback."""


@dataclass(frozen=True)
class InboundSend:
    """Structured inbound fields. The text label is a hint to the model only."""

    sender: str
    intent: str


_inbound: ContextVar[InboundSend | None] = ContextVar("mas_inbound_send", default=None)


def inbound_send() -> InboundSend | None:
    """Structured sender and intent for this hop."""
    return _inbound.get()


@contextmanager
def bound_inbound(*, sender: str, intent: str) -> Iterator[InboundSend]:
    record = InboundSend(sender=str(sender or ""), intent=str(intent or ""))
    token: Token[InboundSend | None] = _inbound.set(record)
    try:
        yield record
    finally:
        _inbound.reset(token)


@runtime_checkable
class AgentCommContract(Protocol):
    """Outbound send to a peer after delegation has already been decided.

    Inbound user turns are not this contract. They are
    ``ControlContract.send_message`` (A2A ``message/send``). Intent
    ``steer`` does not run a peer turn; a link that honours it maps the
    target to a control session, re-scopes the control handle to the
    sender, and calls ``ControlContract.steer``. The send
    ``context_id`` is not that session.

    ``headers`` is an optional string-to-string map. A link carries it in
    the envelope and restores it around the receiver. Only
    ``traceparent``, ``tracestate``, and ``baggage`` ride a later send.
    ``mas.*`` keys stay on this hop.

    ``context`` is the per-send envelope. The caller may set headers and
    intent; only the link sets sender.
    """

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
    ) -> str: ...

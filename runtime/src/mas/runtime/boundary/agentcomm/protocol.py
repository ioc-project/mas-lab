from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SendContext:
    """Per-send envelope. The caller may set headers and intent; only the link sets sender."""

    headers: Mapping[str, str] = field(default_factory=dict)
    intent: str = ""
    sender: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    def __deepcopy__(self, memo: dict[int, Any]) -> SendContext:
        copied = SendContext(headers=dict(self.headers), intent=self.intent, sender=self.sender)
        memo[id(self)] = copied
        return copied

    def __reduce__(self) -> tuple[type[SendContext], tuple[dict[str, str], str, str]]:
        return (type(self), (dict(self.headers), self.intent, self.sender))


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


@runtime_checkable
class AgentCommContract(Protocol):
    """Outbound send to a peer after delegation has already been decided.

    Inbound user turns are not this contract. They are
    ``ControlContract.send_message`` (A2A ``message/send``). There is no
    steer RPC here; mid-token amend exists only on the control protocol.

    ``headers`` is an optional string-to-string map. A link carries it in
    the envelope and restores it around the receiver. Only
    ``traceparent``, ``tracestate``, and ``baggage`` ride a later send.
    ``mas.*`` keys stay on this hop.

    ``context`` is the per-send envelope. The caller may set headers and
    intent; only the link sets sender.
    """

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
    ) -> str: ...

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
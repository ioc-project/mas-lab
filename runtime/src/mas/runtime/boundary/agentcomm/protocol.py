from __future__ import annotations

from contextvars import ContextVar
from typing import Protocol, runtime_checkable

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
    ) -> str: ...
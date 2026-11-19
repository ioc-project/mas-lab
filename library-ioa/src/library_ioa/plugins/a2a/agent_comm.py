from __future__ import annotations

from typing import Any

from mas.library.standard.lib.stack.layer import LinkIdentity
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext

from library_ioa.plugins.a2a.client import A2AClient


class A2AAgentComm(LinkIdentity, AgentCommContract):
    """Reach a remote peer using the A2A protocol and official SDK client.

    A2A has no inform/ask wire. Only ``task`` is honoured. Other intents
    raise rather than going out as an ordinary task.
    """

    HONOURED_SEND_INTENTS = frozenset({"task"})
    plugin_id = "a2a"

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        self.endpoint = url
        self._client = A2AClient(url=url, headers=headers, timeout=timeout)
        self._client.peer_id = url

    async def aprobe(self, target_agent_id: str) -> bool:
        """Fetch the remote agent card afresh: the peer answers, or this raises."""
        del target_agent_id
        try:
            await self._client.aget_agent_card(fresh=True)
        except Exception as exc:
            self._client.reset()
            raise AgentCommError(str(exc)) from exc
        return True

    def stop(self) -> None:
        self._client.close()

    def honoured_send_intents(self) -> frozenset[str]:
        return self.HONOURED_SEND_INTENTS

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
        kind = str(getattr(context, "intent", None) or "task").strip().lower() or "task"
        if kind not in self.HONOURED_SEND_INTENTS:
            raise AgentCommError(f"intent {kind!r} is not supported")
        # The sender is what the link says it speaks for. ``context.sender`` is caller supplied and not trusted.
        sender = str(self.agent_id or "")
        del headers, context
        from library_ioa.plugins.a2a.hooks import bound_identity

        try:
            with bound_identity(sender, str(target_agent_id or "")):
                response = await self._client.asend_message(
                    task,
                    context_id=context_id,
                    metadata={
                        "mas.correlation_id": str(correlation_id),
                        "mas.caller_call_id": caller_call_id,
                    },
                )
        except Exception as exc:
            raise AgentCommError(str(exc)) from exc

        status = str(response.get("status") or "").lower()
        if status in {"failed", "error"}:
            raise AgentCommError(str(response.get("error") or "remote A2A task failed"))
        result: Any = response.get("result", response)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            message = result.get("message")
            if isinstance(message, dict):
                parts = message.get("parts") or []
                text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
                if text:
                    return text
            if isinstance(message, str):
                return message
            parts = result.get("parts") or []
            text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
            if text:
                return text
        return str(result)

from __future__ import annotations

from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext

from library_ioa.plugins.a2a.client import A2AClient


class A2AAgentComm(AgentCommContract):
    """Reach a remote peer using the A2A protocol and official SDK client."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._client = A2AClient(url=url, headers=headers, timeout=timeout)

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
        del target_agent_id, headers, context
        try:
            response = self._client.send_message(
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
                text = "".join(
                    str(part.get("text") or "")
                    for part in parts
                    if isinstance(part, dict)
                )
                if text:
                    return text
            if isinstance(message, str):
                return message
            parts = result.get("parts") or []
            text = "".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, dict)
            )
            if text:
                return text
        return str(result)

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
        return self.send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
        )

    def close(self) -> None:
        self._client.close()
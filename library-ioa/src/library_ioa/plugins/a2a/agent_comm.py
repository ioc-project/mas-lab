from __future__ import annotations

import asyncio
from typing import Any

from mas.library.standard.lib.stack.layer import LinkIdentity
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext

from library_ioa.plugins.a2a.client import A2AClient
from library_ioa.plugins.a2a.conversations import PeerConversations


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
        conversation: str = "explicit",
    ) -> None:
        if conversation not in {"explicit", "per_relationship"}:
            raise ValueError(f"Unknown A2A conversation policy: {conversation!r}")
        self._conversations = PeerConversations() if conversation == "per_relationship" else None
        self.endpoint = url
        self._client = A2AClient(url=url, headers=headers, timeout=timeout)

    def honoured_send_intents(self) -> frozenset[str]:
        return self.HONOURED_SEND_INTENTS

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
        kind = str(getattr(context, "intent", None) or "task").strip().lower() or "task"
        if kind not in self.HONOURED_SEND_INTENTS:
            raise AgentCommError(f"intent {kind!r} is not supported")
        thread = str(getattr(context, "thread", "") or "")
        if thread and context_id:
            raise AgentCommError("Use either a logical thread or an explicit context_id")
        conversations = getattr(self, "_conversations", None)
        if thread and conversations is None:
            raise AgentCommError("Named threads require automatic A2A conversation reuse")
        metadata = {
            "mas.correlation_id": str(correlation_id),
            "mas.caller_call_id": caller_call_id,
        }
        try:
            if conversations is not None and not context_id:
                response = conversations.send(self._client, target_agent_id, task, thread=thread, metadata=metadata)
            else:
                response = self._client.send_message(task, context_id=context_id, metadata=metadata)
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
        return await asyncio.to_thread(
            self.send,
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
        conversations = getattr(self, "_conversations", None)
        if conversations is not None:
            conversations.clear()

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mas.library.standard.lib.stack.layer import LinkIdentity
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext
from mas.runtime.boundary.connection import Invocation, PreparedOperation

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
        del target_agent_id, headers, context
        return self._request(
            self._client, task, context_id=context_id, correlation_id=correlation_id, caller_call_id=caller_call_id
        )

    def _request(
        self,
        client: A2AClient,
        task: str,
        *,
        context_id: str = "",
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        try:
            response = client.send_message(
                task,
                context_id=context_id,
                metadata={
                    "mas.correlation_id": str(correlation_id),
                    "mas.caller_call_id": caller_call_id,
                },
            )
        except Exception as exc:
            raise AgentCommError(str(exc)) from exc

        return self._result_text(response)

    @staticmethod
    def _result_text(response: dict[str, Any]) -> str:
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
        return self.send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
        )

    def prepare_send(
        self,
        recipient: str,
        task: str,
        *,
        context_id: str = "",
        on_error: Callable[[Exception], str] | None = None,
    ) -> PreparedOperation:
        # Capture the client as well as its selected interface. The callbacks
        # must not consult a replacement communication client after admission.
        client, request = self._client, self._request

        def call(**trace: Any) -> str:
            try:
                return request(client, task, context_id=context_id, **trace)
            except Exception as exc:
                if on_error is not None:
                    return on_error(exc)
                raise

        async def acall(**trace: Any) -> str:
            # The native explicit-context comm's asend uses the same send path.
            return call(**trace)

        return client.prepare_message(recipient, task, context_id=context_id, sender=Invocation(call, acall))

    def retire_bindings(self) -> None:
        self._client.retire_bindings()

    def close(self) -> None:
        self._client.close()

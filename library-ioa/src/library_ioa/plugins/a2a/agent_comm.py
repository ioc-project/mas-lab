from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from mas.library.standard.lib.comm.headers import merge_headers
from mas.library.standard.lib.stack import Unauthenticated
from mas.library.standard.lib.stack.layer import LinkIdentity
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext

from library_ioa.plugins.a2a.client import A2AClient, peer_error
from library_ioa.plugins.http_layer.transport import sendable

logger = logging.getLogger(__name__)


def extension_uris(card: Mapping[str, Any]) -> list[str]:
    """The URIs of the extensions an agent card declares under ``capabilities.extensions``."""
    declared = (card.get("capabilities") or {}).get("extensions") or []
    return [str(item["uri"]) for item in declared if isinstance(item, Mapping) and item.get("uri")]


def skill_ids(card: Mapping[str, Any]) -> list[str]:
    """The ids of the skills an agent card declares."""
    return [str(item["id"]) for item in card.get("skills") or [] if isinstance(item, Mapping) and item.get("id")]


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
        headers: dict[str, Any] | None = None,
        timeout: float | None = None,
        allowed_origins: list[str] | None = None,
    ) -> None:
        self.endpoint = url
        self._client = A2AClient(url=url, headers=headers, timeout=timeout, allowed_origins=allowed_origins)
        self._client.peer_id = url

    async def apeer_facts(self, target_agent_id: str) -> Mapping[str, Any]:
        """The extension URIs and skill ids the peer's agent card declares (the card is fetched once and kept).

        A card that cannot be read is no facts: the send itself reports the failure.
        """
        del target_agent_id
        try:
            card = await self._client.aget_agent_card()
        except Exception:
            logger.debug("agent card unavailable for peer facts", exc_info=True)
            return {}
        return {"extensions": extension_uris(card), "skills": skill_ids(card)}

    async def aprobe(self, target_agent_id: str) -> bool:
        """Fetch the remote agent card afresh: the peer answers, or this raises."""
        del target_agent_id
        try:
            await self._client.aget_agent_card(fresh=True)
        except Exception as exc:
            self._client.reset()
            raise peer_error(exc) from None
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
        explicit = headers if headers is not None else (dict(context.headers) if context is not None else None)
        # Hop-safe ambient keys, then this send's headers. A framing header raises before anything is sent.
        call_headers = sendable(merge_headers(explicit))
        # protocol metadata a layer set (namespaced keys) rides next to the mas.* ids; a layer's hook answers an
        # auth-required task on the task it came from
        extra = dict(context.metadata) if context is not None else {}
        hook: dict[str, Any] = {}
        if context is not None and context.session_hook is not None:
            hook["session_hook"] = context.session_hook
        if context is not None and context.message_id:
            # a layer above resends the same message after a challenge: same id, so a verifier binding to it agrees
            hook["message_id"] = context.message_id
        from library_ioa.plugins.a2a.hooks import bound_identity

        try:
            with bound_identity(sender, str(target_agent_id or "")):
                response = await self._client.asend_message(
                    task,
                    context_id=context_id,
                    headers=call_headers,
                    metadata={
                        **extra,
                        "mas.correlation_id": str(correlation_id),
                        "mas.caller_call_id": caller_call_id,
                    },
                    **hook,
                )
        except Unauthenticated:
            raise  # a layer above answers the challenge; it is not a failure of the send
        except Exception as exc:
            raise peer_error(exc) from None

        status = str(response.get("status") or "").lower()
        if status in {"failed", "error"}:
            raise AgentCommError(f"remote A2A task {status}")
        result: Any = response.get("result", response)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            task_text = _task_text(result.get("task"))
            if task_text:
                return task_text
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


def _task_text(task: Any) -> str:
    """The reply text of a task result: its artifacts, else the text of its status message."""
    if not isinstance(task, dict):
        return ""

    def parts_text(parts: Any) -> str:
        return "".join(str(p.get("text") or "") for p in parts or [] if isinstance(p, dict))

    artifacts = "".join(parts_text(a.get("parts")) for a in task.get("artifacts") or [] if isinstance(a, dict))
    if artifacts:
        return artifacts
    message = (task.get("status") or {}).get("message")
    return parts_text(message.get("parts")) if isinstance(message, dict) else ""

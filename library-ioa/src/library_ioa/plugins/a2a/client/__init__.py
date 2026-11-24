from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Any

import httpx
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict
from mas.runtime.aio import LoopCache
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

logger = logging.getLogger(__name__)

#: Task states that end a send in failure, and the word the error says. The remote's own status text is not
#: carried: it is the peer's, and an error shows only the word.
_FAILED_STATES = {
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_REJECTED: "rejected",
    TaskState.TASK_STATE_CANCELED: "canceled",
}


def _kind(response: Any) -> str:
    """Which payload a stream response carries; ``""`` when the object has no payload oneof."""
    which = getattr(response, "WhichOneof", None)
    if not callable(which):
        return ""
    try:
        return which("payload") or ""
    except (TypeError, ValueError):
        return ""


_JSONRPC_FALLBACK = re.compile(r"^JSON-RPC Error (-?\d+):")


def peer_error(exc: BaseException) -> AgentCommError:
    """What a failed A2A call may say: a closed reason and code, never the peer's message.

    The SDK turns a JSON-RPC error into an exception carrying the peer's ``message`` (``TaskNotFoundError``
    for -32001), and an HTTP error status into text holding the response; both are the peer's to write.
    Our own refusals (``AgentCommError``) are kept as they are.
    """
    from a2a.client.errors import A2AClientError, A2AClientTimeoutError, AgentCardResolutionError
    from a2a.utils.errors import A2A_ERROR_MAPPING, JSON_RPC_ERROR_CODE_MAP, A2AError
    from mas.runtime.reliability.classify import safe_exception_text

    if isinstance(exc, AgentCommError):
        return exc
    if isinstance(exc, A2AClientTimeoutError):
        return AgentCommError("remote A2A peer timed out")
    if isinstance(exc, AgentCardResolutionError):
        status = f": HTTP {exc.status_code}" if isinstance(exc.status_code, int) else ""
        return AgentCommError(f"remote A2A agent card could not be read{status}")
    rpc = _rpc_error_in_chain(exc)
    if rpc is not None:
        return rpc
    if isinstance(exc, A2AClientError):
        cause = exc.__cause__
        if isinstance(cause, httpx.HTTPError):
            return AgentCommError(f"remote A2A peer: {safe_exception_text(cause)}")
        match = _JSONRPC_FALLBACK.match(str(exc))
        if match:
            return AgentCommError(f"remote A2A peer answered JSON-RPC error {match.group(1)}")
        return AgentCommError("remote A2A peer sent an answer that is not valid A2A")
    if isinstance(exc, A2AError):
        mapping = A2A_ERROR_MAPPING.get(type(exc))
        reason = mapping.reason if mapping else type(exc).__name__
        code = JSON_RPC_ERROR_CODE_MAP.get(type(exc))
        shown = f"{reason} ({code})" if code is not None else reason
        return AgentCommError(f"remote A2A peer answered an error: {shown}")
    return AgentCommError(f"remote A2A call failed: {safe_exception_text(exc)}")


def _rpc_error(exc: BaseException) -> AgentCommError | None:
    """A slimrpc ``RpcError`` as a closed reason: the peer's message never goes in the text.

    ``UNAVAILABLE`` / ``CANCELLED`` mean the node or the session is gone (a later send reconnects).
    ``DEADLINE_EXCEEDED`` is a timeout. Any other code is an answer from a live peer.
    """
    try:
        from slim_bindings import RpcCode, RpcError
    except ImportError:
        return None
    if not isinstance(exc, RpcError):
        return None
    code = getattr(exc, "code", None)
    if code == RpcCode.DEADLINE_EXCEEDED:
        return AgentCommError("remote A2A peer timed out")
    if code in {RpcCode.UNAVAILABLE, RpcCode.CANCELLED}:
        return AgentCommError("remote A2A call failed")
    name = getattr(code, "name", None) or "UNKNOWN"
    return AgentCommError(f"remote A2A peer answered an error: {name}")


def _rpc_error_in_chain(exc: BaseException) -> AgentCommError | None:
    """The first slimrpc ``RpcError`` on ``exc`` or its cause chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found = _rpc_error(current)
        if found is not None:
            return found
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return None


def raise_if_failed(responses: list[Any]) -> None:
    """``AgentCommError`` when an answer or a streamed update says the task failed, was rejected or was canceled."""
    for response in responses:
        payload = _kind(response)
        if payload == "task":
            state = response.task.status.state
        elif payload == "status_update":
            state = response.status_update.status.state
        else:
            continue
        word = _FAILED_STATES.get(state)
        if word:
            raise AgentCommError(f"remote A2A task {word}")


def streamed_text(responses: list[Any]) -> str:
    """The text of the artifact updates and messages in a stream, in order: it ends on a status update with none."""
    out: list[str] = []
    for response in responses:
        kind = _kind(response)
        if kind == "artifact_update":
            out.extend(part.text for part in response.artifact_update.artifact.parts)
        elif kind == "message":
            out.extend(part.text for part in response.message.parts)
    return "".join(out)


def closing_text(responses: list[Any]) -> str:
    """The streamed text to reply with when the closing response carries none of its own, else ``""``."""
    streamed = streamed_text(responses)
    last = responses[-1]
    kind = _kind(last)
    bare_task = kind == "task" and not last.task.artifacts and not last.task.status.message.parts
    return streamed if streamed and (bare_task or kind not in {"task", "message"}) else ""


_SDK_NAMES = frozenset({"A2ACardResolver", "ClientCallContext", "ClientConfig", "create_client"})


def __getattr__(name: str) -> Any:
    """Resolve the SDK client names on first access so loading the plugin does not import grpc."""
    if name not in _SDK_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import a2a.client as sdk

    value = vars(sdk)[name]
    globals()[name] = value
    return value


def _sdk(name: str) -> Any:
    """A module-level SDK name: a patched or already resolved value, else the lazy import."""
    try:
        return globals()[name]
    except KeyError:
        return __getattr__(name)


class A2AClient:
    """A2A client backed exclusively by the official a2a-sdk."""

    def __init__(
        self,
        *,
        url: str | None = None,
        headers: dict[str, Any] | None = None,
        timeout: float | None = None,
        transport: str | None = None,
        allowed_origins: list[str] | None = None,
    ) -> None:
        self.url = (url or "http://127.0.0.1:8000").rstrip("/")
        self.headers = dict(headers or {})
        self.allowed_origins = list(allowed_origins or ())
        self.timeout = timeout or 10.0
        self.transport = transport
        self._agent_card_cache: dict[str, Any] | None = None
        self._connections = LoopCache()
        self.local_agent_id = ""
        self.peer_id = ""
        self.session_id = ""

    def _transport_interceptors(self) -> list[Any]:
        from mas.runtime.transport.emit import protocol_config

        cfg = protocol_config("a2a")
        if not (cfg.hooks or cfg.propagate_metadata):
            return []
        from library_ioa.plugins.a2a.hooks import A2ATransportInterceptor

        return [
            A2ATransportInterceptor(
                agent_id=self.local_agent_id,
                peer_id=self.peer_id,
                session_id=self.session_id,
            )
        ]

    def _normalize_sdk_payload(self, payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        elif hasattr(payload, "ListFields"):
            data = MessageToDict(payload, preserving_proto_field_name=True)
        elif isinstance(payload, str):
            return {"status": "completed", "result": payload}
        elif isinstance(payload, dict):
            data = payload
        else:
            return {"status": "completed", "result": str(payload)}

        if isinstance(data, dict):
            if "result" in data or "status" in data or "artifacts" in data:
                return data
            if "message" in data and isinstance(data["message"], dict):
                return {"status": "completed", "result": data["message"]}
            if "message" in data and isinstance(data["message"], str):
                return {"status": "completed", "result": data["message"]}
            return {"status": "completed", "result": data}
        return {"status": "completed", "result": data}

    def _record_agent_card(self, card: Any) -> dict[str, Any]:
        if hasattr(card, "model_dump"):
            data = card.model_dump(mode="json", exclude_none=True)
        elif hasattr(card, "ListFields"):
            data = MessageToDict(card, preserving_proto_field_name=True)
        elif isinstance(card, dict):
            data = card
        else:
            data = {"name": "remote-agent", "description": "Remote agent", "url": self.url}
        self._agent_card_cache = data
        from library_ioa.plugins.a2a.hooks import emit_agent_card

        emit_agent_card(data, side="client", agent_id=str(data.get("name") or self.peer_id or ""))
        return dict(data)

    async def _sdk_get_agent_card(self, httpx_client: httpx.AsyncClient) -> dict[str, Any]:
        resolver = _sdk("A2ACardResolver")(httpx_client=httpx_client, base_url=self.url)
        card = await resolver.get_agent_card()
        return self._record_agent_card(card)

    async def _connection(self) -> "_Connection":
        return self._connections.get(lambda: _Connection(self))

    async def _sdk_send_message(
        self,
        message: str,
        *,
        context_id: str = "",
        metadata: dict[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        call_context = self._call_context(headers)
        state = getattr(call_context, "state", None)
        try:
            payload = await self._send_once(
                message, context_id=context_id, metadata=metadata, stream=stream, call_context=call_context
            )
        except BaseException as exc:
            # a card or client kept from before the failure may be stale (the peer restarted, its card changed); a
            # task the peer answered and failed leaves the connection fine
            if isinstance(exc, Exception) and not isinstance(exc, AgentCommError):
                self._connections.drop()
            self._finish_call(state, exc)
            raise
        self._finish_call(state, None)
        return payload

    def _call_context(self, headers: Mapping[str, str] | None = None) -> Any:
        """The SDK call context of one send.

        It carries this call's headers on the request (the httpx client is shared by concurrent sends, so it never
        holds them) and, when transport hooks are on, the call's identity for the transport interceptor.
        """
        from mas.runtime.transport.emit import protocol_config

        cfg = protocol_config("a2a")
        context = _sdk("ClientCallContext")(service_parameters=dict(headers)) if headers else None
        if not (cfg.hooks or cfg.propagate_metadata):
            return context
        from library_ioa.plugins.a2a.hooks import current_identity, peer_label, start_call_state

        source, target, session_id = current_identity()
        context = context or _sdk("ClientCallContext")()
        start_call_state(
            context.state,
            key="",
            source=source or self.local_agent_id,
            target=target or peer_label(self.peer_id),
            session_id=session_id or self.session_id,
        )
        return context

    @staticmethod
    def _finish_call(state: Any, error: BaseException | None) -> None:
        if state is None:
            return
        from library_ioa.plugins.a2a.hooks import finish_call

        finish_call(state, error=error)

    async def _send_once(
        self,
        message: str,
        *,
        context_id: str,
        metadata: dict[str, str] | None,
        stream: bool,
        call_context: Any = None,
    ) -> dict[str, Any]:
        client = await (await self._connection()).client(bool(stream))
        request = SendMessageRequest(
            message=new_text_message(
                message,
                context_id=context_id or None,
                role=Role.ROLE_USER,
            )
        )
        if metadata:
            request.message.metadata.update(metadata)
        chunks: list[Any] = []
        async for chunk in client.send_message(request, context=call_context):
            chunks.append(chunk)
        raise_if_failed(chunks)
        if not chunks:
            return {"status": "completed", "result": ""}
        payload = self._normalize_sdk_payload(chunks[-1])
        payload.setdefault("status", "completed")
        streamed = closing_text(chunks)
        if streamed:
            payload["result"] = streamed
        return payload

    async def aget_agent_card(self, *, fresh: bool = False) -> dict[str, Any]:
        """The agent card, fetched once and kept; ``fresh`` fetches it again (a health check)."""
        connection = await self._connection()
        if fresh:
            return dict(await connection.fetch_card(force=True))
        return dict(connection.card_data or await connection.fetch_card())

    def reset(self) -> None:
        """Forget what this loop kept for the remote agent; the next call connects afresh. Call it from the loop."""
        self._connections.drop()

    def close(self) -> None:
        """Close the kept connections. Safe from any thread; does not wait."""
        self._connections.close()

    async def aclose(self) -> None:
        self.close()

    async def asend_message(
        self,
        message: str,
        *,
        context_id: str = "",
        metadata: dict[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Send one message; ``headers`` go on this call's HTTP request only, over the constructor's."""
        return await self._sdk_send_message(
            message, context_id=context_id, metadata=metadata, headers=headers, stream=stream
        )


class _Connection:
    """What one event loop keeps for one remote agent: an HTTP client, its card and the SDK client per binding."""

    def __init__(self, owner: A2AClient) -> None:
        from mas.runtime.secrets.transport import httpx_client_kwargs_for

        from library_ioa.utils.secret_headers import SecretHeaderHook

        self._owner = owner
        # The row's headers go on each request, revealed then. A secret one goes only to the origin of the
        # configured url (or a listed ``allowed_origins`` entry), over https unless the host is loopback: an
        # agent card names the hosts the SDK then posts to, and a card is the peer's to write.
        self._headers = SecretHeaderHook(
            owner.headers,
            owner_url=owner.url,
            allowed_origins=owner.allowed_origins,
            error=AgentCommError,
            what="a2a client",
        )
        # a loopback peer is reached without the environment's proxy: its plain-http secret headers stay local
        self._http = httpx.AsyncClient(
            timeout=owner.timeout,
            event_hooks={"request": [self._attach_headers]},
            **httpx_client_kwargs_for(owner.url),
        )
        self._card: Any = None
        self.card_data: dict[str, Any] | None = None
        self._clients: dict[bool, Any] = {}
        self._lock = asyncio.Lock()

    async def _attach_headers(self, request: httpx.Request) -> None:
        self._headers.apply(request)

    async def fetch_card(self, *, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self.card_data is None or force:
                self.card_data = await self._owner._sdk_get_agent_card(self._http)
            return self.card_data

    async def client(self, streaming: bool) -> Any:
        async with self._lock:
            found = self._clients.get(streaming)
            if found is not None:
                return found
            if self._card is None:
                resolver = _sdk("A2ACardResolver")(httpx_client=self._http, base_url=self._owner.url)
                self._card = await resolver.get_agent_card()
                if self.card_data is None:
                    self.card_data = self._owner._record_agent_card(self._card)
            owner = self._owner
            grpc_channel_factory = None
            if owner.transport == "GRPC":
                from library_ioa.plugins.a2a.grpc_extra import load_a2a_grpc

                grpc = load_a2a_grpc()
                grpc_channel_factory = grpc.aio.insecure_channel
            config = _sdk("ClientConfig")(
                streaming=streaming,
                httpx_client=self._http,
                grpc_channel_factory=grpc_channel_factory,
                supported_protocol_bindings=[owner.transport] if owner.transport else [],
                use_client_preference=owner.transport is not None,
            )
            found = self._clients[streaming] = await _sdk("create_client")(
                agent=self._card,
                client_config=config,
                interceptors=list(owner._transport_interceptors()),
            )
            return found

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.close()
        await self._http.aclose()

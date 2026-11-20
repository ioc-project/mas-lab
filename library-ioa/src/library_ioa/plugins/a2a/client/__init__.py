from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mas.runtime.aio import LoopCache

logger = logging.getLogger(__name__)


_SDK_NAMES = frozenset({"A2ACardResolver", "ClientConfig", "create_client"})


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
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        transport: str | None = None,
    ) -> None:
        self.url = (url or "http://127.0.0.1:8000").rstrip("/")
        self.headers = dict(headers or {})
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
        stream: bool = False,
    ) -> dict[str, Any]:
        call_context = self._call_context()
        state = getattr(call_context, "state", None)
        try:
            payload = await self._send_once(
                message, context_id=context_id, metadata=metadata, stream=stream, call_context=call_context
            )
        except BaseException as exc:
            # a card or client kept from before the failure may be stale (the peer restarted, its card changed)
            if isinstance(exc, Exception):
                self._connections.drop()
            self._finish_call(state, exc)
            raise
        self._finish_call(state, None)
        return payload

    def _call_context(self) -> Any:
        """The SDK call context that carries this call's identity to the transport interceptor."""
        from mas.runtime.transport.emit import protocol_config

        cfg = protocol_config("a2a")
        if not (cfg.hooks or cfg.propagate_metadata):
            return None
        from a2a.client.client import ClientCallContext

        from library_ioa.plugins.a2a.hooks import current_identity, peer_label, start_call_state

        source, target, session_id = current_identity()
        context = ClientCallContext()
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
        if not chunks:
            return {"status": "completed", "result": ""}
        payload = self._normalize_sdk_payload(chunks[-1])
        payload.setdefault("status", "completed")
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
        stream: bool = False,
    ) -> dict[str, Any]:
        return await self._sdk_send_message(message, context_id=context_id, metadata=metadata, stream=stream)


class _Connection:
    """What one event loop keeps for one remote agent: an HTTP client, its card and the SDK client per binding."""

    def __init__(self, owner: A2AClient) -> None:
        self._owner = owner
        self._http = httpx.AsyncClient(headers=owner.headers, timeout=owner.timeout)
        self._card: Any = None
        self.card_data: dict[str, Any] | None = None
        self._clients: dict[bool, Any] = {}
        self._lock = asyncio.Lock()

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

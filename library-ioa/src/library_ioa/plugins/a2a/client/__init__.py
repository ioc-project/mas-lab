from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mas.runtime.aio import LoopCache

logger = logging.getLogger(__name__)


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

    async def _sdk_get_agent_card(self, httpx_client: httpx.AsyncClient) -> dict[str, Any]:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self.url)
        card = await resolver.get_agent_card()
        if hasattr(card, "ListFields"):
            data = MessageToDict(card, preserving_proto_field_name=True)
        elif isinstance(card, dict):
            data = card
        else:
            data = {"name": "remote-agent", "description": "Remote agent", "url": self.url}
        self._agent_card_cache = data
        return dict(data)

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
        try:
            return await self._send_once(message, context_id=context_id, metadata=metadata, stream=stream)
        except Exception:
            # a card or client kept from before the failure may be stale (the peer restarted, its card changed)
            self._connections.drop()
            raise

    async def _send_once(
        self,
        message: str,
        *,
        context_id: str,
        metadata: dict[str, str] | None,
        stream: bool,
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
        async for chunk in client.send_message(request):
            chunks.append(chunk)
        if not chunks:
            return {"status": "completed", "result": ""}
        payload = self._normalize_sdk_payload(chunks[-1])
        payload.setdefault("status", "completed")
        return payload

    async def aget_agent_card(self) -> dict[str, Any]:
        """The agent card, fetched once and kept."""
        return dict((await self._connection()).card_data or await (await self._connection()).fetch_card())

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

    async def fetch_card(self) -> dict[str, Any]:
        async with self._lock:
            if self.card_data is None:
                self.card_data = await self._owner._sdk_get_agent_card(self._http)
            return self.card_data

    async def client(self, streaming: bool) -> Any:
        async with self._lock:
            found = self._clients.get(streaming)
            if found is not None:
                return found
            if self._card is None:
                resolver = A2ACardResolver(httpx_client=self._http, base_url=self._owner.url)
                self._card = await resolver.get_agent_card()
            owner = self._owner
            grpc_channel_factory = None
            if owner.transport == "GRPC":
                import grpc

                grpc_channel_factory = grpc.aio.insecure_channel
            config = ClientConfig(
                streaming=streaming,
                httpx_client=self._http,
                grpc_channel_factory=grpc_channel_factory,
                supported_protocol_bindings=[owner.transport] if owner.transport else [],
                use_client_preference=owner.transport is not None,
            )
            found = self._clients[streaming] = await create_client(agent=self._card, client_config=config)
            return found

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.close()
        await self._http.aclose()

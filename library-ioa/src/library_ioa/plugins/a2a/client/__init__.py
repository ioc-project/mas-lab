from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict

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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mas-a2a")

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

    async def _sdk_send_message(
        self,
        message: str,
        *,
        context_id: str = "",
        metadata: dict[str, str] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
        ) as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self.url)
            card = await resolver.get_agent_card()
            grpc_channel_factory = None
            if self.transport == "GRPC":
                import grpc

                grpc_channel_factory = grpc.aio.insecure_channel
            config = ClientConfig(
                streaming=bool(stream),
                httpx_client=httpx_client,
                grpc_channel_factory=grpc_channel_factory,
                supported_protocol_bindings=[self.transport] if self.transport else [],
                use_client_preference=self.transport is not None,
            )
            client = await create_client(agent=card, client_config=config)
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
            try:
                async for chunk in client.send_message(request):
                    chunks.append(chunk)
            finally:
                await client.close()

        if not chunks:
            return {"status": "completed", "result": ""}
        payload = self._normalize_sdk_payload(chunks[-1])
        payload.setdefault("status", "completed")
        return payload

    def get_agent_card(self) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            async with httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
            ) as httpx_client:
                return await self._sdk_get_agent_card(httpx_client)

        return self._run_sync(load())

    def send_message(
        self,
        message: str,
        *,
        context_id: str = "",
        metadata: dict[str, str] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        return self._run_sync(
            self._sdk_send_message(
                message,
                context_id=context_id,
                metadata=metadata,
                stream=stream,
            )
        )

    async def asend_message(
        self,
        message: str,
        *,
        context_id: str = "",
        metadata: dict[str, str] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Send on the caller's loop so cancellation reaches the SDK transport."""
        return await self._sdk_send_message(message, context_id=context_id, metadata=metadata, stream=stream)

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def _run_sync(self, coroutine: Any) -> Any:
        """Run SDK I/O off the caller loop, including from an async executor."""
        future = self._executor.submit(asyncio.run, coroutine)
        return future.result()

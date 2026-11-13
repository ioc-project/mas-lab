from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mas.runtime.boundary.connection import (
    BindingUnavailable,
    ConnectionBinding,
    ConnectionTarget,
    Invocation,
    PreparedOperation,
)

from library_ioa.plugins.a2a.client.binding import CURRENT_INTERFACE, RecordingClientFactory, SelectedInterface

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
        self._binding_guard = threading.Lock()
        self._bindings: dict[str, tuple[SelectedInterface, ConnectionBinding]] = {}
        self._closed = False
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
        selected: SelectedInterface | None = None,
    ) -> dict[str, Any]:
        selected = selected or self._active_interface()
        async with httpx.AsyncClient(
            headers=dict(selected.headers) if selected else self.headers,
            timeout=selected.timeout if selected else self.timeout,
        ) as httpx_client:
            if selected is None:
                resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self.url)
                card = await resolver.get_agent_card()
            else:
                card = selected.agent_card()
            protocol = selected.protocol_binding if selected else self.transport
            grpc_channel_factory = None
            if protocol == "GRPC":
                import grpc

                grpc_channel_factory = grpc.aio.insecure_channel
            config = ClientConfig(
                streaming=bool(stream),
                httpx_client=httpx_client,
                grpc_channel_factory=grpc_channel_factory,
                supported_protocol_bindings=[protocol] if protocol else [],
                use_client_preference=protocol is not None,
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

    async def _resolve_interface(self) -> SelectedInterface:
        # Card discovery and transport construction do not send an agent task.
        headers, timeout, url, protocol = dict(self.headers), self.timeout, self.url, self.transport
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as http:
            card = await A2ACardResolver(httpx_client=http, base_url=url).get_agent_card()
            grpc_factory = None
            if protocol == "GRPC":
                import grpc

                grpc_factory = grpc.aio.insecure_channel
            config = ClientConfig(
                streaming=False,
                grpc_channel_factory=grpc_factory,
                httpx_client=http,
                supported_protocol_bindings=[protocol] if protocol else [],
                use_client_preference=protocol is not None,
            )
            factory = RecordingClientFactory(config)
            client = factory.create(card)
            try:
                return factory.selection(card, headers=headers, timeout=timeout)
            finally:
                await client.close()

    def prepare_message(
        self,
        recipient: str,
        message: str,
        *,
        context_id: str = "",
        sender: Invocation | None = None,
    ) -> PreparedOperation:
        with self._binding_guard:
            if self._closed:
                raise BindingUnavailable("A2A client is closed")
        selected = self._run_sync(self._resolve_interface())

        def factory(name: str, arguments: dict[str, Any], ctx: Any) -> Invocation:
            def call(**trace: Any) -> Any:
                token = CURRENT_INTERFACE.set((self, selected))
                try:
                    if sender is not None:
                        return sender.call(**trace)
                    return self.send_message(
                        arguments["message"],
                        context_id=arguments["context_id"],
                        metadata={
                            "mas.correlation_id": str(trace.get("correlation_id", 0)),
                            "mas.caller_call_id": str(trace.get("caller_call_id", "")),
                        },
                    )
                finally:
                    CURRENT_INTERFACE.reset(token)

            async def acall(**trace: Any) -> Any:
                token = CURRENT_INTERFACE.set((self, selected))
                try:
                    if sender is not None and sender.acall is not None:
                        return await sender.acall(**trace)
                    return await self._sdk_send_message(
                        arguments["message"],
                        context_id=arguments["context_id"],
                        selected=selected,
                    )
                finally:
                    CURRENT_INTERFACE.reset(token)

            return Invocation(call, acall)

        with self._binding_guard:
            if self._closed:
                raise BindingUnavailable("A2A client is closed")
            entry = self._bindings.get(recipient)
            if entry is None:
                binding = ConnectionBinding(
                    "a2a",
                    selected.transport,
                    ConnectionTarget(endpoint=selected.url),
                    factory,
                    binding_ref=f"a2a:{recipient}",
                )
                self._bindings[recipient] = (selected, binding)
            else:
                previous, binding = entry
                if selected != previous:
                    binding.replace("a2a", selected.transport, ConnectionTarget(endpoint=selected.url), factory)
                    self._bindings[recipient] = (selected, binding)
            return binding.prepare("send", {"message": message, "context_id": context_id}, factory=factory)

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
                selected=self._active_interface(),
            )
        )

    def _active_interface(self) -> SelectedInterface | None:
        active = CURRENT_INTERFACE.get()
        return active[1] if active is not None and active[0] is self else None

    def retire_bindings(self) -> None:
        """Invalidate pending operations when their route is replaced."""
        with self._binding_guard:
            for _, binding in self._bindings.values():
                binding.close()
            self._bindings.clear()

    def close(self) -> None:
        with self._binding_guard:
            self._closed = True
            for _, binding in self._bindings.values():
                binding.close()
            self._bindings.clear()
        self._executor.shutdown(wait=True)

    def _run_sync(self, coroutine: Any) -> Any:
        """Run SDK I/O off the caller loop, including from an async executor."""
        future = self._executor.submit(asyncio.run, coroutine)
        return future.result()

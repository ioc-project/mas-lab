from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
)
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response
from google.protobuf.json_format import MessageToDict
from mas.runtime.boundary.webserver import WebServerContract, WebServerHandle
from mas.runtime.spec.endpoint_usage import usage_listens

from .agentcard import agent_card_from_manifest
from .server.app import build_app
from .server.executor import MasLabAgentExecutor

logger = logging.getLogger(__name__)


class A2APushNotificationSender(BasePushNotificationSender):
    """HTTP push sender that supports A2A token and authentication fields."""

    async def _dispatch_notification(
        self,
        event: Any,
        push_info: TaskPushNotificationConfig,
        task_id: str,
    ) -> bool:
        headers: dict[str, str] = {}
        if push_info.authentication.scheme and push_info.authentication.credentials:
            headers["Authorization"] = (
                f"{push_info.authentication.scheme} "
                f"{push_info.authentication.credentials}"
            )
        elif push_info.token:
            headers["X-A2A-Notification-Token"] = push_info.token
        try:
            response = await self._client.post(
                push_info.url,
                json=MessageToDict(to_stream_response(event)),
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("A2A push notification failed for task %s", task_id)
            return False
        return True

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class A2AExposureHandle:
    host: str
    port: int
    server: WebServerHandle

    def close(self) -> None:
        self.server.close()


class A2AExposure:
    """On-demand A2A web exposure for a manifest-backed agent."""

    def __init__(
        self,
        *,
        agentCard: bool = True,
        host: str = "127.0.0.1",
        port: int = 8000,
        grpc_port: int | None = None,
        capabilities: dict[str, Any] | None = None,
        endpoint: dict[str, Any] | None = None,
        webserver: WebServerContract | None = None,
    ) -> None:
        self.agent_card_enabled = agentCard
        self.host = host
        self.port = port
        self.grpc_port = grpc_port
        self.public_url: str | None = None
        self.grpc_url: str | None = None
        self.capabilities = dict(capabilities or {})
        if endpoint is not None:
            self._configure_from_application_endpoint(endpoint)
        if webserver is None:
            from mas.runtime.registry import get_registry

            webserver = get_registry().create(
                "webserver",
                binding={"type": "uvicorn"},
            )
        self._webserver = webserver

    def _configure_from_application_endpoint(self, endpoint: dict[str, Any]) -> None:
        if endpoint.get("protocol") != "a2a":
            raise ValueError("A2A exposure requires an Application endpoint with protocol: a2a")
        usage = endpoint.get("usage")
        deploys_endpoint = (
            usage_listens(usage) if usage is not None else endpoint.get("expose") is True
        )
        if not deploys_endpoint:
            raise ValueError(
                "A2A Application endpoint must set usage: listen or [connect, listen]"
            )
        public_url = str(endpoint.get("url") or "")
        parsed_url = urlsplit(public_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ValueError("A2A Application url must be an absolute HTTP(S) URL")
        if parsed_url.path not in {"", "/"}:
            raise ValueError("A2A Application url cannot include a path prefix")

        a2a_config = endpoint.get("a2a") or {}
        listen = a2a_config.get("listen") or {}
        if parsed_url.scheme == "https" and not listen:
            raise ValueError(
                "An HTTPS advertised url requires a2a.listen settings for the local HTTP server"
            )
        self.host = str(listen.get("host") or parsed_url.hostname)
        self.port = int(
            listen.get("port")
            or parsed_url.port
            or (443 if parsed_url.scheme == "https" else 80)
        )
        self.public_url = public_url.rstrip("/")
        self.grpc_port = a2a_config.get("grpc_port")
        configured_grpc_url = a2a_config.get("grpc_url")
        self.grpc_url = (
            str(configured_grpc_url)
            if configured_grpc_url
            else f"{parsed_url.hostname}:{self.grpc_port}"
            if self.grpc_port is not None
            else None
        )
        self.capabilities = dict(a2a_config.get("capabilities") or {})

    def build_app(self, manifest: dict[str, Any], handler: Callable[..., Any]) -> Any:
        if not self.agent_card_enabled:
            raise ValueError("A2A exposure requires agentCard=true")
        url = self.public_url or f"http://{self.host}:{self.port}"
        grpc_url = self.grpc_url or (
            f"{self.host}:{self.grpc_port}" if self.grpc_port is not None else None
        )
        push_config_store = None
        push_sender = None
        if self.capabilities.get("pushNotifications"):
            push_config_store = InMemoryPushNotificationConfigStore()
            push_sender = A2APushNotificationSender(
                httpx.AsyncClient(timeout=10),
                push_config_store,
            )
        return build_app(
            agent_card_from_manifest(
                manifest,
                url=url,
                grpc_url=grpc_url,
                capabilities=self.capabilities,
            ),
            MasLabAgentExecutor(self._adapt_runtime_handler(handler)),
            push_config_store=push_config_store,
            push_sender=push_sender,
        )

    @staticmethod
    def _adapt_runtime_handler(handler: Callable[..., Any]) -> Callable[..., Any]:
        def invoke(
            prompt: str,
            *,
            context: Any = None,
            cancel_event: Any = None,
            **kwargs: Any,
        ) -> Any:
            message = getattr(context, "message", None)
            metadata = getattr(message, "metadata", None)
            metadata = (
                metadata
                if isinstance(metadata, dict)
                else MessageToDict(metadata)
                if metadata is not None
                else {}
            )
            upstream_correlation_id: int | None = None
            try:
                value = metadata.get("mas.correlation_id")
                if value not in (None, ""):
                    upstream_correlation_id = int(value)
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid A2A mas.correlation_id metadata")
            runtime_kwargs = dict(kwargs)
            caller_call_id = str(metadata.get("mas.caller_call_id") or "")
            if caller_call_id:
                runtime_kwargs["parent_call_id"] = caller_call_id
            if upstream_correlation_id is not None:
                runtime_kwargs["upstream_correlation_id"] = upstream_correlation_id
            session_id = str(getattr(context, "context_id", "") or "")
            if not session_id:
                session_id = str(uuid4())
                try:
                    context.context_id = session_id
                except Exception:
                    pass
            result = handler(
                prompt,
                turn_id=(
                    getattr(message, "message_id", "")
                    or getattr(context, "task_id", "")
                    or "u1"
                ),
                session_id=session_id,
                **runtime_kwargs,
            )
            if isinstance(result, dict) and not result.get("context_id"):
                result = dict(result)
                result["context_id"] = session_id
            return result

        return invoke

    def serve_blocking(self, manifest: dict[str, Any], handler: Callable[..., Any]) -> None:
        app = self.build_app(manifest, handler)
        if self.grpc_port is not None:
            from .server import start_grpc_server

            async def serve_all() -> None:
                grpc_server = await start_grpc_server(
                    app.state.a2a_request_handler,
                    host=self.host,
                    port=self.grpc_port,
                )
                try:
                    serve_async = getattr(self._webserver, "serve_async", None)
                    if not callable(serve_async):
                        raise RuntimeError(
                            "The selected webserver plugin does not support shared-loop gRPC serving"
                        )
                    await serve_async(app, host=self.host, port=self.port)
                finally:
                    await grpc_server.stop(grace=5)

            asyncio.run(serve_all())
            return
        self._webserver.serve_blocking(
            app,
            host=self.host,
            port=self.port,
        )

    def serve_background(self, manifest: dict[str, Any], handler: Callable[..., Any]) -> A2AExposureHandle:
        app = self.build_app(manifest, handler)
        server = self._webserver.serve_background(
            app,
            host=self.host,
            port=self.port,
        )
        return A2AExposureHandle(self.host, self.port, server)

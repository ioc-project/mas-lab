from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from hashlib import sha256
from typing import Any

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore, PushNotificationConfigStore, PushNotificationSender
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, TaskState
from a2a.utils.error_handlers import build_rest_error_payload
from a2a.utils.errors import (
    ContentTypeNotSupportedError,
    ExtensionSupportRequiredError,
    InvalidParamsError,
    UnsupportedOperationError,
)
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from google.protobuf.descriptor import FieldDescriptor

from ..delivery import DeliveryController, DeliveryKind

_DYNAMIC_PROTO_MESSAGES = {
    "google.protobuf.Any",
    "google.protobuf.ListValue",
    "google.protobuf.Struct",
    "google.protobuf.Value",
}
A2A_JSON = "application/a2a+json"
JSON_CONTENT_TYPE = "application/json"
_ACCEPTED_CONTENT_TYPES = frozenset({A2A_JSON, JSON_CONTENT_TYPE})
_REST_PREFIX = "/a2a/rest"
_TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


class _A2ARequestHandler(DefaultRequestHandler):
    def __init__(self, *args: Any, delivery: DeliveryController | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.delivery = delivery

    async def _release(self, result: Any, kind: DeliveryKind, params: Any, context: Any) -> Any:
        if self.delivery is None or result is None:
            return result
        return await self.delivery.release(result, kind=kind, request=params, context=context)

    async def on_get_task(self, params: Any, context: Any):
        result = await super().on_get_task(params, context)
        return await self._release(result, "get_task", params, context)

    async def on_list_tasks(self, params: Any, context: Any):
        result = await super().on_list_tasks(params, context)
        for task in result.tasks:
            task.CopyFrom(await self._release(task, "list_tasks", params, context))
        return result

    async def on_cancel_task(self, params: Any, context: Any):
        result = await super().on_cancel_task(params, context)
        return await self._release(result, "cancel_task", params, context)

    def _validate_required_extensions(self, context: Any) -> None:
        required = {extension.uri for extension in self._agent_card.capabilities.extensions if extension.required}
        missing = required - context.requested_extensions
        if missing:
            raise ExtensionSupportRequiredError(f"Required extensions were not requested: {', '.join(sorted(missing))}")

    def _reject_unsupported_part_media(self, message: Any) -> None:
        accepted: set[str] = set()
        for value in list(self._agent_card.default_input_modes) + [
            mode for skill in self._agent_card.skills for mode in skill.input_modes
        ]:
            media = str(value).partition(";")[0].strip().lower()
            if media:
                accepted.add(media)
        for part in message.parts:
            media = str(part.media_type or "").partition(";")[0].strip().lower()
            if not media:
                continue
            if media in accepted or "*/*" in accepted or f"{media.split('/', 1)[0]}/*" in accepted:
                continue
            raise ContentTypeNotSupportedError()

    async def on_message_send(self, params: Any, context: Any):
        self._validate_required_extensions(context)
        self._reject_unsupported_part_media(params.message)
        result = await super().on_message_send(params, context)
        return await self._release(result, "send", params, context)

    async def on_message_send_stream(self, params: Any, context: Any):
        if self.delivery is not None:
            raise UnsupportedOperationError("Streaming is disabled when a delivery policy is configured")
        self._validate_required_extensions(context)
        self._reject_unsupported_part_media(params.message)
        async for event in super().on_message_send_stream(params, context):
            yield event

    async def on_subscribe_to_task(self, params: Any, context: Any):
        if self.delivery is not None:
            raise UnsupportedOperationError("Streaming is disabled when a delivery policy is configured")
        try:
            async for event in super().on_subscribe_to_task(params, context):
                yield event
        except InvalidParamsError as error:
            task = await self.task_store.get(params.id, context)
            if task is None or task.status.state not in _TERMINAL_TASK_STATES:
                raise
            raise UnsupportedOperationError("Cannot subscribe to a terminal task") from error

    async def aclose(self) -> None:
        try:
            if self.delivery is not None:
                await self.delivery.aclose()
        finally:
            await self._close_handler()

    async def _close_handler(self) -> None:
        try:
            await super().aclose()
        finally:
            close = getattr(self._push_sender, "aclose", None)
            if callable(close):
                await close()


class _ClosingActiveTaskRegistry(ActiveTaskRegistry):
    def __init__(
        self,
        *,
        agent_executor: Any,
        task_store: Any,
        push_sender: PushNotificationSender | None = None,
    ) -> None:
        super().__init__(
            agent_executor=agent_executor,
            task_store=task_store,
            push_sender=push_sender,
        )

    def _on_active_task_cleanup(self, active_task: ActiveTask) -> None:
        cleanup_task = asyncio.create_task(self._close_and_remove(active_task))
        self._cleanup_tasks.add(cleanup_task)
        cleanup_task.add_done_callback(self._cleanup_tasks.discard)

    async def _close_and_remove(self, active_task: ActiveTask) -> None:
        await self._remove_task(_remove_task_arg(active_task))
        await active_task.aclose()


def _remove_task_arg(active_task: ActiveTask) -> Any:
    """a2a-sdk 1.1 takes the task id string; 1.3 takes the task object."""
    names = [name for name in inspect.signature(ActiveTaskRegistry._remove_task).parameters if name != "self"]
    return active_task if names and names[0] != "task_id" else active_task.task_id


def _ignore_unknown_proto_fields(value: Any, descriptor: Any) -> Any:
    if not isinstance(value, dict):
        return value

    fields = {field.name: field for field in descriptor.fields}
    fields.update({field.json_name: field for field in descriptor.fields})
    cleaned: dict[str, Any] = {}
    for name, field_value in value.items():
        field = fields.get(name)
        if field is None:
            continue
        message_type = field.message_type
        is_repeated = getattr(field, "is_repeated", None)
        if is_repeated is None:
            is_repeated = field.label == FieldDescriptor.LABEL_REPEATED
        if (
            message_type is None
            or message_type.full_name in _DYNAMIC_PROTO_MESSAGES
            or message_type.GetOptions().map_entry
        ):
            cleaned[name] = field_value
        elif is_repeated and isinstance(field_value, list):
            cleaned[name] = [
                _ignore_unknown_proto_fields(item, message_type) if isinstance(item, dict) else item
                for item in field_value
            ]
        elif isinstance(field_value, dict):
            cleaned[name] = _ignore_unknown_proto_fields(
                field_value,
                message_type,
            )
        else:
            cleaned[name] = field_value
    return cleaned


def _sanitize_a2a_request(path: str, payload: Any) -> Any:
    if path == "/" and isinstance(payload, dict):
        method = payload.get("method")
        request_type = JsonRpcDispatcher.METHOD_TO_MODEL.get(method)
        params = payload.get("params")
        if request_type is not None and isinstance(params, dict):
            payload["params"] = _ignore_unknown_proto_fields(
                params,
                request_type.DESCRIPTOR,
            )
    elif path == "/a2a/rest/message:send" and isinstance(payload, dict):
        request_type = JsonRpcDispatcher.METHOD_TO_MODEL["SendMessage"]
        payload = _ignore_unknown_proto_fields(payload, request_type.DESCRIPTOR)
    return payload


def _coerce_agent_capabilities(capabilities: Any) -> AgentCapabilities:
    if isinstance(capabilities, AgentCapabilities):
        return capabilities
    if isinstance(capabilities, dict):
        return AgentCapabilities(
            streaming=bool(capabilities.get("streaming", False)),
            push_notifications=bool(capabilities.get("pushNotifications", False)),
            extensions=list(capabilities.get("extensions") or []),
            extended_agent_card=bool(capabilities.get("extendedAgentCard", False)),
        )
    return AgentCapabilities(streaming=False, push_notifications=False)


def _coerce_agent_card(card: AgentCard | dict[str, Any]) -> AgentCard:
    if isinstance(card, AgentCard):
        return card

    payload = dict(card)
    agent_url = str(payload.get("url") or payload.get("agent_url") or "")
    payload.pop("url", None)
    payload.pop("agent_url", None)
    documentation_url = payload.get("documentation_url") or payload.get("documentationUrl")
    if payload.get("agent_url"):
        documentation_url = payload["agent_url"]
    capabilities = _coerce_agent_capabilities(payload.get("capabilities", {}))
    supported_interfaces = payload.get("supportedInterfaces") or payload.get("supported_interfaces")
    if not supported_interfaces and agent_url:
        supported_interfaces = [
            AgentInterface(
                url=agent_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
            AgentInterface(
                url=f"{agent_url.rstrip('/')}/a2a/rest",
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
            ),
        ]
    return AgentCard(
        name=str(payload.get("name") or "remote-agent"),
        description=str(payload.get("description") or "Remote agent"),
        version=str(payload.get("version") or "1.0.0"),
        documentation_url=str(documentation_url) if documentation_url else "",
        capabilities=capabilities,
        supported_interfaces=supported_interfaces or [],
        default_input_modes=list(
            payload.get("defaultInputModes") or payload.get("default_input_modes") or ["text/plain"]
        ),
        default_output_modes=list(
            payload.get("defaultOutputModes") or payload.get("default_output_modes") or ["text/plain"]
        ),
        skills=list(payload.get("skills") or []),
    )


def build_app(
    agent_card: AgentCard | dict[str, Any],
    executor: Any,
    *,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    extended_agent_card: AgentCard | dict[str, Any] | None = None,
    delivery: DeliveryController | None = None,
) -> FastAPI:
    """Build an SDK-backed A2A FastAPI app with the official JSON-RPC and agent-card routes."""
    card = _coerce_agent_card(agent_card)
    if delivery is not None:
        card = AgentCard.FromString(card.SerializeToString())
        card.capabilities.streaming = False
        if extended_agent_card is not None:
            extended_agent_card = _coerce_agent_card(extended_agent_card)
            extended_agent_card = AgentCard.FromString(extended_agent_card.SerializeToString())
            extended_agent_card.capabilities.streaming = False
    task_store = InMemoryTaskStore()
    request_handler = _A2ARequestHandler(
        delivery=delivery,
        agent_executor=executor,
        task_store=task_store,
        agent_card=card,
        push_config_store=push_config_store,
        push_sender=push_sender,
        extended_agent_card=(_coerce_agent_card(extended_agent_card) if extended_agent_card is not None else None),
    )
    request_handler._active_task_registry = _ClosingActiveTaskRegistry(
        agent_executor=executor,
        task_store=task_store,
        push_sender=push_sender,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await request_handler.aclose()

    app = FastAPI(title=card.name, version=card.version, lifespan=lifespan)
    app.state.a2a_request_handler = request_handler
    app.state.a2a_delivery = delivery
    app.state.a2a_agent_card = card
    card_etag = f'"{sha256(card.SerializeToString(deterministic=True)).hexdigest()}"'
    card_last_modified = format_datetime(datetime.now(timezone.utc), usegmt=True)

    @app.middleware("http")
    async def add_agent_card_cache_headers(request: Request, call_next: Any) -> Response:
        if request.method == "POST" and request.url.path in {
            "/",
            "/a2a/rest/message:send",
        }:
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in _ACCEPTED_CONTENT_TYPES:
                error = ContentTypeNotSupportedError()
                if request.url.path == "/":
                    try:
                        payload = await request.json()
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        payload = {}
                    return JSONResponse(build_error_response(payload.get("id"), error))
                error_payload = build_rest_error_payload(error)
                return JSONResponse(
                    status_code=int(error_payload["error"]["code"]),
                    content=error_payload,
                    media_type=A2A_JSON,
                )
            try:
                payload = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            else:
                request._body = json.dumps(
                    _sanitize_a2a_request(request.url.path, payload),
                    separators=(",", ":"),
                ).encode("utf-8")
        response = await call_next(request)
        if request.url.path.startswith(_REST_PREFIX):
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != "text/event-stream":
                response.headers["content-type"] = A2A_JSON
        if request.method == "GET" and request.url.path == "/.well-known/agent-card.json":
            response.headers.setdefault("Cache-Control", "public, max-age=300")
            response.headers.setdefault("ETag", card_etag)
            response.headers.setdefault("Last-Modified", card_last_modified)
        return response

    from a2a.server.routes import add_a2a_routes_to_fastapi

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
        rest_routes=create_rest_routes(request_handler, path_prefix="/a2a/rest"),
    )
    return app

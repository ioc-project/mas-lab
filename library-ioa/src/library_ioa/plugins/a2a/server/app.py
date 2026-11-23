from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import re
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from hashlib import sha256
from typing import Any

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore, PushNotificationConfigStore, PushNotificationSender
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    SendMessageRequest,
    TaskState,
)
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
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from mas.library.standard.lib.stack import Envelope, Handler, PeerChallenge
from starlette.authentication import SimpleUser

from library_ioa.plugins.a2a.data_parts import DATA_PARTS_KEY, DataPartError, inbound_metadata
from library_ioa.plugins.a2a.server.owner import VerifiedOwner, VerifiedOwnerContextBuilder
from library_ioa.plugins.http_layer.challenge import challenge_headers

_DYNAMIC_PROTO_MESSAGES = {
    "google.protobuf.Any",
    "google.protobuf.ListValue",
    "google.protobuf.Struct",
    "google.protobuf.Value",
}
A2A_JSON = "application/a2a+json"
JSON_CONTENT_TYPE = "application/json"
_ACCEPTED_CONTENT_TYPES = frozenset({A2A_JSON, JSON_CONTENT_TYPE})
#: The longest context, task or message id a gated server accepts (the verifier reads no more of one).
MAX_ID = 256
_REST_PREFIX = "/a2a/rest"
_logger = logging.getLogger(__name__)

_AGENT_CARD_PATH = "/.well-known/agent-card.json"

_TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


class _A2ARequestHandler(DefaultRequestHandler):
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
        self._emit_received(params)
        try:
            result = await super().on_message_send(params, context)
        except BaseException as exc:
            self._emit_error(params, exc)
            raise
        self._emit_completed(params)
        return result

    async def on_message_send_stream(self, params: Any, context: Any):
        self._validate_required_extensions(context)
        self._reject_unsupported_part_media(params.message)
        self._emit_received(params)
        try:
            async for event in super().on_message_send_stream(params, context):
                yield event
        except BaseException as exc:
            self._emit_error(params, exc)
            raise
        self._emit_completed(params)

    def _transport_key(self, params: Any) -> tuple[str, str]:
        from library_ioa.plugins.a2a.hooks import correlation_key

        message = getattr(params, "message", None)
        message_id = str(getattr(message, "message_id", "") or "")
        key = correlation_key(
            context_id=str(getattr(message, "context_id", "") or ""),
            task_id=str(getattr(message, "task_id", "") or ""),
            message_id=message_id,
        )
        return key, message_id

    def _emit_received(self, params: Any) -> None:
        try:
            from library_ioa.plugins.a2a.hooks import _cfg, emit_received, read_observe_metadata

            if not _cfg().hooks:
                return
            key, message_id = self._transport_key(params)
            observe = read_observe_metadata(getattr(getattr(params, "message", None), "metadata", None))
            target = str(getattr(self._agent_card, "name", "") or "")
            emit_received(
                key=key,
                source=str(observe.get("last_agent_name") or ""),
                target=target,
                # A peer's session id cannot be checked; keep it as a claim, not this run's session.
                peer_session_id=str(observe.get("session_id") or ""),
                agent_id=target,
                message_id=message_id,
            )
        except Exception:
            _logger.debug("a2a received hook failed", exc_info=True)

    def _emit_completed(self, params: Any) -> None:
        try:
            from library_ioa.plugins.a2a.hooks import _cfg, emit_completed

            if not _cfg().hooks:
                return
            key, message_id = self._transport_key(params)
            target = str(getattr(self._agent_card, "name", "") or "")
            emit_completed(side="server", key=key, source="", target=target, agent_id=target, message_id=message_id)
        except Exception:
            _logger.debug("a2a completed hook failed", exc_info=True)

    def _emit_error(self, params: Any, error: BaseException) -> None:
        try:
            from library_ioa.plugins.a2a.hooks import _cfg, emit_error

            if not _cfg().hooks:
                return
            key, message_id = self._transport_key(params)
            emit_error(side="server", key=key, message_id=message_id, error_type=type(error).__name__)
        except Exception:
            _logger.debug("a2a error hook failed", exc_info=True)

    async def on_subscribe_to_task(self, params: Any, context: Any):
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


MIN_ADMISSION_TOKEN = 32
_AGENT_CARD_PATH = "/.well-known/agent-card.json"


@dataclass
class AdmissionCaller:
    """One admitted caller: its name and the SecretRef of its token, revealed on every request."""

    name: str
    ref: Any


def checked_admission(admission: dict[str, Any] | list[AdmissionCaller]) -> list[AdmissionCaller]:
    """Callers map of SecretRef objects. An empty policy, a bad name or a literal token is refused."""
    if isinstance(admission, list):
        return admission
    if not isinstance(admission, dict) or not admission:
        raise ValueError("A2A admission names no caller; an empty policy is refused, not read as open")
    from mas.runtime.secrets.errors import SecretRefError
    from mas.runtime.secrets.ref import parse_secret_ref

    checked: list[AdmissionCaller] = []
    for name, raw in admission.items():
        caller = str(name or "").strip()
        if not caller or any(ord(char) < 32 or ord(char) == 127 for char in caller):
            raise ValueError("A2A admission caller names must be non-empty text without control characters")
        try:
            checked.append(AdmissionCaller(name=caller, ref=parse_secret_ref(raw, field=f"admission.callers.{caller}")))
        except SecretRefError as exc:
            raise ValueError(str(exc)) from exc
    return checked


def admitted_caller(admission: list[AdmissionCaller], presented: str) -> str:
    """The one caller whose current token is ``presented``, else ``""``. A missing, unreadable or short
    token admits no one; a token two callers share admits neither."""
    from mas.runtime.secrets import current_resolver
    from mas.runtime.secrets.errors import SecretError

    digest = sha256(presented.encode("utf-8")).digest()
    matched = []
    for entry in admission:  # walk the whole table: the time does not say which entry matched
        try:
            token = current_resolver().reveal(entry.ref).encode("utf-8")
        except SecretError:
            token = b""
        if len(token) >= MIN_ADMISSION_TOKEN and hmac.compare_digest(digest, sha256(token).digest()):
            matched.append(entry.name)
    return matched[0] if len(matched) == 1 else ""


def without_credential_headers(headers: Any) -> dict[str, str]:
    """``headers`` without the credential headers (``Authorization``, ``Cookie``, API key headers): the call
    context is logged by the SDK at debug level, and the handler is told who called, not the token."""
    from mas.runtime.secrets.ref import HTTP_SECRET_HEADERS

    return {str(key): str(value) for key, value in dict(headers).items() if str(key).lower() not in HTTP_SECRET_HEADERS}


class _CallContextBuilder(VerifiedOwnerContextBuilder):
    """The SDK's call context with a verified owner as the user and the credential headers left out of
    ``state["headers"]``."""

    def build(self, request: Request) -> Any:
        context = super().build(request)
        context.state["headers"] = without_credential_headers(request.headers)
        return context


def _add_admission(app: FastAPI, admission: list[AdmissionCaller]) -> None:
    @app.middleware("http")
    async def admit(request: Request, call_next: Any) -> Response:
        if request.method == "GET" and request.url.path == _AGENT_CARD_PATH:
            return await call_next(request)
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        caller = ""
        if scheme.lower() == "bearer" and value.strip():
            caller = await asyncio.to_thread(admitted_caller, admission, value.strip())
        if not caller:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        request.scope["user"] = SimpleUser(caller)
        return await call_next(request)


def build_request_handler(
    agent_card: AgentCard | dict[str, Any],
    executor: Any,
    *,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    extended_agent_card: AgentCard | dict[str, Any] | None = None,
) -> DefaultRequestHandler:
    """The SDK request handler every A2A binding shares: the HTTP routes and the slimrpc server both call it."""
    card = _coerce_agent_card(agent_card)
    # each owner (the SDK user's name) has its own tasks: said here, so a change of the SDK default cannot drop it
    task_store = InMemoryTaskStore(owner_resolver=resolve_user_scope)
    request_handler = _A2ARequestHandler(
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
    return request_handler


def _message_of(body: Any, *, rest: bool) -> dict[str, Any]:
    """The message of a send request body, read as the binding of its route reads it, else ``{}``.

    REST (``message:send``, ``message:stream``) has the message at the top level; JSON-RPC has it in ``params.message``.
    The other place is not looked at: a body that carries both has one message the SDK reads and one decoy.
    """
    if not isinstance(body, dict):
        return {}
    if rest:
        message = body.get("message")
    else:
        params = body.get("params")
        message = params.get("message") if isinstance(params, dict) else None
    return message if isinstance(message, dict) else {}


@dataclass(frozen=True)
class _Ids:
    """What a request names, read the way the SDK reads it (the same protobuf parse, so the same spelling wins)."""

    message: dict[str, Any]
    text: str | None
    context_id: str
    message_id: str
    task_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    data: tuple[Any, ...] = ()


_TASK_IN_PATH = re.compile(r"/tasks/([^/:]+)")


def _ids_of(path: str, body: Any, *, rest: bool) -> _Ids:
    """The ids and the text of a request. ``ValueError`` when the body does not parse as the SDK would parse it.

    ``contextId`` and ``context_id`` (and the other pairs) are both accepted by the protobuf parse and the last one
    wins: reading the dict by hand would let the gate see one and the SDK the other.
    """
    message = _message_of(body, rest=rest)
    try:
        if message:
            source = body if rest else body["params"]
            sent = ParseDict(source, SendMessageRequest(), ignore_unknown_fields=True).message
            text = "".join(part.text for part in sent.parts if part.WhichOneof("content") == "text")
            data = tuple(MessageToDict(part.data) for part in sent.parts if part.WhichOneof("content") == "data")
            metadata = MessageToDict(sent.metadata) if sent.HasField("metadata") else {}
            return _Ids(message, text, sent.context_id, sent.message_id, sent.task_id, metadata, data)
        if rest:
            found = _TASK_IN_PATH.search(path)
            return _Ids({}, None, "", "", found.group(1) if found else "")
        method = body.get("method") if isinstance(body, dict) else None
        kind = JsonRpcDispatcher.METHOD_TO_MODEL.get(method) if isinstance(method, str) else None
        params = body.get("params") if isinstance(body, dict) else None
        if kind is None or not isinstance(params, dict):
            return _Ids({}, None, "", "", "")
        asked = ParseDict(params, kind(), ignore_unknown_fields=True)
        names = {field.name for field in kind.DESCRIPTOR.fields}
        # a push-config call names its task in ``task_id`` (``id`` is the config's); the others name it in ``id``
        task = asked.task_id if "task_id" in names else asked.id if "id" in names else ""
        return _Ids({}, None, "", "", task)
    except (ParseError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("request does not parse") from exc


def message_text(message: Mapping[str, Any]) -> str:
    """The text parts of a message, joined."""
    return "".join(
        str(part["text"]) for part in message.get("parts") or [] if isinstance(part, dict) and "text" in part
    )


def _challenge_body(refused: PeerChallenge) -> dict[str, Any]:
    body: dict[str, Any] = {"code": refused.code, "challenge": refused.challenge}
    if refused.wire is not None:
        body["wire"] = refused.wire
    if refused.carry:
        body["carry"] = dict(refused.carry)
    return body


def with_message_text(body: Any, text: str, *, rest: bool) -> Any:
    """``body`` with the text of its message replaced: the first text part holds ``text``, other text parts go."""
    message = _message_of(body, rest=rest)
    kept: list[Any] = []
    placed = False
    for part in message.get("parts") or []:
        if isinstance(part, dict) and "text" in part:
            if placed:
                continue
            part = {**part, "text": text}
            placed = True
        kept.append(part)
    if not placed:
        kept.append({"text": text})
    message["parts"] = kept
    return body


def without_metadata_keys(body: Any, keys: set[str], *, rest: bool) -> Any | None:
    """``body`` with the credential carriers named by ``keys`` removed from its message, or ``None`` when it has none.

    A key is a metadata key; ``DATA_PARTS_KEY`` names the message's data parts, which are removed whole.
    """
    message = _message_of(body, rest=rest)
    changed = False
    metadata = message.get("metadata")
    if isinstance(metadata, dict) and keys & set(metadata):
        message["metadata"] = {k: v for k, v in metadata.items() if k not in keys}
        changed = True
    parts = message.get("parts")
    if DATA_PARTS_KEY in keys and isinstance(parts, list):
        kept = [part for part in parts if not (isinstance(part, dict) and "data" in part)]
        if len(kept) != len(parts):
            message["parts"] = kept
            changed = True
    return body if changed else None


def build_app(
    agent_card: AgentCard | dict[str, Any],
    executor: Any,
    *,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    extended_agent_card: AgentCard | dict[str, Any] | None = None,
    admission: dict[str, Any] | list[AdmissionCaller] | None = None,
    inbound: Callable[[Handler], Handler] | None = None,
) -> FastAPI:
    """Build an SDK-backed A2A FastAPI app with the official JSON-RPC and agent-card routes.

    ``admission`` maps a caller name to a SecretRef. Given, every request but the public agent
    card must carry a token of that table, and the caller is the table's name for it, never anything
    the request says about itself. Omitted, the app admits everyone (the default).

    ``inbound`` is the layers' inbound chain (``Stack.serve``): it wraps the handler that lets a POST through to the
    A2A routes, and runs before the SDK sees the request. A layer that refuses the caller raises
    ``PeerChallenge`` and the request is answered 401 with ``WWW-Authenticate``; the routes never run.
    Reading the agent card is not gated.
    """
    if admission is not None:
        admission = checked_admission(admission)
    card = _coerce_agent_card(agent_card)
    request_handler = build_request_handler(
        card,
        executor,
        push_config_store=push_config_store,
        push_sender=push_sender,
        extended_agent_card=extended_agent_card,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await request_handler.aclose()

    app = FastAPI(title=card.name, version=card.version, lifespan=lifespan)
    app.state.a2a_request_handler = request_handler
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
            try:
                from library_ioa.plugins.a2a.hooks import emit_agent_card

                emit_agent_card(
                    {"name": card.name, "description": card.description},
                    side="server",
                    agent_id=card.name,
                )
            except Exception:
                _logger.debug("a2a agent card hook failed", exc_info=True)
        return response

    if inbound is not None:

        async def proceed(envelope: Envelope) -> Response:
            request, call_next, body, text, rest = envelope.meta["proceed"]
            if envelope.payload != text:
                # a verifier handed back the text with its identity layer removed: that is what the agent receives
                request._body = json.dumps(
                    with_message_text(body, str(envelope.payload), rest=rest), separators=(",", ":")
                ).encode("utf-8")
            keys = {"mas.headers", *(str(k) for k in envelope.meta.get("credential_keys") or ())}
            stripped = without_metadata_keys(body, keys, rest=rest)
            if stripped is not None:
                # the credential carriers (the headers a peer put in the metadata, the keys the verifier says carried
                # the credential) are not kept: the SDK's task store keeps the message and GetTask returns it
                request._body = json.dumps(stripped, separators=(",", ":")).encode("utf-8")
            owner = str(envelope.meta.get("owner") or "")
            if owner:
                # the SDK keeps tasks per user: each verified owner gets its own, never the shared anonymous one, and
                # the executor makes the conversation a handler sees from this user (never from a caller's word)
                request.scope["user"] = VerifiedOwner(owner)
            return await call_next(request)

        chain = inbound(proceed)

        @app.middleware("http")
        async def gate_inbound(request: Request, call_next: Any) -> Response:
            if request.method in {"GET", "HEAD"} and request.url.path == _AGENT_CARD_PATH:
                return await call_next(request)
            try:
                body = json.loads(await request.body())
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = None
            rest = request.url.path.startswith(_REST_PREFIX)
            try:
                ids = _ids_of(request.url.path, body, rest=rest)
            except ValueError:
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_request"}})
            if max(len(ids.context_id), len(ids.task_id), len(ids.message_id)) > MAX_ID:
                # the verifier binds its evidence to the ids it reads: two ids that agree on a prefix must not share it
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_request"}})
            try:
                seen_metadata = inbound_metadata(ids.metadata, ids.data)
            except DataPartError:
                # a part that breaks the limits is refused before any verifier reads it; nothing of it is echoed
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_data_part"}})
            # headers are what the caller sent; ids come from the request the transport parsed, never from the layer
            envelope = Envelope(
                payload=ids.text,
                headers={key.lower(): value for key, value in request.headers.items()},
                meta={
                    "protocol": "a2a",
                    "context_id": ids.context_id,
                    "message_id": ids.message_id,
                    # a follow-up on a task names it; a first message has none yet (the SDK assigns it after this gate)
                    "task_id": ids.task_id,
                    # the caller's namespaced message metadata and the data parts (the transport's reserved key)
                    "metadata": seen_metadata,
                    "proceed": (request, call_next, body, ids.text, rest),
                },
            )
            try:
                response = await chain(envelope)
            except PeerChallenge as refused:
                return JSONResponse(
                    status_code=401,
                    headers=challenge_headers(refused, "Challenge"),
                    content={"error": _challenge_body(refused)},
                )
            return response

    if admission is not None:
        _add_admission(app, admission)

    from a2a.server.routes import add_a2a_routes_to_fastapi

    context_builder = _CallContextBuilder()

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/", context_builder=context_builder),
        rest_routes=create_rest_routes(request_handler, path_prefix="/a2a/rest", context_builder=context_builder),
    )
    return app

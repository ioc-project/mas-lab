#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""layer/a2a-rpc: the A2A SDK over a stack's SLIM link, through the ``slimrpc`` protocol binding.

The bottom link is ``slim``. This layer does not frame text itself: it sends and serves real A2A
(``SendMessage`` over the SDK's client and request handler) on the link's app, so a peer that speaks
A2A over slimrpc can be reached and can reach this one. An agent served here is the same SDK
request handler the ``a2a`` HTTP exposure uses.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Mapping
from typing import Any, ClassVar

from a2a.helpers import new_text_message
from a2a.server.agent_execution import RequestContext
from a2a.types.a2a_pb2 import AgentCard, Role, SendMessageRequest, StreamResponse
from google.protobuf.json_format import MessageToDict
from mas.library.standard.lib.comm.headers import HOP_HEADERS, bound_headers, validate_headers
from mas.library.standard.lib.comm.payload import normalize_send_intent
from mas.library.standard.lib.stack import (
    SESSION_HOOK_META,
    Envelope,
    Handler,
    Layer,
    PeerChallenge,
    StackError,
    Unauthenticated,
)
from mas.library.standard.lib.stack.agent_comm import inbound_envelope
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.runtime.aio import acall
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, bound_inbound

from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
from library_ioa.plugins.a2a.client import peer_error, raise_if_failed, streamed_text
from library_ioa.plugins.a2a.data_parts import DATA_PARTS_KEY, DataPartError, inbound_metadata
from library_ioa.plugins.a2a.handshake import complete
from library_ioa.plugins.slim.agent_comm import SlimAgentComm

logger = logging.getLogger(__name__)

BINDING = "slimrpc"
PROTOCOL_VERSION = "1.0"
_START_TIMEOUT_S = 10.0
_CARD_KEYS = ("description", "version", "skills", "card", "manifest")
_HEALTHY_S = 5.0
_MAX_RESTART_WAIT_S = 30.0
_SHUTDOWN_GRACE_S = 0.5


def _hop_map(values: Mapping[str, Any] | None) -> dict[str, str]:
    """Hop-safe keys from a map that may use mixed case (envelope headers or protobuf metadata)."""
    if not values:
        return {}
    lower = {str(key).lower(): str(value) for key, value in values.items() if value not in (None, "")}
    return {key: lower[key] for key in HOP_HEADERS if key in lower}


#: Message metadata key under which a layer above carries what would be wire headers over HTTP.
HEADERS_KEY = "mas.headers"


def _parts_text(parts: Any) -> str:
    return "".join(part.text for part in parts if part.WhichOneof("content") == "text")


def reply_text(responses: list[StreamResponse]) -> str:
    """The reply text of a ``SendMessage`` answer; ``AgentCommError`` when the remote task failed.

    A completed stream may be artifacts plus a ``status_update``, or only a status message: both
    are the reply, the same way the HTTP client reads a task.
    """
    if not responses:
        raise AgentCommError("A2A peer sent no response")
    raise_if_failed(responses)
    text = ""
    for response in responses:
        kind = response.WhichOneof("payload")
        if kind == "message":
            text = _parts_text(response.message.parts) or text
        elif kind == "task":
            task = response.task
            status_text = _parts_text(task.status.message.parts) if task.status.HasField("message") else ""
            found = "".join(_parts_text(artifact.parts) for artifact in task.artifacts) or status_text
            if not found:
                for message in reversed(task.history):
                    if message.role == Role.ROLE_AGENT:
                        found = _parts_text(message.parts)
                        break
            text = found or text
        elif kind == "artifact_update":
            text += _parts_text(response.artifact_update.artifact.parts)
        elif kind == "status_update":
            status = response.status_update.status
            if status.HasField("message"):
                text = text or _parts_text(status.message.parts)
    return text or streamed_text(responses)


#: The server-side key of the RPC being served: refusals are kept under it, so a caller cannot ask for (or spoil) the
#: refusal of another RPC by choosing the same message id.
_RPC_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar("a2a_rpc_token", default=None)


class _Refusals:
    """Refusals a turn raised, by the key of the RPC being served, until that RPC asks for its own.

    A send that does not wait (``blocking=false``) returns before its turn runs, so nobody may ask: an entry is kept
    for ``ttl`` seconds and at most ``limit`` are kept (the oldest goes first).
    """

    def __init__(self, *, ttl: float = 60.0, limit: int = 256) -> None:
        self._ttl = ttl
        self._limit = limit
        self._found: dict[str, tuple[float, PeerChallenge]] = {}

    def add(self, key: str, refused: PeerChallenge) -> None:
        now = time.monotonic()
        self._found = {k: v for k, v in self._found.items() if now - v[0] < self._ttl}
        self._found.pop(key, None)
        self._found[key] = (now, refused)
        while len(self._found) > self._limit:
            del self._found[next(iter(self._found))]

    def pop(self, key: str) -> PeerChallenge | None:
        found = self._found.pop(key, None)
        return found[1] if found is not None and time.monotonic() - found[0] < self._ttl else None


class A2ALayer(Layer):
    """A2A over the ``slim`` link below it.

    Client side, ``send`` is one SDK ``SendMessage`` over slimrpc to the peer's SLIM name. The text is one
    text part, ``context_id`` is the message ``contextId``, and ``correlation_id`` / ``caller_call_id``
    ride the message metadata as ``mas.correlation_id`` / ``mas.caller_call_id``, as the ``a2a`` link sends
    them. Hop-safe headers (``traceparent``, ``tracestate``, ``baggage``) ride the same metadata. A2A has
    no inform/ask wire: only the ``task`` intent is honoured. A failed, rejected or canceled remote task
    raises ``AgentCommError``.

    Server side, ``serve(handler)`` registers the SDK request handler on the link's app: the handler gets the
    text as ``payload`` and the call ids and context id in ``meta``. The link runs no reader of its own
    (``claim_inbound``): the RPC server owns the app's sessions. A connect-only stack never calls ``serve``,
    so the link keeps its reader and this layer starts no RPC server.
    """

    HONOURED_SEND_INTENTS: ClassVar[frozenset[str]] = frozenset({"task"})
    # slimrpc has no HTTP: headers a layer above sets cross as message metadata (``mas.headers``)
    WIRE_HEADERS: ClassVar[bool] = False

    def __init__(self, **params: Any) -> None:
        super().__init__()
        self._refused = _Refusals()
        self._handler: Handler | None = None
        self._request_handler: Any = None
        self._server: Any = None
        self._serving: asyncio.Task[Any] | None = None
        self._rpc_conn_id: Any = None
        # only the card keys: nothing else on the row is published
        self._card_spec = {key: params[key] for key in _CARD_KEYS if key in params}
        self._serving_since = 0.0
        self._quick_stops = 0
        self._app_ready_bound = False
        self._rpc_lock = asyncio.Lock()

    @staticmethod
    def _slim_link(lower: Layer | None) -> SlimAgentComm | None:
        """The SLIM link a bottom layer carries (a stack wraps the link in a layer), or ``None``."""
        if isinstance(lower, LinkLayer) and isinstance(lower.link, SlimAgentComm):
            return lower.link
        return None

    def bind(self, lower: Layer) -> None:
        if self._slim_link(lower) is None:
            raise StackError("the a2a-rpc layer runs over slim; to reach an A2A server at a url, use the a2a link")
        super().bind(lower)

    def honoured_send_intents(self) -> frozenset[str]:
        return self.HONOURED_SEND_INTENTS

    def serve(self, handler: Handler) -> Handler:
        self._handler = handler
        link = self._slim_link(self._lower)
        if link is not None:
            link.claim_inbound()
        return handler

    def start(self) -> None:
        if self._started:
            return
        super().start()
        if self._handler is None:
            return
        link = self._slim_link(self._require())
        if not self._app_ready_bound:
            link.on_app(self._on_app_ready)
            self._app_ready_bound = True
        if link.ready:
            link.actor.submit(self._astart_server(link), timeout=_START_TIMEOUT_S)

    def stop(self) -> None:
        if self._stopped:
            return
        super().stop()
        link = self._slim_link(self._lower)
        if self._server is None or link is None or not link.actor.running:
            return
        try:
            link.actor.submit(self._astop_server(), timeout=_START_TIMEOUT_S)
        except AgentCommError:
            logger.warning("a2a-rpc server did not stop cleanly", exc_info=True)

    async def _on_app_ready(self) -> None:
        if self._stopped or self._handler is None:
            return
        link = self._slim_link(self._lower)
        if link is None:
            return
        await self._astart_server(link)

    def _served_card(self, link: SlimAgentComm, url: str) -> Any:
        spec = self._card_spec
        manifest = spec.get("card") if isinstance(spec.get("card"), dict) else None
        if manifest is None and isinstance(spec.get("manifest"), dict):
            manifest = spec["manifest"]
        skills = spec.get("skills")
        return slimrpc_agent_card(
            str(link.agent_id or url),
            url,
            description=str(spec.get("description") or ""),
            version=str(spec.get("version") or "1"),
            skills=skills if isinstance(skills, list) else None,
            manifest=manifest,
        )

    async def _astart_server(self, link: Any) -> None:
        from slima2a.handler import SRPCHandler
        from slima2a.types.v1.a2a_pb2_slimrpc import add_A2AServiceServicer_to_server

        from library_ioa.plugins.a2a.server.app import build_request_handler
        from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor

        async with self._rpc_lock:
            if self._stopped or self._handler is None:
                return
            sb, app, conn_id = link.rpc_endpoint()
            if self._serving is not None and not self._serving.done() and self._rpc_conn_id == conn_id:
                return
            await self._adrop_server()
            if self._stopped or self._handler is None:
                return
            url = link.rpc_name()
            name = sb.Name(*url.split("/"))
            await app.subscribe_async(name, conn_id)
            card = self._served_card(link, url)
            self._request_handler = build_request_handler(card, MasLabAgentExecutor(self._turn))
            server = sb.Server.new_with_connection(app, name, conn_id)
            handler = _gated_handler(SRPCHandler, card, self._request_handler, self._refused, gated=self.verified_above)
            add_A2AServiceServicer_to_server(handler, server)
            self._server = server
            serving = asyncio.get_running_loop().create_task(server.serve_async())
            self._serving = serving
            self._rpc_conn_id = conn_id
            self._serving_since = time.monotonic()
            serving.add_done_callback(lambda task, bound=link: self._on_serving_done(task, bound))

    def _on_serving_done(self, task: asyncio.Task[Any], link: Any) -> None:
        """Reconnect when the RPC server stops on its own (the node dropped). Stop has already cleared ``_serving``."""
        if self._stopped or self._serving is not task:
            return
        self._serving = None
        self._rpc_conn_id = None
        stopped = None if task.cancelled() else task.exception()
        logger.warning("a2a-rpc server stopped", exc_info=stopped)
        # A server that dies at once, over and over, is not a dropped node: back off instead of
        # reconnecting in a tight loop.
        self._quick_stops = 0 if time.monotonic() - self._serving_since >= _HEALTHY_S else self._quick_stops + 1
        wait = min(_MAX_RESTART_WAIT_S, 0.5 * 2 ** (self._quick_stops - 1)) if self._quick_stops else 0.0
        link.mark_dead()
        actor = getattr(link, "actor", None)
        if actor is not None and getattr(actor, "running", False):
            actor.spawn(self._arecover(link, wait))

    async def _arecover(self, link: Any, wait: float = 0.0) -> None:
        errors = 0
        if wait:
            await asyncio.sleep(wait)
        while not self._stopped and self._handler is not None:
            try:
                await link.aensure()
                await self._astart_server(link)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                errors += 1
                logger.warning("a2a-rpc did not reconnect", exc_info=True)
                if self._stopped or self._handler is None:
                    return
                await asyncio.sleep(min(1.0, 0.05 * (2 ** min(errors, 5))))

    async def _astop_server(self) -> None:
        async with self._rpc_lock:
            await self._adrop_server()

    async def _adrop_server(self) -> None:
        server, self._server = self._server, None
        serving, self._serving = self._serving, None
        self._rpc_conn_id = None
        if server is not None:
            try:  # a graceful shutdown waits for open sessions; a stack that is stopping does not wait for them
                await asyncio.wait_for(server.shutdown_async(), _SHUTDOWN_GRACE_S)
            except (asyncio.TimeoutError, TimeoutError):
                logger.debug("a2a-rpc server still had open sessions at stop")
        if serving is not None:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        if self._request_handler is not None:
            await self._request_handler.aclose()
            self._request_handler = None

    async def _turn(self, message: str, context: RequestContext, **_: Any) -> Any:
        """The SDK executor's handler: the received A2A message as an envelope for the layers above."""
        if self._handler is None:
            raise AgentCommError("a2a-rpc layer has no inbound handler")
        sent = context.message
        user = getattr(getattr(context, "call_context", None), "user", None)
        # The caller SLIM verified for the session (never metadata the caller wrote).
        sender = str(user.user_name or "") if user is not None and getattr(user, "is_authenticated", False) else ""
        metadata = MessageToDict(sent.metadata) if sent is not None and sent.HasField("metadata") else {}
        try:
            correlation_id = int(metadata.get("mas.correlation_id") or 0)
        except (TypeError, ValueError):
            logger.warning("ignoring invalid A2A mas.correlation_id metadata")
            correlation_id = 0
        headers = _hop_map(metadata)
        if self.headers_in_metadata_above:
            # only a layer above that verifies and sends its headers this way reads them: no such layer, no injected
            # headers. A peer never overrides the trace context the transport itself carried.
            carried = _carried_headers(metadata.get(HEADERS_KEY))
            headers.update({k: v for k, v in carried.items() if k.lower() not in HOP_HEADERS})

        def forget_metadata(keys: Any) -> None:
            """Remove what carried the credential from the message the SDK keeps: metadata keys (names only), and
            the data parts when ``DATA_PARTS_KEY`` is named."""
            if sent is None:
                return
            if sent.HasField("metadata"):
                for key in keys:
                    if key in sent.metadata.fields:
                        del sent.metadata.fields[key]
            if DATA_PARTS_KEY in keys:
                kept = [part for part in sent.parts if part.WhichOneof("content") != "data"]
                del sent.parts[:]
                sent.parts.extend(kept)

        def replace_text(text: str) -> None:
            """Put the text a verifier handed back (the identity wrapper removed) in the message the SDK keeps."""
            if sent is None:
                return
            texts = [part for part in sent.parts if part.WhichOneof("content") == "text"]
            others = [part for part in sent.parts if part.WhichOneof("content") != "text"]
            first = texts[0] if texts else None
            del sent.parts[:]
            if first is not None:
                first.text = text
                sent.parts.append(first)
            else:
                sent.parts.add(text=text)
            sent.parts.extend(others)

        forget_metadata([HEADERS_KEY])  # read above; a header a peer carried is not kept with the message
        envelope = inbound_envelope(
            message,
            sender=sender,
            intent="task",
            correlation_id=correlation_id,
            caller_call_id=str(metadata.get("mas.caller_call_id") or ""),
            context_id=str(context.context_id or "") or str(uuid.uuid4()),
            headers=headers,
        )
        if self.verified_above:
            # what a verifying layer above reads; with none the handler gets the envelope it always got
            try:
                seen_metadata = inbound_metadata(
                    metadata,
                    (
                        MessageToDict(part.data)
                        for part in (sent.parts if sent is not None else ())
                        if part.WhichOneof("content") == "data"
                    ),
                )
            except DataPartError as broken:
                refused = PeerChallenge({}, code=str(broken))
                self._refused.add(_RPC_TOKEN.get() or "", refused)
                raise refused from None
            envelope = envelope.with_(
                meta={
                    "protocol": "a2a",
                    "message_id": str(sent.message_id) if sent is not None else "",
                    # the A2A task the message continues: one that was already open. A first message names none (the
                    # SDK makes a new task for it, and a resent first message gets another), so a verifier binds to
                    # the message id instead
                    "task_id": str(context.current_task.id) if context.current_task is not None else "",
                    # what the peer put in its message metadata under its own namespaced keys (caller data, never
                    # identity) and the data parts of the message, under the transport's reserved key
                    "metadata": seen_metadata,
                    # for the verifier: take the keys that carried the credential out of the kept message
                    "forget_metadata": forget_metadata,
                    "replace_text": replace_text,
                }
            )
        from library_ioa.plugins.slim.hooks import call_key, emit_completed, emit_error, emit_received, hooks_enabled

        link = self._slim_link(self._lower)
        target = str(link.name) if link is not None else ""
        announce = hooks_enabled()
        key = call_key(str(envelope.meta["context_id"]), correlation_id) if announce else ""
        if announce:
            emit_received(source=sender, target=target, key=key)
        try:
            with bound_headers(headers), bound_inbound(sender=sender, intent="task"):
                reply = await acall(self._handler, envelope)
        except PeerChallenge as refused:
            # the handler did not run; the RPC answers unauthenticated (see _gated_handler)
            self._refused.add(_RPC_TOKEN.get() or "", refused)
            raise
        except Exception as exc:
            if announce:
                emit_error(side="server", key=key, source=sender, target=target, error_type=type(exc).__name__)
            raise
        if announce:
            emit_completed(side="server", source=sender, target=target, key=key)
        return str(reply if reply is not None else "")

    async def asend(self, envelope: Envelope) -> Any:
        intent = normalize_send_intent(envelope.meta.get("intent") or "task")
        if intent not in self.HONOURED_SEND_INTENTS:
            raise AgentCommError(f"intent {intent!r} is not supported")
        if not isinstance(envelope.payload, str):
            raise AgentCommError("message must be a string")
        link = self._slim_link(self._require())
        target = str(envelope.meta.get("target") or "")
        if not target:
            raise AgentCommError("a2a-rpc send needs a target agent")
        if not link.actor.running:
            raise AgentCommError("slim link is not started")

        async def send() -> str:
            await link.aensure()
            # No retry: a SendMessage that reached the peer may already have run its task, and a second
            # one would run it again. A dead node is the link's to notice (its reader, or the RPC server
            # stopping), and the next send waits for the new connection.
            return await self._acall(link, target, envelope)

        return await link.actor.asubmit(send(), timeout=link.send_timeout_s)

    def _acall(self, link: Any, target: str, envelope: Envelope) -> Awaitable[str]:
        peer = link.rpc_name(target)
        meta = envelope.meta
        context_id = str(meta.get("context_id") or "")
        hop = _hop_map(dict(envelope.headers))
        metadata = {
            **dict(meta.get("metadata") or {}),
            "mas.correlation_id": str(int(meta.get("correlation_id") or 0)),
            "mas.caller_call_id": str(meta.get("caller_call_id") or ""),
            **hop,
        }
        hook = meta.get(SESSION_HOOK_META)
        text = str(envelope.payload)

        async def call() -> str:
            from slima2a.client_transport import ClientConfig, MultiAgentClientFactory, slimrpc_channel_factory

            _, app, conn_id = link.rpc_endpoint()
            base = slimrpc_channel_factory(app, conn_id)
            channels: list[Any] = []

            def open_channel(remote: str) -> Any:
                channel = base(remote)
                channels.append(channel)
                return channel

            factory = MultiAgentClientFactory(
                ClientConfig(
                    supported_protocol_bindings=[BINDING], streaming=False, slimrpc_channel_factory=open_channel
                )
            )
            client = factory.create(card=slimrpc_agent_card(target, peer))
            message = new_text_message(text, context_id=context_id or None, role=Role.ROLE_USER)
            if meta.get("message_id"):
                # a layer above resends the same message after a challenge: same id, so a verifier binding to it agrees
                message.message_id = str(meta["message_id"])
            message.metadata.update(metadata)

            async def send(sent: Any) -> list[StreamResponse]:
                return [r async for r in client.send_message(request=SendMessageRequest(message=sent))]

            loop = asyncio.get_running_loop()

            closing: set[asyncio.Task[None]] = set()

            def start_close(channel: Any) -> None:
                # a task the loop only holds weakly could be collected mid-close: keep it until it is done
                task = loop.create_task(_close(channel))
                closing.add(task)
                task.add_done_callback(closing.discard)

            def close_now() -> None:
                # the hook's identity was revoked: whatever channel this call holds closes, from any thread
                for channel in list(channels):
                    loop.call_soon_threadsafe(start_close, channel)

            forget = hook.on_invalidate(close_now) if hook is not None else None
            try:
                # a peer that wants proof answers with an auth-required task; the hook replies on that task, once
                responses = await complete(await send(message), hook, send)
            except AgentCommError:
                raise
            except Exception as exc:
                refused = _unauthenticated(exc) if hook is not None else None
                if refused is not None:
                    raise refused from None
                # a closed reason: the SDK's text is the peer's to write
                raise peer_error(exc) from None
            finally:
                if forget is not None:
                    forget()
                # an open channel keeps its session, and the peer's server waits for it when it shuts down
                for channel in channels:
                    await _close(channel)
            return reply_text(responses)

        return call()


async def _close(channel: Any) -> None:
    try:
        await channel.close_async(None)
    except Exception:
        logger.debug("a2a-rpc channel did not close", exc_info=True)


def _unauthenticated(exc: Exception) -> Unauthenticated | None:
    """``exc`` as the peer's unauthenticated answer with its challenge, or ``None`` for any other failure."""
    import slim_bindings as sb

    if isinstance(exc, sb.RpcError.Rpc) and exc.code == sb.RpcCode.UNAUTHENTICATED:
        challenge, wire, carry = challenge_from_message(str(exc.message))
        return Unauthenticated(challenge, wire, carry)
    return None


def _carried_headers(raw: Any) -> dict[str, str]:
    """Headers a peer carried in message metadata, validated. Anything malformed is none: the peer showed nothing."""
    if not isinstance(raw, dict):
        return {}
    try:
        return validate_headers({str(k): str(v) for k, v in raw.items()})
    except AgentCommError:
        logger.warning("ignoring malformed %s metadata", HEADERS_KEY)
        return {}


_UNAUTHENTICATED = "unauthenticated"
_MAX_MESSAGE_BYTES = 32768


def challenge_message(
    challenge: dict[str, Any], wire: str | None = None, carry: Mapping[str, str] | None = None
) -> str:
    """The RPC error message of an unauthenticated answer: the code, then what the verifier asked as compact JSON.

    slimrpc carries an error's code and message to the caller but not its ``details``, so the message is where a
    challenge can travel: the structured ``challenge``, the serialized ``wire`` challenge a scheme built for a peer with
    its own shape (verbatim) and ``carry`` (how it asked for it to be carried), each only when there is one. It is for
    the caller, so it holds no secret.
    """
    body: dict[str, Any] = {}
    if challenge:
        body["challenge"] = challenge
    if wire is not None:
        body["wire"] = wire
    if carry:
        body["carry"] = dict(carry)
    if not body:
        return _UNAUTHENTICATED
    return f"{_UNAUTHENTICATED}:{json.dumps(body, sort_keys=True, separators=(',', ':'), default=str)}"


def challenge_from_message(message: str) -> tuple[dict[str, Any], str | None, dict[str, str]]:
    """``(challenge, wire, carry)`` of an unauthenticated message; empty ones when it carries none, is malformed or is
    too large."""
    _, _, raw = message.partition(":")
    if not raw or len(raw.encode()) > _MAX_MESSAGE_BYTES:
        return {}, None, {}
    try:
        found = json.loads(raw)
    except ValueError:
        return {}, None, {}
    if not isinstance(found, dict):
        return {}, None, {}
    challenge = found.get("challenge")
    wire = found.get("wire")
    carry = found.get("carry")
    return (
        dict(challenge) if isinstance(challenge, dict) else {},
        wire if isinstance(wire, str) else None,
        {str(k): str(v) for k, v in carry.items()} if isinstance(carry, dict) else {},
    )


_STREAMING_RPCS = frozenset({"SubscribeToTask"})
#: Every slimrpc method of the A2A service other than the two sends.
_OTHER_RPCS = (
    "CancelTask",
    "SubscribeToTask",
    "GetTask",
    "ListTasks",
    "GetTaskPushNotificationConfig",
    "CreateTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
    "GetExtendedAgentCard",
)


def _gated_handler(base: Any, card: AgentCard, request_handler: Any, refused: _Refusals, *, gated: bool = False) -> Any:
    """The slimrpc servicer, answering an unauthenticated caller with the RPC's UNAUTHENTICATED error.

    The layers above decide inside the turn (the executor); a refusal the turn raised is found here by the
    message id, so the handler of the agent never ran and the caller learns only that it is unauthenticated,
    plus the challenge, which is meant for it.

    With ``gated`` (a layer above verifies callers) every other RPC is refused too: task reads, cancel, subscribe, the
    push-notification config calls and the extended card carry no message, so no evidence has travelled with them
    and nothing here can say who is asking. Without ``gated`` they are the SDK's, as before.
    """
    import slim_bindings as sb

    def unauthenticated(found: PeerChallenge) -> Exception:
        return sb.RpcError.Rpc(
            code=sb.RpcCode.UNAUTHENTICATED,
            message=challenge_message(found.challenge, found.wire, found.carry),
            details=None,
        )

    def closed() -> Exception:
        return unauthenticated(PeerChallenge({}, code="refused:no_evidence"))

    def refuse_task_continuation(request: Any) -> None:
        # The SDK keeps these tasks for the transport's user, not for the verified owner (verification happens inside
        # the turn, after the SDK has read the task): a send that names a task could append to another caller's. The
        # turn of this server never leaves a task open, so a caller has none of its own to continue.
        if gated and request.message.task_id:
            raise sb.RpcError.Rpc(code=sb.RpcCode.INVALID_ARGUMENT, message="refused:task_continuation", details=None)

    class Gated(base):  # type: ignore[misc, valid-type]
        async def SendMessage(self, request: Any, context: Any) -> Any:  # noqa: N802
            refuse_task_continuation(request)
            key = uuid.uuid4().hex  # this RPC's own key: never a message id the caller chose
            _RPC_TOKEN.set(key)
            response = await super().SendMessage(request, context)
            found = refused.pop(key)
            if found is not None:
                raise unauthenticated(found)
            return response

        async def SendStreamingMessage(self, request: Any, context: Any) -> Any:  # noqa: N802
            refuse_task_continuation(request)
            key = uuid.uuid4().hex
            _RPC_TOKEN.set(key)
            try:
                async for item in super().SendStreamingMessage(request, context):
                    yield item
            finally:
                found = refused.pop(key)
            if found is not None:
                raise unauthenticated(found)

    if gated:
        for name in _OTHER_RPCS:

            async def refuse(self: Any, request: Any, context: Any) -> Any:
                raise closed()

            async def refuse_stream(self: Any, request: Any, context: Any) -> Any:
                raise closed()
                yield  # pragma: no cover  (makes this an async generator, as the streaming RPC is)

            setattr(Gated, name, refuse_stream if name in _STREAMING_RPCS else refuse)
    return Gated(card, request_handler)

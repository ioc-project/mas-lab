#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SLIM L7 link: unicast, anycast, and group sessions."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.headers import merge_headers
from mas.library.standard.lib.comm.names import instance_name, namespace_of, parse_name, require_instance_name
from mas.library.standard.lib.comm.payload import (
    INFORM_ASK_INTENTS,
    MAX_MESSAGE_SIZE,
    check_message_size,
    inbound_payload,
    require_honoured_intent,
    send_result,
)
from mas.library.standard.lib.comm.send_context import align_send
from mas.library.standard.lib.stack.layer import ChainMember
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext

from library_ioa.plugins.slim.endpoint import assert_endpoint_allowed
from library_ioa.plugins.slim.inbound import (
    ENVELOPE_SLACK,
    HANDLER_ERROR,
    HANDLING_LINK,
    MAX_INFLIGHT_FRAMES,
    NOT_SERVED,
    SlimInbound,
)
from library_ioa.plugins.slim.names import instance_uuid as slim_instance_uuid
from library_ioa.plugins.slim.names import local_id as slim_local_id
from library_ioa.plugins.slim.names import slim_app_triple
from library_ioa.plugins.slim.service import SlimService

logger = logging.getLogger(__name__)


def _refuse_dots(parts: tuple[str, str, str, str]) -> None:
    """The app name is ``<service>.<agent>`` and the bare service is ``<service>``: a dot in either part
    makes two different services or agents share a name."""
    for label, part in (("service", parts[2]), ("agent id", parts[3])):
        if "." in part:
            raise AgentCommError(f"SLIM {label} cannot contain '.': {part!r}")


class SlimAgentComm(SlimInbound, ChainMember, AgentCommContract):
    """layer/slim: the SLIM transport, the bottom layer of a stack.

    A stack builds one per local agent. ``shared_service`` gives every agent
    on the stack one ``SlimService`` (one ``ConnectionActor``), passed back as
    ``service=``; the stack closes it after the last agent's link stops. A
    link never owns the actor.

    The row's ``identity`` (``org/ns/service``) names the agents: each one is
    ``<identity>/<agent_id>`` (``agent_name``). The service takes the secret
    value: ctl reads the row's ``secret_env`` and passes ``secret``. One event
    loop is shared; each agent opens its own node connection.

    Names and signatures from the slim-bindings 2.2.0 wheel:
    ``new_insecure_client_config(endpoint) -> ClientConfig``,
    ``new_secure_client_config(endpoint) -> ClientConfig``,
    ``Service.connect_async(config) -> int``, ``Service.disconnect(conn_id)``,
    ``create_app_with_secret(name, secret)``, ``subscribe_async``,
    ``set_route_async``, ``create_session_async`` -> session + completion,
    ``listen_for_session_async(timeout: timedelta | None) -> Session``,
    ``Session.get_message_async(timeout: timedelta | None)``,
    ``Session.publish_async`` / ``publish_to_async`` -> handle.wait_async(),
    ``delete_session_async(session) -> handle``. Binding errors are
    ``SlimError`` (including ``SlimError.Timeout``).
    """

    plugin_id = "slim"
    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set(unicast=True, anycast=True, group=True, ordered=True)
    HONOURED_SEND_INTENTS = INFORM_ASK_INTENTS

    @classmethod
    def shared_service(cls, params: Mapping[str, Any]) -> SlimService:
        """One node connection actor for every agent on a stack."""
        return SlimService.from_params(params)

    @classmethod
    def agent_name(cls, params: Mapping[str, Any], agent_id: str) -> str:
        """An agent's name on the node: ``<identity>/<agent_id>``."""
        return instance_name(str(params.get("identity") or ""), agent_id)

    @classmethod
    def check_agent_id(cls, params: Mapping[str, Any], agent_id: str) -> None:
        """Refuse an agent id or identity SLIM cannot name: a '.' in the service or the agent."""
        _refuse_dots(require_instance_name(cls.agent_name(params, agent_id)))

    def __init__(
        self,
        run_turn: Callable[..., str] | None = None,
        *,
        name: str,
        service: SlimService,
        agent_id: str = "",
    ) -> None:
        if not isinstance(service, SlimService):
            raise AgentCommError("slim link requires the stack's shared service")
        actor = service.actor
        parts = require_instance_name(name)
        self.name = "/".join(parts)
        self.namespace = namespace_of(parts)
        self.service = "/".join(parts[:3])
        self.agent_id = parts[3]
        _refuse_dots(parts)
        self.local_agent_id = slim_local_id(self.name, agent_id)
        self._actor = actor
        self._serve_handler: Callable[[Any], Any] | None = None
        self.endpoint = actor.endpoint
        self.fabric_id: int | None = None
        self.dev = actor.dev
        self.max_message_size = int(service.max_message_size or MAX_MESSAGE_SIZE)
        self.timeout_s = actor.timeout_s
        self.reply_timeout_s = actor.reply_timeout_s
        self._secret = service.secret  # checked by the service
        self._instance_id = ""
        self._run_turn = run_turn
        self._sticky: dict[str, str] = {}
        self._app: Any = None
        self._service: Any = None
        self._conn_id: Any = None
        self._sb: Any = None
        self._p2p: dict[str, Any] = {}
        self._groups: dict[str, Any] = {}
        self._channel_members: dict[str, set[str]] = {}
        # Sessions this link closed, by id. The session is kept so its id cannot be given to a later session.
        self._closed: dict[int, Any] = {}
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._stopped = False
        self._readers: list[asyncio.Task[None]] = []
        self._frames: set[asyncio.Task[None]] = set()
        self._inflight = asyncio.Semaphore(MAX_INFLIGHT_FRAMES)
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._stop_deferred = False
        self._listening = False
        self._read_errors = 0
        self._need_reconnect = False
        self._reconnect_lock: asyncio.Lock | None = None

    def __repr__(self) -> str:
        return f"SlimAgentComm(name={self.name!r}, endpoint={self.endpoint!r})"

    @property
    def _timeout(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=self.timeout_s)

    @property
    def _send_timeout(self) -> float:
        """The longest one send may take: the peer's reply, plus the routes and the session before it."""
        return self.reply_timeout_s + self.timeout_s * 4

    def honoured_send_intents(self) -> frozenset[str]:
        return self.HONOURED_SEND_INTENTS

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)

    def bind(self, lower: object) -> None:
        del lower

    def serve(self, handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """Hand every inbound request to ``handler`` (a stack's inbound chain) instead of the turn.

        The handler gets the request as an envelope (``payload`` the task,
        ``meta`` the sender, intent and call ids) and returns the reply text.
        A link that is never given a handler calls its ``run_turn`` itself.
        """
        self._serve_handler = handler
        return handler

    def start(self) -> None:
        self._stopped = False
        if self._actor.failed:
            raise AgentCommError("slim link is failed")
        self._actor.start()
        self._run(self._astart())

    def stop(self) -> None:
        self._stopped = True
        if HANDLING_LINK.get() is self:  # called by this link's own handler: stop after the reply is sent
            self._stop_deferred = True
            return
        actor = self._actor
        if actor.failed or not actor.running:
            return
        if threading.current_thread() is actor.thread:
            actor.spawn(self._astop())
            return
        try:
            self._run(self._astop())
        except AgentCommError:
            pass

    async def _astart(self) -> None:
        if self._app is not None:
            return
        self._stopped = False
        actor = self._actor
        assert_endpoint_allowed(self.endpoint, dev=self.dev)
        sb = actor.bindings
        if sb is None:
            raise AgentCommError("slim link is not connected")
        service, conn_id = await actor.open_connection(self.name)
        try:
            await self._aattach(service, conn_id, sb)
        except BaseException:
            # A start that fails half way must not leave its connection to the node open; a retry opens a new one.
            self._app = None
            actor.close_connection(service, conn_id)
            raise

    async def _aattach(self, service: Any, conn_id: Any, sb: Any) -> None:
        actor = self._actor
        self._sb = sb
        parts = [p for p in self.name.split("/") if p]
        # A frame's source_name is the app name without the instance, so each
        # agent needs its own app name for replies to route back to it.
        org, ns, third = slim_app_triple(self.name)
        app = self._sync_call(service.create_app_with_secret, sb.Name(org, ns, third), self._secret)
        self._instance_id = str(app.id())
        actor.register_instance(self.local_agent_id, self._instance_id)
        actor.register_instance(self.agent_id, self._instance_id)
        actor.register_source(third, self.local_agent_id)
        actor.register_source(self.agent_id, self.local_agent_id)
        await self._call(app.subscribe_async(sb.Name.new_with_id(org, ns, third, id=self._instance_id), conn_id))
        # Anycast targets the bare three-part service, so subscribe to it too.
        shared = sb.Name.new_with_id(parts[0], parts[1], parts[2], id=self._instance_id)
        await self._call(app.subscribe_async(shared, conn_id))
        await asyncio.sleep(actor.SUBSCRIBE_SETTLE_S)
        self._service = service
        self._conn_id = conn_id
        self._app = app
        self._start_listen_thread()
        await asyncio.sleep(0)

    async def _astop(self) -> None:
        self._stopped = True
        self._listening = False
        await self._adrop_link()

    async def _adrop_link(self) -> None:
        """Close this link's app, sessions and node connection. The actor stays up so a reconnect can open a new one."""
        sessions = _unique_sessions(list(self._groups.values()) + list(self._p2p.values()))
        self._groups.clear()
        self._p2p.clear()
        self._channel_members.clear()
        app = self._app
        service, conn_id = self._service, self._conn_id
        self._app = None
        self._service = None
        self._conn_id = None
        current = asyncio.current_task()
        readers = [task for task in self._readers if task is not current]
        self._readers.clear()
        for task in readers:
            task.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        if app is not None:
            for session in sessions:
                await self._aclose_session(app, session)
        if service is not None:
            self._actor.close_connection(service, conn_id)

    async def _areconnect(self) -> None:
        """Drop the current connection and open a new one. Senders wait here until the new app is up."""
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()
        async with self._reconnect_lock:
            if self._stopped:
                raise AgentCommError("slim link is stopped")
            if self._app is not None and not self._need_reconnect:
                return
            logger.warning("slim reconnecting")
            await self._adrop_link()
            self._need_reconnect = False
            self._read_errors = 0
            try:
                await self._astart()
            except BaseException:
                self._need_reconnect = True
                raise

    def send(
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
        if self._actor.failed:
            raise AgentCommError("slim link is failed")
        if self._stopped:
            raise AgentCommError("slim link is stopped")
        if not self._actor.running:
            self.start()
        return self._actor.submit(
            self._asend_impl(
                target_agent_id,
                task,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
                context_id=context_id,
                headers=headers,
                context=context,
            ),
            timeout=self._send_timeout,
        )

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
        if self._stopped:
            raise AgentCommError("slim link is stopped")
        if not self._actor.running:
            await asyncio.to_thread(self.start)
        return await self._actor.asubmit(
            self._asend_impl(
                target_agent_id,
                task,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
                context_id=context_id,
                headers=headers,
                context=context,
            ),
            timeout=self._send_timeout,
        )

    async def _asend_impl(
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
        await self._aensure()
        # a name part is letters, digits, '.', '_' and '-': nothing else reaches a node name
        parse_name(target_agent_id)
        check_message_size(task, max_size=self.max_message_size)
        headers, context = align_send(headers, context, sender=self.name)
        kind = require_honoured_intent(self, context.intent)
        labelled = inbound_payload(task, sender=context.sender, intent=kind)
        raw = json.dumps(
            {
                "kind": "request",
                "task": labelled,
                "intent": kind,
                "headers": merge_headers(headers),
                "correlation_id": correlation_id,
                "caller_call_id": caller_call_id,
                "context_id": context_id,
            }
        ).encode("utf-8")
        # One request at a time per target: they share a session, so a second request's reply would
        # otherwise be read by the first.
        async with self._target_locks.setdefault(target_agent_id, asyncio.Lock()):
            session = await self._p2p_session(target_agent_id)
            try:
                await self._wait_handle(session.publish_async(raw, None, None))
                reply = await self._call(
                    session.get_message_async(timeout=datetime.timedelta(seconds=self.reply_timeout_s))
                )
            except BaseException:
                # No reply was read (or none was sent): a late one would answer the next request.
                await self._drop_p2p_session(session)
                raise
        if len(reply.payload) > self.max_message_size + ENVELOPE_SLACK:
            raise AgentCommError(HANDLER_ERROR)
        try:
            incoming = json.loads(reply.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AgentCommError(HANDLER_ERROR) from exc
        if not isinstance(incoming, dict) or str(incoming.get("kind") or "") == "error":
            text = str(incoming.get("error") or "") if isinstance(incoming, dict) else ""
            raise AgentCommError(text if text in {HANDLER_ERROR, NOT_SERVED} else HANDLER_ERROR)
        source = str(reply.context.source_name or "")
        parts = tuple(p for p in target_agent_id.split("/") if p)
        if len(parts) == 3 and source:
            self._sticky[target_agent_id] = source
        return send_result(kind, str(incoming.get("task") or ""))

    async def _drop_p2p_session(self, session: Any) -> None:
        """Forget a point-to-point session and close it, so the next request opens a fresh one."""
        self._closed[id(session)] = session
        for key in [key for key, cached in self._p2p.items() if cached is session]:
            del self._p2p[key]
        if self._app is not None:
            await asyncio.shield(self._aclose_session(self._app, session))

    def create_channel(self, name: str, members: list[str], options: dict[str, Any] | None = None) -> str:
        self._run(self._acreate_channel(name, members, options))
        return name

    async def _acreate_channel(self, name: str, members: list[str], options: dict[str, Any] | None = None) -> str:
        await self._aensure()
        options = dict(options or {})
        sb = self._sb
        cfg = sb.SessionConfig(
            session_type=sb.SessionType.GROUP,
            max_retries=options.get("max_retries"),
            interval=options.get("interval"),
            metadata={"channel": name},
            mls_settings=options.get("mls_settings"),
        )
        dest = self._pyname(f"{self.namespace}/{name}")
        await self._call(self._app.set_route_async(dest, self._conn_id))
        ctx = await self._call(self._app.create_session_async(cfg, dest))
        await self._call(ctx.completion.wait_async())
        session = ctx.session
        roster = {self._full(m) for m in members}
        roster.add(self.name)
        for member in roster:
            await self._wait_handle(session.invite_async(self._pyname(member)))
        self._channel_members[name] = roster
        self._groups[name] = session
        self._closed.pop(id(session), None)
        return name

    def invite(self, channel: str, member: str) -> None:
        self._run(self._ainvite(channel, member))

    async def _ainvite(self, channel: str, member: str) -> None:
        session = await self._agroup(channel)
        full = self._full(member)
        await self._wait_handle(session.invite_async(self._pyname(full)))
        self._channel_members.setdefault(channel, set()).add(full)

    def remove(self, channel: str, member: str) -> None:
        self._run(self._aremove(channel, member))

    async def _aremove(self, channel: str, member: str) -> None:
        session = await self._agroup(channel)
        full = self._full(member)
        await self._wait_handle(session.remove_async(self._pyname(full)))
        roster = self._channel_members.setdefault(channel, set())
        roster.discard(full)
        roster.discard(member)

    def close_channel(self, channel: str) -> None:
        self._run(self._aclose_channel(channel))

    async def _aclose_channel(self, channel: str) -> None:
        session = await self._agroup(channel)
        await self._aclose_session(self._app, session)
        self._channel_members.pop(channel, None)

    def members(self, channel: str) -> list[str]:
        roster = self._channel_members.get(channel)
        if roster is not None:
            return sorted({m.split("/")[-1] for m in roster})
        return self._run(self._amembers(channel))

    async def _amembers(self, channel: str) -> list[str]:
        session = await self._agroup(channel)
        listed = await self._call(session.participants_list_async())
        names: set[str] = set()
        for member in listed:
            ident = str(member).split("/")[-1]
            names.add(self._actor.agent_of(ident) or ident)
        return sorted(names)

    def publish(
        self,
        channel: str,
        payload: str,
        *,
        to: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        self._run(self._apublish(channel, payload, to=to, headers=headers))
        return {"channel": channel, "sender": self.agent_id}

    async def _apublish(
        self,
        channel: str,
        payload: str,
        *,
        to: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        await self._aensure()
        check_message_size(payload, max_size=self.max_message_size)
        session = await self._agroup(channel)
        if id(session) in self._closed:
            raise AgentCommError(f"channel {channel!r} is closed")
        listed = {str(m) for m in await self._call(session.participants_list_async())}
        shorts = {m.split("/")[-1] for m in listed}
        mine = {
            self.agent_id,
            self.local_agent_id,
            self._instance_id,
            slim_instance_uuid(self.agent_id),
            self.name,
        }
        if shorts.isdisjoint(mine) and self.name not in listed:
            raise AgentCommError(f"{self.agent_id!r} is not a member of {channel!r}")
        envelope = {
            "kind": "message",
            "channel": channel,
            "payload": payload,
            "headers": merge_headers(headers),
            "to": to,
        }
        await self._wait_handle(session.publish_async(json.dumps(envelope).encode("utf-8"), None, None))

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    async def _aensure(self) -> None:
        if self._stopped:
            raise AgentCommError("slim link is stopped")
        if self._actor.failed:
            raise AgentCommError("slim link is failed")
        if self._app is None or self._need_reconnect:
            await self._areconnect()

    async def _agroup(self, channel: str) -> Any:
        session = self._groups.get(channel)
        if session is not None:
            if id(session) in self._closed:
                raise AgentCommError(f"channel {channel!r} is closed")
            return session
        await self._aadopt_pending()
        session = self._groups.get(channel)
        if session is not None:
            if id(session) in self._closed:
                raise AgentCommError(f"channel {channel!r} is closed")
            return session
        raise AgentCommError(f"unknown channel {channel!r}")

    def _full(self, member: str) -> str:
        if member.count("/") >= 3:
            return member
        return f"{self.service}/{member}"

    def _pyname(self, value: str) -> Any:
        parts = [p for p in value.split("/") if p]
        if len(parts) >= 4:
            return self._peer_name(*slim_app_triple(value), parts[3])
        if len(parts) == 1:
            return self._peer_name(*slim_app_triple(f"{self.service}/{parts[0]}"), parts[0])
        return self._sb.Name(parts[0], parts[1], parts[2])

    def _peer_name(self, org: str, ns: str, app: str, agent_id: str) -> Any:
        """Unicast to the instance this actor linked; otherwise name the app.

        A peer in another process has no instance id here, and an id-less
        name reaches the node's one subscriber for that app name.
        """
        instance = self._actor.instance_of(agent_id)
        if instance:
            return self._sb.Name.new_with_id(org, ns, app, id=instance)
        return self._sb.Name(org, ns, app)

    async def _p2p_session(self, target: str) -> Any:
        key = self._sticky.get(target) or target
        cached = self._p2p.get(key) or self._p2p.get(target)
        if cached is not None and id(cached) not in self._closed:
            return cached
        dest_name = key if key.count("/") >= 2 else self._full(key)
        dest = self._pyname(dest_name if dest_name.count("/") >= 2 else self._full(dest_name))
        await self._call(self._app.set_route_async(dest, self._conn_id))
        await asyncio.sleep(self._actor.ROUTE_SETTLE_S)
        cfg = self._sb.SessionConfig(
            session_type=self._sb.SessionType.POINT_TO_POINT,
            max_retries=5,
            interval=datetime.timedelta(seconds=1),
            metadata={},
            mls_settings=None,
        )
        try:
            ctx = await self._call(self._app.create_session_async(cfg, dest))
            await self._call(ctx.completion.wait_async())
        except AgentCommError as exc:
            if "retries exhausted" in str(exc):
                raise AgentCommError(f"no peer answers to {target!r}") from exc
            raise
        session = ctx.session
        self._p2p[target] = session
        self._p2p[key] = session
        return session

    async def _aclose_session(self, app: Any, session: Any) -> None:
        self._closed[id(session)] = session
        try:
            await self._wait_handle(app.delete_session_async(session))
        except AgentCommError:
            return

    async def _wait_handle(self, value: Any) -> Any:
        handle = await self._call(value)
        await self._call(handle.wait_async())
        return handle

    def _sync_call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except AgentCommError:
            raise
        except (AttributeError, TypeError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            # create_app_with_secret takes the secret, and a binding may echo its arguments in an error
            text = str(exc).replace(self._secret, "***") if self._secret else str(exc)
            raise AgentCommError(text or "slim binding error") from None

    async def _call(self, value: Any) -> Any:
        try:
            if asyncio.iscoroutine(value):
                return await value
            return value
        except AgentCommError:
            raise
        except asyncio.CancelledError:
            raise
        except (AttributeError, TypeError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise AgentCommError(str(exc) or "slim binding error") from exc

    def _run(self, coro: Any) -> Any:
        if not asyncio.iscoroutine(coro):
            return coro
        return self._actor.submit(coro)


def _unique_sessions(sessions: list[Any]) -> list[Any]:
    seen: set[int] = set()
    out: list[Any] = []
    for session in sessions:
        key = id(session)
        if key in seen:
            continue
        seen.add(key)
        out.append(session)
    return out

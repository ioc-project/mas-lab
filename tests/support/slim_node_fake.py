#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process stand-in for slim-bindings 2.2: test support, shipped in no package, not a real node."""

from __future__ import annotations

import asyncio
import datetime
import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError


class SlimError(Exception):
    """Stand-in for slim_bindings.SlimError. Timeout is a subclass."""


class Timeout(SlimError):
    pass


class SessionError(SlimError):
    pass


class AuthError(SlimError):
    pass


SlimError.Timeout = Timeout  # type: ignore[attr-defined]
SlimError.SessionError = SessionError  # type: ignore[attr-defined]
SlimError.AuthError = AuthError  # type: ignore[attr-defined]


class SessionType(Enum):
    POINT_TO_POINT = "point_to_point"
    GROUP = "group"


@dataclass
class SessionConfig:
    session_type: SessionType
    max_retries: int | None = None
    interval: Any = None
    metadata: dict[str, str] = field(default_factory=dict)
    mls_settings: Any = None


@dataclass
class Name:
    """A SLIM name, as the bindings have it: ``id()`` is a method, ``NULL_COMPONENT`` when unset."""

    org: str
    ns: str
    svc: str
    instance: str | None = None

    def id(self) -> str:
        return self.instance or "NULL_COMPONENT"

    def __str__(self) -> str:
        parts = [self.org, self.ns, self.svc]
        if self.instance:
            parts.append(self.instance)
        return "/".join(parts)

    @classmethod
    def from_string(cls, value: str) -> Name:
        parts = [p for p in value.split("/") if p]
        if len(parts) < 3:
            raise AgentCommError(f"SLIM name needs org/ns/svc, got {value!r}")
        ident = parts[3] if len(parts) > 3 else None
        return cls(parts[0], parts[1], parts[2], ident)

    @classmethod
    def new_with_id(cls, org: str, ns: str, svc: str, id: str) -> Name:
        return cls(org, ns, svc, id)


def _timeout_seconds(timeout: Any) -> float:
    if timeout is None:
        return 0.0
    if isinstance(timeout, datetime.timedelta):
        return float(timeout.total_seconds())
    return float(timeout)


async def _wait_until(ready: Any, timeout: Any) -> None:
    delay = _timeout_seconds(timeout)
    if delay <= 0:
        return
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if ready():
            return
        await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


@dataclass
class FakeMessageContext:
    """What a received message says about its sender: the app name, without the instance id."""

    source_name: Name
    metadata: dict[str, str] = field(default_factory=dict)


def _ctx(owner: Any) -> FakeMessageContext:
    # A real node reports the sender's three-part app name, without the instance id.
    name = owner._name
    return FakeMessageContext(Name(name.org, name.ns, name.svc))


def _msg(payload: bytes, ctx: Any) -> Any:
    return type("Msg", (), {"payload": payload, "context": ctx})()


@dataclass
class _Done:
    session: FakeSession | None = None

    async def wait_async(self) -> None:
        if self.session is not None:
            self.session.ready = True


@dataclass
class FakeSessionContext:
    session: FakeSession
    completion: _Done = field(init=False)

    def __post_init__(self) -> None:
        self.completion = _Done(self.session)

    def session_id(self) -> int:
        return id(self.session)


@dataclass
class FakeSession:
    world: FakeWorld
    owner: FakeApp
    destination: Name
    session_type: SessionType
    _metadata: dict[str, str] = field(default_factory=dict)
    members: set[str] = field(default_factory=set)
    closed: bool = False
    ready: bool = False
    inbox: deque = field(default_factory=deque)
    bound_instance: str | None = None
    published_to: list[bytes] = field(default_factory=list)
    last_timeout: Any = None
    pair: FakeSession | None = None
    group_id: int | None = None
    initiator: bool = False

    def is_initiator(self) -> bool:
        return self.initiator

    def metadata(self) -> dict[str, str]:
        return dict(self._metadata)

    async def invite_async(self, name: Name) -> _Done:
        if not self.ready:
            raise SessionError("handshake incomplete")
        full = str(name)
        self.members.add(full)
        peer = self.world.find_app(name)
        if peer is not None and peer is not self.owner:
            view = FakeSession(
                world=self.world,
                owner=peer,
                destination=self.destination,
                session_type=self.session_type,
                _metadata=dict(self._metadata),
                members=self.members,
                pair=self,
                group_id=self.group_id if self.group_id is not None else id(self),
            )
            self.group_id = view.group_id
            peer.sessions.append(view)
            peer._pending_sessions.append(view)
            self.world.sessions.append(view)
        return _Done()

    async def remove_async(self, name: Name) -> _Done:
        self.members.discard(str(name))
        return _Done()

    async def publish_async(self, payload: bytes, payload_type: Any = None, metadata: Any = None) -> _Done:
        del payload_type, metadata
        if self.closed:
            raise SessionError("session closed")
        if not self.ready:
            raise SessionError("handshake incomplete")
        if self.session_type is SessionType.POINT_TO_POINT:
            if self.pair is None:
                target = self._publish_target()
                raise SessionError(f"no peer {target or str(self.destination)!r}")
            self.world.deliver_to_session(self.pair, payload, sender=self.owner)
            return _Done()
        self.world.deliver_group(self, payload, sender=self.owner)
        return _Done()

    async def publish_to_async(self, ctx: FakeMessageContext | str | None, payload: bytes, *_rest: Any) -> _Done:
        if self.closed:
            raise SessionError("session closed")
        self.published_to.append(payload)
        if self.pair is not None:
            source = ctx.source_name if isinstance(ctx, FakeMessageContext) else None
            if source is not None and self.session_type is SessionType.POINT_TO_POINT:
                # A real node routes the reply by the app name: two apps that share it leave no route.
                # Routing is per node: only apps on the replier's node can match.
                apps = [
                    app
                    for app in self.world.by_app.get(str(Name(source.org, source.ns, source.svc))) or []
                    if app.node == self.owner.node and not app.stopped
                ]
                if len(apps) != 1 or apps[0] is not self.pair.owner:
                    raise SessionError("channel closed")
            self.world.deliver_to_session(self.pair, payload, sender=self.owner)
            return _Done()
        source = ctx.source_name if isinstance(ctx, FakeMessageContext) else ctx
        if source:
            view = self.world.group_view(self, str(source))
            if view is not None:
                self.world.deliver_to_session(view, payload, sender=self.owner)
                return _Done()
        if ctx is not None and isinstance(ctx, str):
            target = str(ctx)
            app = self.world._lookup(target)
            if app is None:
                raise SessionError(f"no peer {target!r}")
            view = self.world.group_view(self, target)
            if view is not None:
                self.world.deliver_to_session(view, payload, sender=self.owner)
            return _Done()
        return _Done()

    def _publish_target(self) -> str | None:
        if self.session_type is SessionType.POINT_TO_POINT:
            dest = str(self.destination)
            parts = dest.split("/")
            if len(parts) == 3:
                if self.bound_instance:
                    return self.bound_instance
                apps = [app for app in self.world.by_service.get(dest) or [] if app is not self.owner]
                if not apps:
                    return dest
                chosen = self.world.pick(apps)
                self.bound_instance = str(
                    Name.new_with_id(chosen._name.org, chosen._name.ns, chosen._name.svc, chosen.id())
                )
                return self.bound_instance
            return dest
        return None

    async def get_message_async(self, timeout: Any = None) -> Any:
        _check_loop()
        self.last_timeout = timeout
        if self.inbox:
            return self.inbox.popleft()
        await _wait_until(
            lambda: bool(self.inbox) or self.closed or self.owner.stopped,
            timeout,
        )
        if self.inbox:
            return self.inbox.popleft()
        if self.closed:  # a real binding names a closed session; it does not time out
            raise SessionError("session closed")
        raise Timeout("timeout")

    async def participants_list_async(self) -> list[str]:
        return sorted(self.members)

    def close(self) -> None:
        self.closed = True


class FakeApp:
    def __init__(self, name: Name, secret: str, world: FakeWorld, service: str) -> None:
        self._name = name
        self._secret = secret
        self.world = world
        self.service = service
        self._id = str(uuid.uuid4())
        self.sessions: list[FakeSession] = []
        self.inbox: deque = deque()
        self._pending_sessions: deque = deque()
        self.stopped = False
        self.node = ""
        world.apps[self._id] = self
        world.by_app.setdefault(str(Name(name.org, name.ns, name.svc)), []).append(self)
        world.by_instance[str(Name(name.org, name.ns, name.svc, self._id))] = self
        world.by_identity[str(name)] = self

    def id(self) -> str:
        return self._id

    async def subscribe_async(self, name: Name, conn: Any) -> None:
        _check_loop()
        # An app receives anycast only for the service names it subscribed.
        del conn
        self.world.subscriptions[str(name)] = self
        if name.instance:
            self.world.by_instance[str(name)] = self
        apps = self.world.by_service.setdefault(str(Name(name.org, name.ns, name.svc)), [])
        if self not in apps:
            apps.append(self)

    async def set_route_async(self, name: Name, connection_id: Any) -> None:
        del name, connection_id

    async def create_session_async(self, config: SessionConfig, destination: Name) -> FakeSessionContext:
        _check_loop()
        session = FakeSession(
            world=self.world,
            owner=self,
            destination=destination,
            session_type=config.session_type,
            _metadata=dict(config.metadata or {}),
            members={str(self._name)},
            initiator=True,
        )
        if config.session_type is SessionType.GROUP:
            session.group_id = id(session)
        self.sessions.append(session)
        self.world.sessions.append(session)
        if config.session_type is SessionType.POINT_TO_POINT:
            peer, bound = self.world.pair_peer(destination, exclude=self)
            session.bound_instance = bound
            if peer is not None:
                remote = FakeSession(
                    world=self.world,
                    owner=peer,
                    destination=self._name,
                    session_type=config.session_type,
                    _metadata=dict(config.metadata or {}),
                    members={str(self._name), str(peer._name)},
                    pair=session,
                    bound_instance=bound,
                )
                session.pair = remote
                session.members = remote.members
                peer.sessions.append(remote)
                peer._pending_sessions.append(remote)
                self.world.sessions.append(remote)
        return FakeSessionContext(session=session)

    async def listen_for_session_async(self, timeout: Any = None) -> FakeSession:
        _check_loop()
        if self._pending_sessions:
            return self._pending_sessions.popleft()
        await _wait_until(lambda: bool(self._pending_sessions) or self.stopped, timeout)
        if self._pending_sessions:
            return self._pending_sessions.popleft()
        raise Timeout("timeout")

    async def delete_session_async(self, session: FakeSession) -> _Done:
        session.closed = True
        if session in self.sessions:
            self.sessions.remove(session)
        return _Done()


class FakeService:
    def __init__(self, name: str, world: FakeWorld | None = None) -> None:
        self.name = name
        self.world = world or FakeWorld.default()
        self.connected = False
        self.endpoint = ""

    async def connect_async(self, config: Any) -> int:
        _check_loop()
        if self.world.down:
            raise SlimError("node down")
        endpoint = str(config or "")
        if "reject-tls" in endpoint:
            raise SlimError("TLS validation failed")
        self.connected = True
        self.endpoint = endpoint
        return 1

    def disconnect(self, conn_id: Any = None) -> None:
        del conn_id
        self.connected = False
        for app in self.world.apps.values():
            if app.service == self.name:
                app.stopped = True

    def create_app_with_secret(self, name: Name, secret: str) -> FakeApp:
        if self.world.require_secret and secret != self.world.require_secret:
            raise AuthError("bad credentials")
        if len(secret) < 32:
            raise AuthError("AuthError: hmac key is too short")
        app = FakeApp(name, secret, self.world, self.name)
        app.node = self.endpoint
        return app


class FakeWorld:
    _current: FakeWorld | None = None

    def __init__(self, *, seed: int | None = 0) -> None:
        self.apps: dict[str, FakeApp] = {}
        self.by_service: dict[str, list[FakeApp]] = defaultdict(list)
        self.by_app: dict[str, list[FakeApp]] = defaultdict(list)
        self.by_instance: dict[str, FakeApp] = {}
        self.by_identity: dict[str, FakeApp] = {}
        self.subscriptions: dict[str, FakeApp] = {}
        self.sessions: list[FakeSession] = []
        self.frames: list[bytes] = []
        self.down = False
        self.require_secret: str | None = None
        self._rng = random.Random(seed)

    @classmethod
    def default(cls) -> FakeWorld:
        if cls._current is None:
            cls._current = cls()
        return cls._current

    @classmethod
    def reset(cls, *, seed: int | None = 0) -> FakeWorld:
        global _LOOP
        _LOOP = None
        cls._current = cls(seed=seed)
        return cls._current

    def pick(self, apps: list[FakeApp]) -> FakeApp:
        ordered = sorted(apps, key=lambda item: item.id())
        return ordered[self._rng.randrange(len(ordered))]

    def find_app(self, name: Name) -> FakeApp | None:
        full = str(name)
        hit = self.by_identity.get(full) or self.by_instance.get(full)
        if hit is not None and not hit.stopped:
            return hit
        if name.instance:
            return None
        apps = [app for app in (self.by_service.get(full) or []) if not app.stopped]
        return apps[0] if apps else None

    def pair_peer(self, destination: Name, *, exclude: FakeApp | None = None) -> tuple[FakeApp | None, str | None]:
        if destination.instance:
            return self.find_app(destination), None
        key = str(destination)
        apps = [app for app in self.by_service.get(key) or [] if app is not exclude and not app.stopped]
        if not apps:
            return self.find_app(destination), None
        chosen = self.pick(apps)
        bound = str(Name.new_with_id(chosen._name.org, chosen._name.ns, chosen._name.svc, chosen.id()))
        return chosen, bound

    def deliver_to_session(self, session: FakeSession, payload: bytes, *, sender: FakeApp) -> None:
        self.frames.append(payload)
        session.inbox.append(_msg(payload, _ctx(sender)))

    def deliver_group(self, session: FakeSession, payload: bytes, *, sender: FakeApp) -> None:
        self.frames.append(payload)
        gid = session.group_id if session.group_id is not None else id(session)
        message = _msg(payload, _ctx(sender))
        for view in self.sessions:
            if view.session_type is not SessionType.GROUP:
                continue
            if (view.group_id if view.group_id is not None else id(view)) != gid:
                continue
            if view.owner is sender:
                continue
            view.inbox.append(message)

    def group_view(self, session: FakeSession, member: str) -> FakeSession | None:
        gid = session.group_id if session.group_id is not None else id(session)
        for view in self.sessions:
            if view.session_type is not SessionType.GROUP:
                continue
            if (view.group_id if view.group_id is not None else id(view)) != gid:
                continue
            if str(view.owner._name) == member or str(view.owner._name).endswith("/" + member.split("/")[-1]):
                return view
        return None

    def _lookup(self, name: str) -> FakeApp | None:
        app = self.by_identity.get(name) or self.by_instance.get(name) or self.subscriptions.get(name)
        if app is not None and not app.stopped:
            return app
        parts = [p for p in name.split("/") if p]
        if len(parts) == 4:
            prefix = "/".join(parts[:3])
            matches = [
                item
                for item in self.apps.values()
                if not item.stopped
                and item._name.instance == parts[3]
                and str(Name(item._name.org, item._name.ns, item._name.svc)) == prefix
            ]
            return matches[0] if len(matches) == 1 else None
        if len(parts) == 1:
            matches = [item for item in self.apps.values() if not item.stopped and item._name.instance == parts[0]]
            return matches[0] if len(matches) == 1 else None
        if len(parts) == 3:
            apps = [item for item in (self.by_service.get(name) or []) if not item.stopped]
            return apps[0] if apps else None
        return None


_INITIALIZED = True


_LOOP: Any = None


def uniffi_set_event_loop(loop: Any) -> None:
    """Like the real bindings: one process-wide loop, the last caller wins."""
    global _LOOP
    _LOOP = loop


def _check_loop() -> None:
    if _LOOP is not None and asyncio.get_running_loop() is not _LOOP:
        raise RuntimeError("future attached to a different event loop")


def is_initialized() -> bool:
    return _INITIALIZED


def initialize_with_defaults() -> None:
    return


def new_insecure_client_config(endpoint: str) -> str:
    return endpoint


def new_secure_client_config(endpoint: str) -> str:
    return endpoint


Service = FakeService

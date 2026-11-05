#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SLIM link plugin against fake bindings."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import slim_node_fake as slim_fake
from library_ioa.plugins.slim import SlimAgentComm
from library_ioa.plugins.slim.actor import _SHARED_LOOP, ConnectionActor
from library_ioa.plugins.slim.bindings import load_slim_bindings
from library_ioa.plugins.slim.endpoint import (
    assert_endpoint_allowed,
    require_secret,
    use_insecure_client_config,
)
from library_ioa.plugins.slim.names import (
    instance_uuid,
    local_id,
    slim_app_triple,
)
from library_ioa.plugins.slim.service import SlimService
from mas.library.standard.lib.comm.headers import get_headers
from mas.library.standard.lib.comm.payload import label_untrusted_peer
from mas.library.standard.lib.stack import StackError, StackHandle
from mas.library.standard.testing.agent_comm_checks import check_agent_comm
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, InboundSend, SendContext, inbound_send

FakeWorld = slim_fake.FakeWorld

_SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
_SLIM_DIR = Path(__file__).resolve().parents[1] / "src" / "library_ioa" / "plugins" / "slim"


def _ack(agent_id: str, task: str, *_args: object) -> str:
    """The turn of a served test agent: ``ack:<task>``."""
    del agent_id
    return f"ack:{task}"


class _Desk:
    """A one-layer ``slim`` stack. ``link`` returns the SLIM link at the bottom of that agent's stack.

    Every agent is served with the ``_ack`` turn unless the test gives another.
    """

    def __init__(self, *, identity: str, run_turn=_ack, **params: object) -> None:
        self.handle = StackHandle("test", [{"type": "slim", "params": {"identity": identity, **params}}], run_turn)
        self.identity = identity
        self.fabric_id = self.handle.fabric_id
        self._service = None

    def start(self) -> None:
        self.handle.start()
        self._service = self.handle._services[0]

    def stack(self, agent_id: str, run_turn=None):
        comm = self.handle.link(agent_id, run_turn)
        self._service = self.handle._services[0]
        return comm

    def link(self, agent_id: str, run_turn=None) -> SlimAgentComm:
        return self.stack(agent_id, run_turn)._layers[0].link

    @property
    def _actor(self) -> ConnectionActor:
        assert self._service is not None, "start the desk first"
        return self._service.actor

    def close(self) -> None:
        self.handle.close()


_DESKS: list[_Desk] = []


@pytest.fixture(autouse=True)
def _patch_slim_bindings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if not request.node.get_closest_marker("slim"):
        monkeypatch.setattr(
            "library_ioa.plugins.slim.actor.load_slim_bindings",
            lambda: slim_fake,
        )
        monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
        monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)
    yield
    for desk in _DESKS:
        try:
            desk.close()
        except Exception:
            pass
    _DESKS.clear()
    assert _SHARED_LOOP._refs == 0, "a test left a SLIM actor running"


def _desk(**kwargs: object) -> _Desk:
    params: dict[str, object] = {
        "identity": "acme/lab/mas",
        "url": "http://127.0.0.1:46357",
        "secret": _SECRET,
        "timeout_s": 0.25,
    }
    params.update(kwargs)
    desk = _Desk(**params)  # type: ignore[arg-type]
    _DESKS.append(desk)
    return desk


def _svc(url: str = "http://127.0.0.1:46357", **kwargs: object) -> SlimService:
    """A shared service built by hand, as a stack's ``shared_service`` builds one."""
    return SlimService(identity="acme/lab/mas", url=url, secret=_SECRET, **kwargs)  # type: ignore[arg-type]


def _set_run_turn(link: SlimAgentComm, handler) -> None:
    def _turn(agent_id: str, task: str, *_args: object, **_kw: object) -> str:
        del agent_id
        return handler(task)

    link._run_turn = _turn


def _link(name: str, **kwargs: object) -> SlimAgentComm:
    on_request = kwargs.pop("on_request", None)
    owner = kwargs.pop("desk", None)
    if owner is None and not kwargs and _DESKS:
        owner = _DESKS[-1]
    desk = owner if isinstance(owner, _Desk) else _desk(**kwargs)
    link = desk.link(str(name))
    if on_request is not None:
        _set_run_turn(link, on_request)
    return link


def _triple() -> list[SlimAgentComm]:
    FakeWorld.reset()
    desk = _desk()
    return [desk.link("a"), desk.link("b"), desk.link("c")]


def _wait_until(ready, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def _halt_listen(link: SlimAgentComm) -> None:
    link._stopped = True
    link._listening = False
    app = link._app
    if app is not None:
        app.stopped = True
    time.sleep(0.05)
    link._stopped = False
    if app is not None:
        app.stopped = False


def test_capabilities_omit_isolation_and_reliable() -> None:
    caps = SlimAgentComm.CAPABILITIES
    assert caps["isolation"] is False
    assert caps["reliable"] is False
    assert caps["unicast"] is True


def test_check_agent_comm_on_fake_bindings() -> None:
    links = _triple()

    def on_request(index: int, handler) -> None:
        _set_run_turn(links[index], handler)

    check_agent_comm(links, on_request=on_request)


def test_headers_cross_the_serialised_envelope() -> None:
    a, b, _c = _triple()
    seen: list[dict[str, str]] = []
    _set_run_turn(b, lambda task, **_kw: seen.append(get_headers()) or f"ok:{task}")
    assert a.send("b", "t", headers={"k": "v"}) == "ok:t"
    assert seen == [{"k": "v"}]
    a.create_channel("room", members=["a", "b", "c"], options={})
    got: list[dict[str, str]] = []
    b.subscribe(lambda msg: got.append(get_headers()))
    a.publish("room", "x", to="b", headers={"k": "pub"})
    _wait_until(lambda: got == [{"k": "pub"}])
    assert get_headers() == {}


def test_anycast_is_sticky() -> None:
    links = _triple()
    a, _b, _c = links
    first = a.send("acme/lab/mas", "ping")
    assert first == "ack:ping"
    a.send("acme/lab/mas", "again")
    session = a._p2p["acme/lab/mas"]
    assert session.bound_instance
    assert a._sticky.get("acme/lab/mas")


def test_anycast_seed_picks_a_stable_instance() -> None:
    FakeWorld.reset(seed=0)
    first = [_link("a"), _link("b"), _link("c")]
    first[0].send("acme/lab/mas", "ping")
    bound_zero = first[0]._p2p["acme/lab/mas"].bound_instance
    FakeWorld.reset(seed=1)
    second = [_link("a"), _link("b"), _link("c")]
    second[0].send("acme/lab/mas", "ping")
    bound_one = second[0]._p2p["acme/lab/mas"].bound_instance
    assert bound_zero
    assert bound_one


def test_insecure_endpoint_rejected_off_loopback() -> None:
    with pytest.raises(AgentCommError, match="127.0.0.1"):
        assert_endpoint_allowed("http://10.0.0.1:46357")
    with pytest.raises(AgentCommError, match="127.0.0.1"):
        assert_endpoint_allowed("http://10.0.0.1:46357", dev=True)
    assert_endpoint_allowed("http://127.0.0.1:46357")
    assert_endpoint_allowed("http://localhost:46357")
    assert_endpoint_allowed("http://[::1]:46357")
    assert_endpoint_allowed("https://10.0.0.1:46357")


def test_link_needs_the_stacks_shared_service() -> None:
    with pytest.raises(AgentCommError, match="requires the stack's shared service"):
        SlimAgentComm(name="acme/lab/mas/a", service=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="url"):
        SlimAgentComm(name="acme/lab/mas/a", url="http://127.0.0.1:46357")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"url": "http://127.0.0.1:46357", "secret": _SECRET}, "three-part"),
        ({"url": "http://127.0.0.1:46357", "secret": _SECRET, "identity": "acme/lab"}, "three-part"),
        ({"url": "http://127.0.0.1:46357", "identity": "acme/lab/mas"}, "requires a secret"),
        ({"secret": _SECRET, "identity": "acme/lab/mas"}, "requires url"),
        ({"url": "http://127.0.0.1:46357", "secret": _SECRET, "identity": "acme/lab/mas", "x": 1}, "unknown params"),
    ],
)
def test_the_shared_service_checks_the_row_params(params: dict, message: str) -> None:
    with pytest.raises(AgentCommError, match=message):
        SlimAgentComm.shared_service(params)


def test_an_agent_is_named_under_the_row_identity() -> None:
    assert SlimAgentComm.agent_name({"identity": "acme/lab/desk"}, "billing") == "acme/lab/desk/billing"
    assert SlimAgentComm.agent_name({"identity": "acme/lab/desk"}, "acme/lab/desk/billing") == "acme/lab/desk/billing"
    for bad in ("other/ns/svc/billing", "acme/lab/desk/x/billing"):
        with pytest.raises(AgentCommError, match="outside the identity"):
            SlimAgentComm.agent_name({"identity": "acme/lab/desk"}, bad)
    with pytest.raises(AgentCommError, match="three-part"):
        SlimAgentComm.agent_name({"identity": "acme/lab"}, "billing")


def test_plugin_takes_secret_value_not_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLIM_SECRET", raising=False)
    FakeWorld.reset()
    _desk(url="http://127.0.0.1:1").link("a")
    with pytest.raises(TypeError, match="secret"):
        SlimAgentComm(name="acme/lab/mas/a", service=_svc(), secret_env="SLIM_SECRET")  # type: ignore[call-arg]


def test_wrong_secret_and_short_secret() -> None:
    with pytest.raises(AgentCommError, match="too short"):
        require_secret("short")
    with pytest.raises(AgentCommError, match="requires a secret"):
        require_secret("")
    FakeWorld.reset()
    FakeWorld.default().require_secret = "other" + ("x" * 32)
    with pytest.raises(AgentCommError, match="credentials"):
        _desk(url="http://127.0.0.1:1").link("a")


def test_forged_sender_is_ignored() -> None:
    a, b, _c = _triple()
    assert a.send("b", "t") == "ack:t"
    a.create_channel("room", members=["a", "b"], options={})
    published: list[str] = []
    b.subscribe(lambda msg: published.append(str(msg["sender"])))
    a.publish("room", "x")
    _wait_until(lambda: published == ["a"])


def test_oversize_missing_peer_and_stop() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()
    a.max_message_size = 4
    with pytest.raises(AgentCommError, match="max size"):
        a.send("b", "too-big")
    with pytest.raises(AgentCommError, match="no peer"):
        a.send("missing-peer", "x")
    service = a._service
    a.stop()
    assert service is not None
    assert service.connected is False
    with pytest.raises(AgentCommError, match="stopped"):
        a.send("b", "x")
    _DESKS[-1].close()
    assert service.connected is False
    del b


def test_handler_error_reaches_the_caller(caplog: pytest.LogCaptureFixture) -> None:
    FakeWorld.reset()
    a, b, _c = _triple()

    def _boom(task: str, **_kw: object) -> str:
        raise RuntimeError("db password=hunter2 at /srv/x")

    _set_run_turn(b, _boom)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(AgentCommError, match="peer handler failed") as raised:
            a.send("b", "x")
    assert "hunter2" not in str(raised.value)
    assert "peer handler failed" in str(raised.value)
    assert "hunter2" in caplog.text
    wire = b._on_inbound(
        type(
            "Msg",
            (),
            {
                "payload": __import__("json").dumps({"kind": "request", "task": "x", "headers": {}}).encode("utf-8"),
                "context": type("Ctx", (), {"source_name": slim_fake.Name("acme", "lab", "mas")})(),
            },
        )()
    )
    assert wire is not None
    assert b"hunter2" not in wire
    assert b"/srv/x" not in wire
    assert b"peer handler failed" in wire


def test_asend_does_not_block_the_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from slim_node_fake import FakeSession

    FakeWorld.reset()
    a, b, _c = _triple()
    orig = FakeSession.get_message_async

    async def _slow(self: FakeSession, timeout: object = None) -> object:
        await asyncio.sleep(0.15)
        return await orig(self, timeout)

    monkeypatch.setattr(FakeSession, "get_message_async", _slow)
    ticks: list[int] = []

    async def _go() -> str:
        async def _ticker() -> None:
            await asyncio.sleep(0.05)
            ticks.append(1)

        task = asyncio.create_task(_ticker())
        result = await a.asend("b", "ping")
        await task
        return result

    assert asyncio.run(_go()) == "ack:ping"
    assert ticks == [1]


def test_first_asend_does_not_block_on_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from slim_node_fake import FakeService

    FakeWorld.reset()
    _link("b")
    orig = FakeService.connect_async

    async def _slow(self: FakeService, config: object) -> object:
        await asyncio.sleep(0.4)
        return await orig(self, config)

    monkeypatch.setattr(FakeService, "connect_async", _slow)
    a = SlimAgentComm(name="acme/lab/mas/a", service=_DESKS[-1]._service)
    ticks: list[int] = []

    async def _go() -> str:
        async def _ticker() -> None:
            await asyncio.sleep(0.05)
            ticks.append(1)

        task = asyncio.create_task(_ticker())
        result = await a.asend("b", "ping")
        await task
        return result

    assert asyncio.run(_go()) == "ack:ping"
    assert ticks == [1]


def test_send_passes_timedelta_timeout() -> None:
    import datetime

    FakeWorld.reset()
    a, b, _c = _triple()
    a.send("b", "ping")
    session = next(iter(a._p2p.values()))
    assert isinstance(session.last_timeout, datetime.timedelta)


def test_stop_closes_shared_p2p_session_once() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()
    a.send("b", "ping")
    session = next(iter(a._p2p.values()))
    a._p2p["alias"] = session
    a._groups["also"] = session
    n = {"n": 0}
    orig = a._app.delete_session_async

    async def _spy(session: object) -> object:
        n["n"] += 1
        return await orig(session)

    a._app.delete_session_async = _spy  # type: ignore[method-assign]
    a.stop()
    assert n["n"] == 1


def test_adopt_ignores_session_without_channel() -> None:
    FakeWorld.reset()
    a = _link("a")

    class _NoChannel:
        def metadata(self) -> dict[str, str]:
            return {}

    a._adopt_session(_NoChannel())
    assert a._groups == {}
    a._adopt_session(None)

    class _Room:
        def metadata(self) -> dict[str, str]:
            return {"channel": "room"}

    room = _Room()
    a._adopt_session(room)
    assert a._groups["room"] is room


def test_idle_inbound_loop_does_not_spin() -> None:
    import asyncio
    import time

    FakeWorld.reset()
    a = _link("a")
    a.timeout_s = 0.15
    _halt_listen(a)
    start = time.process_time()

    async def _go() -> None:
        task = asyncio.create_task(a._inbound_loop())
        await asyncio.sleep(0.3)
        a._stopped = True
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1)
        except asyncio.CancelledError:
            pass

    asyncio.run(_go())
    used = time.process_time() - start
    assert used < 0.15


def test_session_publish_requires_completion_wait() -> None:
    import asyncio

    from slim_node_fake import FakeService, Name, SessionConfig, SessionType

    FakeWorld.reset()
    svc = FakeService("svc", world=FakeWorld.default())
    app = svc.create_app_with_secret(Name.from_string("acme/lab/svc/a"), _SECRET)

    async def _go() -> None:
        ctx = await app.create_session_async(
            SessionConfig(session_type=SessionType.POINT_TO_POINT),
            Name.from_string("acme/lab/other"),
        )
        with pytest.raises(slim_fake.SessionError, match="handshake"):
            await ctx.session.publish_async(b"x")
        await ctx.completion.wait_async()
        with pytest.raises(slim_fake.SessionError, match="no peer"):
            await ctx.session.publish_async(b"x")

    asyncio.run(_go())


def test_stop_closes_sessions() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()
    a.send("b", "ping")
    a.create_channel("room", members=["a", "b"], options={})
    p2p = next(iter(a._p2p.values()))
    group = a._groups["room"]
    a.stop()
    assert p2p.closed is True
    assert group.closed is True


def test_stop_swallows_session_close_errors() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()
    a.send("b", "ping")

    async def _boom_binding(_session: object) -> None:
        raise slim_fake.SessionError("already closed")

    a._app.delete_session_async = _boom_binding  # type: ignore[method-assign]
    a.stop()
    with pytest.raises(AgentCommError, match="stopped"):
        a.send("b", "x")


def test_https_without_secure_factory_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoSecure:
        Name = slim_fake.Name
        Service = slim_fake.Service
        SessionConfig = slim_fake.SessionConfig
        SessionType = slim_fake.SessionType
        new_insecure_client_config = staticmethod(slim_fake.new_insecure_client_config)
        uniffi_set_event_loop = staticmethod(slim_fake.uniffi_set_event_loop)
        is_initialized = staticmethod(slim_fake.is_initialized)
        initialize_with_defaults = staticmethod(slim_fake.initialize_with_defaults)

    monkeypatch.setattr(
        "library_ioa.plugins.slim.actor.load_slim_bindings",
        lambda: _NoSecure,
    )
    FakeWorld.reset()
    with pytest.raises(AttributeError, match="new_secure_client_config"):
        _desk(url="https://example.test:46357").link("a")


def test_inbound_loop_adopts_invited_session() -> None:
    import json

    FakeWorld.reset()
    a, b, _c = _triple()
    a.create_channel("room", members=["a"], options={})
    a.invite("room", "b")
    _wait_until(lambda: "room" in b._groups)
    session = b._groups["room"]
    session.inbox.append(
        type(
            "Msg",
            (),
            {
                "payload": json.dumps({"kind": "request", "task": "ping", "headers": {}}).encode("utf-8"),
                "context": type("Ctx", (), {"source_name": slim_fake.Name("acme", "lab", "mas"), "metadata": {}})(),
            },
        )()
    )
    _wait_until(lambda: bool(session.published_to))
    assert b"peer handler failed" not in session.published_to[0]
    assert b'"kind": "reply"' in session.published_to[0]


def test_inbound_loop_logs_and_keeps_running(caplog: pytest.LogCaptureFixture) -> None:
    import asyncio

    FakeWorld.reset()
    a = _link("a")
    _halt_listen(a)
    calls = {"n": 0}

    async def _boom(*_args: object, **_kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("listen boom")
        a._stopped = True
        raise RuntimeError("stopped boom")

    a._app.listen_for_session_async = _boom  # type: ignore[method-assign]

    async def _go() -> None:
        with caplog.at_level(logging.ERROR):
            await asyncio.wait_for(a._inbound_loop(), timeout=1)

    asyncio.run(_go())
    assert "listen boom" in caplog.text


def test_listen_thread_adopts_invited_session() -> None:
    import time

    FakeWorld.reset()
    a, b, _c = _triple()
    a.create_channel("room", members=["a"], options={})
    a.invite("room", "b")
    b.timeout_s = 0.05
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    b._start_listen_thread()
    b._start_listen_thread()
    deadline = time.monotonic() + 1.0
    while "room" not in b._groups and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "room" in b._groups
    a.publish("room", "via-listen")
    deadline = time.monotonic() + 1.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == ["via-listen"]
    assert b._listening and b._actor.thread is not None and b._actor.thread.is_alive()
    b.stop()
    _DESKS[-1].close()
    assert _DESKS[-1]._actor.thread is None


def test_start_without_injected_bindings_uses_loaded_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "library_ioa.plugins.slim.actor.load_slim_bindings",
        lambda: slim_fake,
    )
    FakeWorld.reset()
    desk = _desk()
    a = desk.link("a")
    b = desk.link("b")
    assert a._listening and a._actor.thread is not None and a._actor.thread.is_alive()
    assert a.send("b", "hello") == "ack:hello"
    a.stop()
    b.stop()
    with pytest.raises(TypeError, match="bindings"):
        SlimAgentComm(name="acme/lab/mas/a", service=_svc(), bindings=slim_fake)  # type: ignore[call-arg]


def test_secret_never_in_logs_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    FakeWorld.reset()
    with caplog.at_level(logging.DEBUG):
        link = _link("a")
    text = caplog.text + repr(link) + str(link)
    assert _SECRET not in text
    assert "secret=" not in repr(link)


def test_plugin_source_does_not_read_env_or_world() -> None:
    agent = (_SLIM_DIR / "agent_comm.py").read_text(encoding="utf-8")
    actor = (_SLIM_DIR / "actor.py").read_text(encoding="utf-8")
    endpoint = (_SLIM_DIR / "endpoint.py").read_text(encoding="utf-8")
    inbound = (_SLIM_DIR / "inbound.py").read_text(encoding="utf-8")
    service = (_SLIM_DIR / "service.py").read_text(encoding="utf-8")
    plugin = agent + actor + inbound + service
    assert "os.environ" not in plugin
    assert "os.environ" not in endpoint
    assert "import os" not in endpoint
    assert "self._app.world" not in plugin
    assert 'getattr(self._app, "world"' not in plugin
    assert "app.subscribers" not in plugin
    assert "session.members" not in plugin
    assert "session.closed" not in plugin
    assert 'getattr(self._app, "sessions"' not in plugin
    assert "self._app.sessions" not in plugin
    assert "getattr(" not in plugin
    assert "hasattr(" not in actor
    assert 'startswith("slim_bindings")' not in plugin
    assert "new_tls_client_config" not in plugin
    assert "new_client_config" not in plugin
    assert "disconnect_async" not in plugin
    assert "new_secure_client_config" in plugin
    assert "new_insecure_client_config" in plugin
    assert "set_message_handler" not in plugin
    assert "self._bindings" not in plugin
    assert "def _listen_loop" not in agent
    assert "asyncio.run(" not in agent
    assert "params_from_endpoint" not in plugin
    assert "infra_endpoint" not in plugin
    assert "_owns_actor" not in plugin  # ownership is one field, _owned_service, set only outside a stack
    assert "_on_request" not in plugin
    assert "fake" not in plugin.lower()


def test_https_loopback_uses_tls_unless_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    orig = slim_fake.new_insecure_client_config

    def _spy(endpoint: str) -> str:
        calls.append(endpoint)
        return orig(endpoint)

    monkeypatch.setattr(slim_fake, "new_insecure_client_config", _spy)
    secure_calls: list[str] = []
    orig_secure = slim_fake.new_secure_client_config

    def _spy_secure(endpoint: str) -> str:
        secure_calls.append(endpoint)
        return orig_secure(endpoint)

    monkeypatch.setattr(slim_fake, "new_secure_client_config", _spy_secure)
    FakeWorld.reset()
    _desk(url="https://127.0.0.1:46357", dev=False).link("a")
    assert calls == []
    assert secure_calls == ["https://127.0.0.1:46357"]
    assert use_insecure_client_config("https://127.0.0.1:46357", dev=False) is False
    FakeWorld.reset()
    calls.clear()
    _desk(url="https://127.0.0.1:46357", dev=True).link("a")
    assert calls == ["https://127.0.0.1:46357"]
    FakeWorld.reset()
    calls.clear()
    _desk(url="http://127.0.0.1:46357").link("a")
    assert calls == ["http://127.0.0.1:46357"]


def test_node_down_and_tls() -> None:
    FakeWorld.reset()
    FakeWorld.default().down = True
    with pytest.raises(AgentCommError, match="node down"):
        _desk(url="http://127.0.0.1:1").link("a")
    FakeWorld.reset()
    with pytest.raises(AgentCommError, match="TLS"):
        _desk(url="http://127.0.0.1/reject-tls").link("a")
    a, b, _c = _triple()
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    a.create_channel("room", members=["a", "b", "c"], options={})
    a.publish("room", "hi")
    _wait_until(lambda: seen == ["hi"])


def test_lazy_bindings_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "slim_bindings":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(AgentCommError, match=r"uv pip install \"mas-lab-library-ioa\[slim\]\""):
        load_slim_bindings()


def test_name_capabilities_and_session_helpers() -> None:
    with pytest.raises(AgentCommError, match="org/ns/service/instance"):
        SlimAgentComm(name="too/short", service=_svc("http://127.0.0.1:1"))
    FakeWorld.reset()
    link = _link("a")
    caps = link.capabilities()
    assert caps["unicast"] is True
    assert caps["group"] is True
    assert caps["e2e"] is False
    link.bind(None)
    assert link._run(3) == 3
    assert link._full("acme/lab/mas/z") == "acme/lab/mas/z"
    with pytest.raises(AgentCommError, match="host"):
        assert_endpoint_allowed("http://")
    assert inspect.signature(SlimAgentComm.__init__).parameters["service"].kind.name == "KEYWORD_ONLY"
    assert "secret" not in inspect.signature(SlimAgentComm.__init__).parameters  # the service holds it


def test_directed_publish_and_unknown_channel() -> None:
    def _hello(task: str, **_kw: object) -> str:
        return f"hi:{task}"

    FakeWorld.reset()
    a = _link("a", on_request=_hello)
    b = _link("b", on_request=_hello)
    c = _link("c", on_request=_hello)
    assert a.send("b", "ping") == "hi:ping"
    a.create_channel("room", members=["a", "b", "c"], options={"max_retries": 1})
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    seen_c: list[str] = []
    c.subscribe(lambda msg: seen_c.append(str(msg["payload"])))
    a.publish("room", "only-b", to="b")
    _wait_until(lambda: seen == ["only-b"])
    assert seen_c == []
    a.invite("room", "acme/lab/mas/c")
    with pytest.raises(AgentCommError, match="unknown"):
        a.members("no-such")


def test_channel_membership_async_and_lazy_start() -> None:
    import asyncio

    FakeWorld.reset()
    a, b, c = _triple()
    assert "task" in a.honoured_send_intents()
    assert asyncio.run(a.asend("b", "ping")) == "ack:ping"
    a.create_channel("room", members=["a"], options={})
    a.invite("room", "b")
    assert "b" in a.members("room")
    _wait_until(lambda: "room" in b._groups)
    assert "a" in b.members("room")
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    a.publish("room", "from-a")
    _wait_until(lambda: seen == ["from-a"])
    a.remove("room", "b")
    with pytest.raises(AgentCommError, match="not a member"):
        b.publish("room", "again")
    a.close_channel("room")
    with pytest.raises(AgentCommError, match="closed"):
        a.publish("room", "x")

    def _turn(agent_id: str, task: str, *_rest: object) -> str:
        return f"turn:{task}"

    FakeWorld.reset()
    remote = _svc("https://example.test:46357", timeout_s=0.25)
    actor = remote.actor
    lazy = SlimAgentComm(_turn, name="acme/lab/mas/z", service=remote)
    peer = SlimAgentComm(_ack, name="acme/lab/mas/y", service=remote)
    peer.start()
    assert lazy.send("y", "go") == "ack:go"
    y_reply = peer.send("z", "ask")
    assert y_reply == "turn:ask"

    async def _go() -> None:
        async def _coro() -> int:
            return 7

        assert a._run(_coro()) == 7

        async def _boom() -> None:
            raise AgentCommError("threaded")

        with pytest.raises(AgentCommError, match="threaded"):
            a._run(_boom())

    asyncio.run(_go())
    peer.stop()
    lazy.stop()
    actor.stop()


def test_fake_session_helpers() -> None:
    import asyncio
    import datetime

    from slim_node_fake import FakeService, Name, SessionConfig, SessionType

    FakeWorld.reset()
    world = FakeWorld.default()
    svc = FakeService("svc", world=world)
    with pytest.raises(slim_fake.AuthError, match="too short"):
        svc.create_app_with_secret(Name.from_string("acme/lab/svc"), "short")
    name = Name.from_string("acme/lab/svc/a")
    assert str(name) == "acme/lab/svc/a"
    app = svc.create_app_with_secret(name, _SECRET)
    assert str(Name.new_with_id("acme", "lab", "svc", app.id())).count("/") == 3

    async def _go() -> None:
        ctx = await app.create_session_async(
            SessionConfig(session_type=SessionType.POINT_TO_POINT),
            Name.from_string("acme/lab/other"),
        )
        await ctx.completion.wait_async()
        session = ctx.session
        await session.invite_async(Name.from_string("acme/lab/svc/x"))
        assert await session.participants_list_async()
        await app.delete_session_async(session)
        with pytest.raises(slim_fake.SessionError, match="closed"):
            await session.publish_async(b"x")
        with pytest.raises(slim_fake.SessionError, match="closed"):
            await session.publish_to_async(None, b"x")
        session.closed = False
        session.ready = True
        with pytest.raises(slim_fake.Timeout, match="timeout"):
            await session.get_message_async(timeout=datetime.timedelta(seconds=0))
        with pytest.raises(slim_fake.Timeout, match="timeout"):
            await app.listen_for_session_async(timeout=datetime.timedelta(seconds=0))
        with pytest.raises(AgentCommError, match="org/ns/svc"):
            Name.from_string("ab")

    asyncio.run(_go())


def _replying_desk(seen: list[tuple[str, str]]) -> _Desk:
    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        seen.append((agent_id, task))
        return f"ok:{agent_id}:{task}"

    return _desk(run_turn=run_turn)


def test_reply_routes_between_agents_on_one_service() -> None:
    """The fake routes replies by app name like a real node; a shared app name has no route."""
    FakeWorld.reset()
    seen: list[tuple[str, str]] = []
    desk = _replying_desk(seen)
    a = desk.link("a")
    desk.link("b")
    assert a.send("b", "hello") == "ok:b:hello"
    assert seen == [("b", "hello")]


def test_each_agent_has_its_own_app_name() -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")
    desk.link("b")
    world = FakeWorld.default()
    assert sorted(world.by_app) == ["acme/lab/mas.a", "acme/lab/mas.b"]
    assert {app._name.svc for app in world.by_service["acme/lab/mas"]} == {"mas.a", "mas.b"}


def test_anycast_reaches_a_peer_on_the_bare_service() -> None:
    FakeWorld.reset()
    seen: list[tuple[str, str]] = []
    desk = _replying_desk(seen)
    a = desk.link("a")
    desk.link("b")
    assert a.send("acme/lab/mas", "any") == "ok:b:any"
    assert seen == [("b", "any")]


def test_link_refuses_dotted_service() -> None:
    desk = _desk()
    desk.start()
    with pytest.raises(AgentCommError, match="service cannot contain"):
        SlimAgentComm(name="acme/lab/mas.x/a", service=desk._service)


def _actor(**kwargs: object) -> ConnectionActor:
    return ConnectionActor(endpoint="http://127.0.0.1:46357", timeout_s=0.25, **kwargs)  # type: ignore[arg-type]


def test_failed_start_returns_its_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(self: ConnectionActor) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(ConnectionActor, "_aconnect", hang)
    actor = _actor()
    with pytest.raises(AgentCommError, match="did not start"):
        actor.start()
    assert actor.failed
    assert actor._loop is None
    assert _SHARED_LOOP._refs == 0


def test_stop_from_the_loop_thread_returns_the_lease() -> None:
    actor = _actor()
    actor.start()
    done = threading.Event()

    async def stop_here() -> None:
        actor.stop()
        done.set()

    actor.spawn(stop_here())
    assert done.wait(2.0)
    deadline = time.monotonic() + 2.0
    while _SHARED_LOOP._refs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _SHARED_LOOP._refs == 0
    assert actor._loop is None


def test_stop_cancels_only_its_own_in_flight_calls() -> None:
    mine, other = _actor(), _actor()
    mine.start()
    other.start()
    outcome: list[str] = []

    def call() -> None:
        try:
            mine.submit(asyncio.sleep(30), timeout=5.0)
        except AgentCommError as exc:
            outcome.append(str(exc))

    thread = threading.Thread(target=call)
    thread.start()
    time.sleep(0.1)
    mine.stop()
    thread.join(2.0)
    assert not thread.is_alive()
    assert outcome == ["slim link is stopped"]
    assert other.submit(asyncio.sleep(0, "alive")) == "alive"
    other.stop()


def test_send_reaches_a_peer_served_by_another_actor() -> None:
    """Two actors on one namespace stand for two processes: no shared instance ids."""
    FakeWorld.reset()
    one, two = _desk(), _desk()
    a = one.link("a")
    b = two.link("b")
    assert a.send("b", "hi") == "ack:hi"
    assert b.send("a", "back") == "ack:back"
    assert a.send("acme/lab/mas", "any") == "ack:any"


def test_dotted_service_is_refused() -> None:
    with pytest.raises(AgentCommError, match="service cannot contain"):
        _desk(identity="acme/lab/mas.x").start()


def test_two_stacks_share_one_loop() -> None:
    """slim-bindings has one global loop; a second desk must not break the first."""
    FakeWorld.reset()
    one = _desk(identity="acme/lab/one")
    two = _desk(identity="acme/lab/two")
    a, b = one.link("a"), one.link("b")
    c, _d = two.link("c"), two.link("d")
    assert one._actor.thread is two._actor.thread
    assert a.send("b", "x") == "ack:x"
    assert c.send("d", "y") == "ack:y"
    two.close()
    assert a.send("b", "z") == "ack:z"
    assert b.send("a", "w") == "ack:w"
    one.close()
    assert one._actor.thread is None
    assert one._actor._loop is None
    again = _desk(identity="acme/lab/three")
    first, _second = again.link("a"), again.link("b")
    assert first.send("b", "v") == "ack:v"


def test_dotted_agent_id_is_refused() -> None:
    FakeWorld.reset()
    desk = _desk()
    with pytest.raises(AgentCommError, match="cannot contain"):
        desk.link("x.y")


@pytest.mark.slim
def test_real_node_frame_crosses() -> None:
    """Needs slim-bindings and a node on 127.0.0.1:46357 (tutorial 7, step 3, starts one)."""
    import socket

    pytest.importorskip("slim_bindings")
    try:
        socket.create_connection(("127.0.0.1", 46357), timeout=0.5).close()
    except OSError:
        pytest.skip("no SLIM node on 127.0.0.1:46357")
    seen: list[tuple[str, str]] = []

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        seen.append((agent_id, task))
        return f"ok:{agent_id}:{task}"

    desk = _Desk(
        identity="acme/lab/mas",
        url="http://127.0.0.1:46357",
        secret=_SECRET,
        timeout_s=5.0,
        run_turn=run_turn,
    )
    try:
        a = desk.link("a")
        desk.link("b")
        result = a.send("b", "hello")
        assert ("b", "hello") in seen
        assert result == "ok:b:hello"
        assert a.send("acme/lab/mas", "any") == "ok:b:any"
        with pytest.raises(AgentCommError, match="no peer answers"):
            a.send("nobody", "x")
    finally:
        desk.close()


@pytest.mark.slim
def test_real_node_a2a_layer_over_slim() -> None:
    """``[slim, a2a]`` on the real node: an A2A request crosses, the answer comes back, a failed turn is an error."""
    import socket

    pytest.importorskip("slim_bindings")
    try:
        socket.create_connection(("127.0.0.1", 46357), timeout=0.5).close()
    except OSError:
        pytest.skip("no SLIM node on 127.0.0.1:46357")

    def run_turn(agent_id: str, task: str, *rest: object) -> str:
        if task == "boom":
            raise RuntimeError("turn failed")
        return f"{agent_id}:{task}:{rest[-1]}"

    slim = {
        "type": "slim",
        "params": {"url": "http://127.0.0.1:46357", "identity": "acme/lab/a2a", "secret": _SECRET, "timeout_s": 5.0},
    }
    handle = StackHandle("a2a-real", [slim, {"type": "a2a-framing"}], run_turn)
    try:
        a = handle.link("a")
        handle.link("b")
        assert a.send("b", "héllo ✓", context_id="ctx-real") == "b:héllo ✓:ctx-real"
        with pytest.raises(AgentCommError, match="remote A2A task failed"):
            a.send("b", "boom")
    finally:
        handle.close()


@pytest.mark.slim
def test_real_node_two_stacks_in_one_process() -> None:
    import socket

    pytest.importorskip("slim_bindings")
    try:
        socket.create_connection(("127.0.0.1", 46357), timeout=0.5).close()
    except OSError:
        pytest.skip("no SLIM node on 127.0.0.1:46357")

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        return f"ok:{agent_id}:{task}"

    def desk(identity: str) -> _Desk:
        return _Desk(identity=identity, url="http://127.0.0.1:46357", secret=_SECRET, timeout_s=5.0, run_turn=run_turn)

    one, two = desk("acme/lab/mas"), desk("acme/lab/other")
    try:
        a, _b = one.link("a"), one.link("b")
        d, _e = two.link("d"), two.link("e")
        assert a.send("b", "1") == "ok:b:1"
        assert d.send("e", "2") == "ok:e:2"
    finally:
        one.close()
        two.close()


@pytest.mark.slim
def test_real_node_two_actors_one_namespace() -> None:
    """Two actors on one identity, as two processes would be."""
    import socket

    pytest.importorskip("slim_bindings")
    try:
        socket.create_connection(("127.0.0.1", 46357), timeout=0.5).close()
    except OSError:
        pytest.skip("no SLIM node on 127.0.0.1:46357")

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        return f"ok:{agent_id}:{task}"

    def desk() -> _Desk:
        return _Desk(
            identity="acme/lab/mas", url="http://127.0.0.1:46357", secret=_SECRET, timeout_s=5.0, run_turn=run_turn
        )

    one, two = desk(), desk()
    try:
        a, b = one.link("a"), two.link("b")
        assert a.send("b", "1") == "ok:b:1"
        assert b.send("a", "2") == "ok:a:2"
    finally:
        one.close()
        two.close()


def test_local_id_and_app_names() -> None:
    assert local_id("acme/lab/mas/buyer") == "buyer"
    assert local_id("acme/lab/mas/buyer", "desk-buyer") == "desk-buyer"
    assert slim_app_triple("acme/lab/mas/buyer") == ("acme", "lab", "mas.buyer")
    assert slim_app_triple("acme/lab/mas") == ("acme", "lab", "mas")
    assert slim_app_triple("solo") == ("lab", "app", "solo")
    assert local_id("solo") == "solo"
    assert local_id("") == ""
    assert instance_uuid("buyer") != "buyer"
    uuid.UUID(instance_uuid("buyer"))
    assert instance_uuid(instance_uuid("buyer")) == instance_uuid("buyer")


def test_send_to_unstarted_peer_is_no_peer() -> None:
    FakeWorld.reset()
    desk = _desk()
    a = desk.link("a")
    with pytest.raises(AgentCommError, match="no peer"):
        a.send("b", "hello")
    desk.link("b")
    assert a.send("b", "hello") == "ack:hello"


def test_send_puts_call_and_context_on_the_wire() -> None:
    a, b, _c = _triple()
    seen: list[tuple[object, ...]] = []

    def run_turn(
        agent_id: str,
        task: str,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
    ) -> str:
        seen.append((agent_id, task, correlation_id, caller_call_id, context_id, inbound_send()))
        return f"ok:{task}"

    b._run_turn = run_turn
    assert a.send("b", "hello", correlation_id=7, caller_call_id="parent", context_id="ctx-1") == "ok:hello"
    assert seen == [("b", "hello", 7, "parent", "ctx-1", InboundSend(sender=a.agent_id, intent="task"))]


def test_inform_and_ask_cross_the_envelope() -> None:
    a, b, _c = _triple()
    seen: list[tuple[object, ...]] = []

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        del agent_id
        seen.append((task, inbound_send()))
        return f"ok:{task}"

    b._run_turn = run_turn
    labelled = label_untrusted_peer("fyi", sender=a.name, intent="inform")
    assert a.send("b", "fyi", context=SendContext(intent="inform")) == "delivered"
    assert seen == [(labelled, InboundSend(sender=a.agent_id, intent="inform"))]
    seen.clear()
    asked = label_untrusted_peer("q?", sender=a.name, intent="ask")
    assert a.send("b", "q?", context=SendContext(intent="ask")) == f"ok:{asked}"
    assert seen == [(asked, InboundSend(sender=a.agent_id, intent="ask"))]
    with pytest.raises(AgentCommError, match="not supported"):
        a.send("b", "nudge", context=SendContext(intent="steer"))


def test_bad_inbound_bytes_are_a_generic_error() -> None:
    a, b, _c = _triple()
    msg = SimpleNamespace(
        payload=b"not-json{{{",
        context=SimpleNamespace(source_name=slim_fake.Name("acme", "lab", "mas")),
    )
    reply = b._on_inbound(msg)
    body = json.loads(reply.decode("utf-8"))
    assert body["kind"] == "error"
    assert a.send("b", "hello") == "ack:hello"


def test_run_turn_receives_the_agency_id() -> None:
    FakeWorld.reset()
    seen: list[str] = []

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        seen.append(agent_id)
        return f"ok:{task}"

    desk = _desk()
    a = desk.link("a")
    b = SlimAgentComm(run_turn, name="acme/lab/mas/b", service=desk._service, agent_id="desk-b")
    b.start()
    assert a.send("b", "t") == "ok:t"
    assert seen == ["desk-b"]
    seen.clear()
    c = SlimAgentComm(run_turn, name="acme/lab/mas/c", service=desk._service)
    c.start()
    assert a.send("c", "u") == "ok:u"
    assert seen == ["c"]


def test_drain_session_backs_off_on_persistent_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorld.reset()
    link = _link("a", timeout_s=0.05)
    _halt_listen(link)
    sleeps: list[float] = []

    async def _sleep(delay: float, *_args: object, **_kwargs: object) -> None:
        sleeps.append(delay)
        link._stopped = True

    monkeypatch.setattr("library_ioa.plugins.slim.inbound._async_sleep", _sleep)

    class Boom:
        async def get_message_async(self, timeout: object = None) -> None:
            del timeout
            raise slim_fake.SessionError("read failed")

    asyncio.run(link._drain_session(Boom()))
    assert sleeps == [0.05]


def test_stop_does_not_cancel_listen_loop_tasks_from_another_thread() -> None:
    FakeWorld.reset()
    link = _link("a")
    cancelled_from: list[int] = []

    class Dummy:
        def cancel(self) -> None:
            cancelled_from.append(threading.get_ident())

    link._readers.append(Dummy())  # type: ignore[arg-type]
    main_ident = threading.get_ident()
    link.stop()
    assert main_ident not in cancelled_from


def test_stop_while_send_in_flight() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()

    def _slow(task: str, **_kw: object) -> str:
        time.sleep(0.4)
        return f"ok:{task}"

    _set_run_turn(b, _slow)
    errors: list[str] = []

    def _send() -> None:
        try:
            a.send("b", "ping")
        except AgentCommError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=_send)
    worker.start()
    time.sleep(0.05)
    a.stop()
    worker.join(timeout=3.0)
    assert worker.is_alive() is False
    with pytest.raises(AgentCommError, match="stopped"):
        a.send("b", "again")


def test_stop_from_inbound_handler(caplog: pytest.LogCaptureFixture) -> None:
    import gc

    FakeWorld.reset()
    a, b, _c = _triple()

    def _handler(task: str, **_kw: object) -> str:
        b.stop()
        return f"ok:{task}"

    _set_run_turn(b, _handler)
    caplog.set_level("ERROR")
    assert a.send("b", "x") == "ok:x"
    _wait_until(lambda: b._app is None and not b._frames)
    gc.collect()  # a frame task that failed after the reply would log "Task exception was never retrieved"
    assert [rec.getMessage() for rec in caplog.records if rec.levelname == "ERROR"] == []


@pytest.mark.parametrize(
    ("error", "idle"),
    [
        (slim_fake.Timeout("timeout"), True),
        (slim_fake.SessionError("receive timeout"), True),
        (slim_fake.SessionError("listen_for_session timed out"), True),
        (slim_fake.SessionError("operation timed out"), False),
        (slim_fake.SessionError("handshake timed out"), False),
    ],
)
def test_only_an_idle_read_is_not_a_read_failure(monkeypatch: pytest.MonkeyPatch, error: Exception, idle: bool) -> None:
    FakeWorld.reset()
    link = _link("a", timeout_s=1.0)
    _halt_listen(link)

    async def _sleep(delay: float, *_args: object, **_kwargs: object) -> None:
        del delay

    monkeypatch.setattr("library_ioa.plugins.slim.inbound._async_sleep", _sleep)
    reads = [0]

    class Reader:
        async def get_message_async(self, timeout: object = None) -> None:
            del timeout
            reads[0] += 1
            if reads[0] > 8:
                link._stopped = True
            raise error

    link._run(link._drain_session(Reader()))
    assert link._actor.failed is False
    assert link._need_reconnect is (not idle)
    assert reads[0] == (9 if idle else 5)


def test_reader_backs_off_then_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorld.reset()
    link = _link("a", timeout_s=1.0)
    _halt_listen(link)
    sleeps: list[float] = []

    async def _sleep(delay: float, *_args: object, **_kwargs: object) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("library_ioa.plugins.slim.inbound._async_sleep", _sleep)

    class Boom:
        async def get_message_async(self, timeout: object = None) -> None:
            del timeout
            raise slim_fake.SessionError("read failed")

    link._run(link._drain_session(Boom()))
    assert link._actor is not None
    assert link._actor.failed is False
    assert link._need_reconnect is True
    assert sleeps == [0.05, 0.1, 0.2, 0.25]


def test_one_loop_many_links() -> None:
    FakeWorld.reset()
    shared = _svc(timeout_s=0.5)
    actor = shared.actor
    a = SlimAgentComm(name="acme/lab/mas/a", service=shared)
    b = SlimAgentComm(name="acme/lab/mas/b", service=shared, run_turn=_ack)
    a.start()
    b.start()
    assert len(actor._connections) == 2  # one node connection per link, on one loop
    assert a.send("b", "hello") == "ack:hello"
    a.stop()
    b.stop()
    actor.stop()
    assert actor._connections == []
    assert actor.thread is None or actor.thread.is_alive() is False


def test_no_thread_left_after_stop() -> None:
    FakeWorld.reset()
    desk = _desk()
    link = desk.link("a")
    assert link._listening
    desk.close()
    assert desk._actor.thread is None
    assert desk._actor._loop is None


def test_the_slim_layer_rejects_unknown_params() -> None:
    desk = _desk(swallowed="nope")
    with pytest.raises(AgentCommError, match=r"unknown params \['swallowed'\]"):
        desk.start()


def test_handle_close_stops_the_actor() -> None:
    FakeWorld.reset()
    desk = _desk(timeout_s=0.5, auth_mode="shared_secret", max_message_size=4096)
    a = desk.link("a")
    desk.link("b")
    assert a.max_message_size == 4096
    assert desk._actor.thread is not None
    desk.close()
    assert desk._actor.thread is None
    assert desk._actor._loop is None


def test_secret_never_in_inbound_events() -> None:
    FakeWorld.reset()
    events: list[dict] = []
    a = _link("a")
    b = _link("b")
    a.subscribe(lambda msg: events.append(dict(msg)))
    assert b.send("a", "hello") == "ack:hello"
    blob = repr(events) + repr(a) + repr(b)
    assert _SECRET not in blob
    assert "secret=" not in repr(a)


# --- endpoint url checks -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.com\\@localhost:46357",  # a parser that reads \ as / sees the host evil.com
        "http://evil.com:80\\@127.0.0.1",
        "http://localhost@evil.com",
        "http://localhost:46357 @evil.com",
        "http://localhost\t.evil.com",
        "http://localhost.evil.com",
        "http://localhost.:46357",
        "http://127.1",
        "http://0.0.0.0:46357",
        "http://[::ffff:127.0.0.1]:46357",
    ],
)
def test_a_url_that_could_reach_another_host_is_refused(url: str) -> None:
    with pytest.raises(AgentCommError):
        assert_endpoint_allowed(url)
    with pytest.raises(AgentCommError):
        assert_endpoint_allowed(url, dev=True)


def test_only_http_and_https_are_accepted_and_the_message_says_so() -> None:
    for url in ("ws://127.0.0.1:46357", "localhost:46357", "ftp://localhost"):
        with pytest.raises(AgentCommError, match="scheme .* is not supported; use http:// or https://"):
            assert_endpoint_allowed(url)
    assert_endpoint_allowed(
        "https://slim.example.com:46357"
    )  # a name with "insecure" in its path is no reason to refuse
    assert_endpoint_allowed("https://slim.example.com/insecure")
    assert_endpoint_allowed("HTTP://LOCALHOST:46357")
    assert_endpoint_allowed("http://[::1]:46357")


# --- handlers run off the SLIM loop ---------------------------------------------------------


def test_a_handler_can_send_to_another_agent() -> None:
    FakeWorld.reset()
    a, b, c = _triple()
    _set_run_turn(c, lambda task: f"c saw {task}")
    _set_run_turn(b, lambda task: b.send("c", f"via b: {task}"))  # a -> b -> c
    assert a.send("b", "hi") == "c saw via b: hi"


def test_a_slow_handler_does_not_stop_the_other_agents_of_the_process() -> None:
    FakeWorld.reset()
    a, b, c = _triple()
    gate = threading.Event()
    started = threading.Event()

    def slow(task: str) -> str:
        started.set()
        assert gate.wait(timeout=10)
        return f"slow:{task}"

    _set_run_turn(b, slow)
    _set_run_turn(c, lambda task: f"fast:{task}")
    results: list[str] = []
    sender = threading.Thread(target=lambda: results.append(a.send("b", "x")))
    sender.start()
    assert started.wait(timeout=5)
    assert a.send("c", "y") == "fast:y"  # b's turn is still running; the loop is free
    gate.set()
    sender.join(timeout=10)
    assert results == ["slow:x"]


def test_the_reply_wait_is_longer_than_the_io_timeout() -> None:
    FakeWorld.reset()
    desk = _desk(timeout_s=0.5)
    a, b = desk.link("a"), desk.link("b")
    _set_run_turn(b, lambda task: (time.sleep(1.5), f"late:{task}")[1])  # longer than timeout_s, a normal turn
    assert a.send("b", "x") == "late:x"
    assert a.reply_timeout_s == 120.0
    assert _desk(timeout_s=0.5, reply_timeout_s=7.0).link("z").reply_timeout_s == 7.0


def test_a_reply_that_never_comes_ends_at_the_reply_timeout() -> None:
    FakeWorld.reset()
    desk = _desk(timeout_s=0.2, reply_timeout_s=0.5)
    a, b = desk.link("a"), desk.link("b")
    _set_run_turn(b, lambda task: (time.sleep(3), task)[1])
    started = time.monotonic()
    with pytest.raises(AgentCommError):
        a.send("b", "x")
    assert time.monotonic() - started < 2.5


@pytest.mark.slim
def test_real_node_a_handler_can_send_and_a_slow_turn_is_waited_for() -> None:
    """On a real node: a -> b -> c, and a turn longer than the I/O timeout still returns."""
    import socket

    pytest.importorskip("slim_bindings")
    try:
        socket.create_connection(("127.0.0.1", 46357), timeout=0.5).close()
    except OSError:
        pytest.skip("no SLIM node on 127.0.0.1:46357")
    links: dict[str, SlimAgentComm] = {}

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        if agent_id == "b":
            return links["b"].send("c", f"via b: {task}")
        if agent_id == "c":
            time.sleep(1.5)  # longer than timeout_s below
            return f"c saw {task}"
        return f"ok:{agent_id}"

    desk = _Desk(
        identity="acme/lab/mas",
        url="http://127.0.0.1:46357",
        secret=_SECRET,
        timeout_s=0.5,
        run_turn=run_turn,
    )
    try:
        links.update({name: desk.link(name) for name in ("a", "b", "c")})
        assert links["a"].send("b", "hi") == "c saw via b: hi"
    finally:
        desk.close()


# --- names, sizes, lifecycle -------------------------------------------------------------------


def test_an_agent_id_cannot_name_another_identity() -> None:
    FakeWorld.reset()
    desk = _desk()
    assert desk.link("acme/lab/mas/buyer").agent_id == "buyer"  # inside the identity
    for outside in ("acme/lab/other/buyer", "evil/lab/mas/buyer", "acme/lab/mas/x/buyer"):
        with pytest.raises(StackError, match="outside the identity"):
            desk.link(outside)


def test_a_short_secret_is_refused_with_a_message_that_names_this_check() -> None:
    with pytest.raises(AgentCommError, match="SLIM shared secret is too short: at least 32 bytes"):
        require_secret("short")


def test_fabric_ids_are_not_reused_after_a_stack_is_freed() -> None:
    import gc

    slim = {"type": "slim", "params": {"url": "http://127.0.0.1:46357", "identity": "acme/lab/mas", "secret": _SECRET}}
    for layers in ([slim], [{"type": "local-group", "params": {"identity": "acme/lab/mas"}}]):
        seen: set[int] = set()
        for _ in range(25):
            handle = StackHandle("s", layers)
            assert handle.fabric_id not in seen
            seen.add(handle.fabric_id)
            del handle
            gc.collect()


def _slim_stack(*upper: object, run_turn=None) -> StackHandle:
    """A SLIM stack, with live layers above it when given, over the fake node."""
    slim = {
        "type": "slim",
        "params": {"url": "http://127.0.0.1:46357", "identity": "acme/lab/mas", "secret": _SECRET, "timeout_s": 0.25},
    }
    handle = StackHandle("desk", [slim], run_turn)
    _DESKS.append(handle)  # type: ignore[arg-type]
    if not upper:
        return handle
    from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

    link = handle.link

    def with_upper(agent_id: str, run_turn=None):  # the same agent's SLIM link, with the layers above it
        return StackAgentComm([link(agent_id, run_turn)._layers[0].link, *(factory() for factory in upper)])

    handle.link = with_upper  # type: ignore[method-assign]
    return handle


def test_a_slim_stack_passes_every_send() -> None:
    FakeWorld.reset()
    stack = _slim_stack(run_turn=lambda agent_id, task, *_: f"ok:{agent_id}:{task}")
    a = stack.link("a")
    stack.link("b")
    assert a.send("b", "quote") == "ok:b:quote"
    assert any(b"quote" in frame for frame in FakeWorld.default().frames)


def test_a_failing_layer_on_a_slim_stack_raises_and_no_frame_leaves() -> None:
    from mas.library.standard.lib.stack import StackSendError
    from mas.library.standard.testing.layer_conformance import FailingLayer

    FakeWorld.reset()
    stack = _slim_stack(lambda: FailingLayer(targets=("b",), message="b refused"), run_turn=lambda *_: "ok")
    a = stack.link("a")
    stack.link("b")
    stack.link("c")
    with pytest.raises(StackSendError, match="b refused"):
        a.send("b", "lost")
    assert not any(b"lost" in frame for frame in FakeWorld.default().frames)
    assert a.send("c", "kept") == "ok"  # the layer refuses b only


def test_every_send_on_a_slim_stack_carries_a_child_traceparent() -> None:
    FakeWorld.reset()
    seen: list[dict] = []

    def run_turn(agent_id: str, task: str, *_: object) -> str:
        seen.append(get_headers())
        return "ok"

    stack = _slim_stack(run_turn=run_turn)
    a = stack.link("a")
    stack.link("b")
    parent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    assert a.send("b", "x", headers={"traceparent": parent}) == "ok"
    assert a.send("b", "y") == "ok"
    first, second = seen[0]["traceparent"].split("-"), seen[1]["traceparent"].split("-")
    assert first[1] == "a" * 32 and first[2] != "b" * 16  # same trace, new span
    assert len(second[1]) == 32  # no parent: a new root


def test_each_agent_on_a_slim_stack_has_its_own_stack_and_name() -> None:
    FakeWorld.reset()
    stack = _slim_stack()
    a, b = stack.link("a"), stack.link("b")
    assert a is not b
    assert a is stack.link("a")
    assert a._layers[0].link.name == "acme/lab/mas/a"
    assert b._layers[0].link.name == "acme/lab/mas/b"
    assert a._layers[0].link._actor is b._layers[0].link._actor  # one shared service
    assert a.fabric_id == b.fabric_id


def test_an_oversized_inbound_frame_is_refused_before_it_is_parsed() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()
    called: list[str] = []
    _set_run_turn(b, lambda task: called.append(task) or "ok")
    big = json.dumps({"kind": "request", "task": "x" * (b.max_message_size + 100_000)}).encode()
    reply = b._on_inbound(
        SimpleNamespace(payload=big, context=SimpleNamespace(source_name=slim_fake.Name("acme", "lab", "a")))
    )
    assert json.loads(reply) == {"kind": "error", "error": "peer handler failed"}
    assert called == []
    small = json.dumps({"kind": "request", "task": "fine"}).encode()
    assert (
        json.loads(
            b._on_inbound(
                SimpleNamespace(payload=small, context=SimpleNamespace(source_name=slim_fake.Name("acme", "lab", "a")))
            )
        )["task"]
        == "ok"
    )


def test_a_finished_session_reader_is_dropped() -> None:
    FakeWorld.reset()
    a, b, _c = _triple()
    _set_run_turn(b, lambda task: f"ok:{task}")
    assert a.send("b", "x") == "ok:x"
    _wait_until(lambda: len(b._readers) >= 1)
    served = [s for s in FakeWorld.default().sessions if s.owner is b._app]
    assert served, "b should hold the session a opened"
    for session in served:  # the session ends; b's reader for it finishes and is forgotten
        session.close()
    _wait_until(lambda: len(b._readers) == 0, timeout=5.0)
    assert b._readers == []


def test_closing_the_stack_stops_its_thread_and_the_links_refuse_to_send() -> None:
    FakeWorld.reset()
    desk = _desk()
    a, b = desk.link("a"), desk.link("b")
    _set_run_turn(b, lambda task: f"ok:{task}")
    assert a.send("b", "x") == "ok:x"
    names_before = {t.name for t in threading.enumerate()}
    assert "slim-link" in names_before
    desk.close()
    _wait_until(lambda: not any(t.name == "slim-link" and t.is_alive() for t in threading.enumerate()), timeout=5.0)
    assert not any(t.name == "slim-link" and t.is_alive() for t in threading.enumerate())
    with pytest.raises(AgentCommError, match="stopped|failed|not started"):
        a.send("b", "again")


def test_a_new_stack_works_after_the_previous_one_was_closed() -> None:
    FakeWorld.reset()
    first = _desk()
    a, b = first.link("a"), first.link("b")
    _set_run_turn(b, lambda task: f"first:{task}")
    assert a.send("b", "1") == "first:1"
    first.close()
    second = _desk()
    c, d = second.link("c"), second.link("d")
    _set_run_turn(d, lambda task: f"second:{task}")
    assert c.send("d", "2") == "second:2"
    second.close()


@pytest.mark.parametrize(
    ("identity", "agent_id", "message"),
    [
        ("acme/lab/desk", "a.b", "SLIM agent id cannot contain '.': 'a.b'"),
        ("acme/lab/de.sk", "a", "SLIM service cannot contain '.': 'de.sk'"),
        ("acme/lab/desk", "x/y", "agent id 'x/y' is outside the identity"),
        ("acme/lab/desk", "a b", "illegal name part 'a b'"),
        ("acme/desk", "a", "three-part org/ns/service"),
    ],
)
def test_the_slim_grammar_refuses_an_agent_id_or_identity_before_anything_connects(
    identity: str, agent_id: str, message: str
) -> None:
    from mas.library.standard.lib.stack.handle import agent_id_refusal

    assert message in agent_id_refusal(SlimAgentComm, {"identity": identity}, agent_id)
    assert agent_id_refusal(SlimAgentComm, {"identity": "acme/lab/desk"}, "triage-1_b") == ""


@pytest.mark.slim
def test_real_node_idle_listen_is_an_idle_read() -> None:
    """The real bindings' idle ``listen_for_session`` error is what ``_is_idle_timeout`` treats as idle."""
    import datetime
    import socket

    from library_ioa.plugins.slim.inbound import _is_idle_timeout

    pytest.importorskip("slim_bindings")
    try:
        socket.create_connection(("127.0.0.1", 46357), timeout=0.5).close()
    except OSError:
        pytest.skip("no SLIM node on 127.0.0.1:46357")
    desk = _Desk(identity="acme/lab/idle", url="http://127.0.0.1:46357", secret=_SECRET, timeout_s=2.0)
    try:
        link = desk.link("a")
        with pytest.raises(AgentCommError) as err:
            link._run(link._call(link._app.listen_for_session_async(timeout=datetime.timedelta(seconds=0.2))))
        assert _is_idle_timeout(err.value, link._sb)
    finally:
        desk.close()

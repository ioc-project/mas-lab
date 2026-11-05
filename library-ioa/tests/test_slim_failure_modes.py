#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The SLIM layer under load and failure: concurrent sends, late replies, limits, cleanup, config.

Every test here runs on the in-process node stand-in (``tests/support/slim_node_fake.py``), never on a real
node. Real-node coverage is the ``@pytest.mark.slim`` tests of ``test_slim_comm.py``."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path

import pytest
import slim_node_fake as slim_fake
from library_ioa.plugins.slim.actor import ConnectionActor
from library_ioa.plugins.slim.service import SlimService
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from test_slim_comm import (  # noqa: F401  (the autouse fixture patches the bindings for this module too)
    _SECRET,
    _desk,
    _patch_slim_bindings,
    _set_run_turn,
    _wait_until,
)

FakeWorld = slim_fake.FakeWorld
_SLIM_DIR = Path(__file__).resolve().parents[1] / "src" / "library_ioa" / "plugins" / "slim"


def test_a_second_send_to_one_target_waits_for_the_first_reply() -> None:
    """Both sends share a session: a second request published early could read the first one's reply."""
    FakeWorld.reset()
    desk = _desk(timeout_s=0.5)
    a, b = desk.link("a"), desk.link("b")
    gate = threading.Event()
    _set_run_turn(b, lambda task: (gate.wait(5), f"re:{task}")[1])
    published: list[int] = []
    real = slim_fake.FakeSession.publish_async

    async def counting(self, payload, payload_type=None, metadata=None):
        published.append(1)
        return await real(self, payload, payload_type, metadata)

    slim_fake.FakeSession.publish_async = counting  # type: ignore[method-assign]
    try:
        results: dict[str, str] = {}
        first = threading.Thread(target=lambda: results.update(one=a.send("b", "one")))
        second = threading.Thread(target=lambda: results.update(two=a.send("b", "two")))
        first.start()
        _wait_until(lambda: len(published) == 1)
        second.start()
        time.sleep(0.3)
        assert len(published) == 1  # the second request is held back, not published
        gate.set()
        first.join(5)
        second.join(5)
    finally:
        slim_fake.FakeSession.publish_async = real  # type: ignore[method-assign]
    assert results == {"one": "re:one", "two": "re:two"}


def test_a_reply_that_arrives_after_the_timeout_does_not_answer_the_next_request() -> None:
    FakeWorld.reset()
    desk = _desk(timeout_s=0.25, reply_timeout_s=0.3)
    a, b = desk.link("a"), desk.link("b")
    _set_run_turn(b, lambda task: (time.sleep(0.8) if task == "first" else None, f"re:{task}")[1])
    with pytest.raises(AgentCommError):
        a.send("b", "first")
    time.sleep(1.0)  # the late reply is now waiting on the old session
    assert a.send("b", "second") == "re:second"


def test_a_reply_larger_than_the_limit_is_refused() -> None:
    FakeWorld.reset()
    desk = _desk(timeout_s=0.5, max_message_size=100)
    a, b = desk.link("a"), desk.link("b")
    _set_run_turn(b, lambda task: "x" * 100_000)
    with pytest.raises(AgentCommError, match="peer handler failed"):
        a.send("b", "hi")


@pytest.mark.parametrize("target", ["b c", "b\nc", "b//c", "", "b|c"])
def test_a_target_that_is_not_a_name_never_reaches_the_node(target: str) -> None:
    FakeWorld.reset()
    a = _desk().link("a")
    with pytest.raises(AgentCommError):
        a.send(target, "x")


def test_after_five_read_failures_the_link_reconnects_and_a_send_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeWorld.reset()
    desk = _desk(timeout_s=0.15, reply_timeout_s=2)
    a = desk.link("a")
    desk.link("b")
    old_app = a._app
    count = {"n": 0}
    real = slim_fake.FakeApp.listen_for_session_async

    async def flaky(self, timeout=None):
        if self is old_app and count["n"] < 5:
            count["n"] += 1
            raise slim_fake.SlimError("connection reset")
        return await real(self, timeout)

    monkeypatch.setattr(slim_fake.FakeApp, "listen_for_session_async", flaky)
    _wait_until(lambda: count["n"] >= 5, timeout=5)
    _wait_until(lambda: a._app is not None and a._app is not old_app and not a._need_reconnect, timeout=5)
    assert a.send("b", "hi") == "ack:hi"
    assert a._actor.failed is False


def test_a_start_that_fails_half_way_closes_its_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorld.reset()
    desk = _desk()

    async def boom(self, *args, **kwargs):
        raise RuntimeError("subscribe failed")

    monkeypatch.setattr(slim_fake.FakeApp, "subscribe_async", boom)
    with pytest.raises(AgentCommError, match="subscribe failed"):
        desk.link("a")
    actor = desk.handle._services[0].actor
    assert actor._connections == []  # no connection is left open for a retry to add to


def test_a_peer_that_sends_faster_than_the_agent_answers_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("library_ioa.plugins.slim.agent_comm.MAX_INFLIGHT_FRAMES", 1)
    FakeWorld.reset()
    desk = _desk(timeout_s=0.5)
    senders = [desk.link("a1"), desk.link("a2"), desk.link("b")]
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def handle(task: str) -> str:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.3)
        with lock:
            active["now"] -= 1
        return task

    _set_run_turn(senders[2], handle)
    threads = [threading.Thread(target=lambda s=s: s.send("b", "x")) for s in senders[:2]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert active["peak"] == 1


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"secret": "short"}, "too short"),
        ({"secret": ""}, "requires a secret"),
        ({"auth_mode": "oauth"}, "auth_mode"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"timeout_s": -1}, "timeout_s"),
        ({"timeout_s": True}, "timeout_s"),
        ({"reply_timeout_s": "soon"}, "reply_timeout_s"),
        ({"max_message_size": 0}, "max_message_size"),
        ({"max_message_size": 1.5}, "max_message_size"),
    ],
)
def test_the_service_refuses_a_bad_param_when_it_is_built(params: dict, message: str) -> None:
    base = {"identity": "acme/lab/mas", "url": "http://127.0.0.1:46357", "secret": _SECRET}
    with pytest.raises(AgentCommError, match=message):
        SlimService(**{**base, **params})


def test_the_secret_minimum_counts_bytes_not_characters() -> None:
    from library_ioa.plugins.slim.endpoint import require_secret

    assert require_secret("é" * 20) == "é" * 20  # 20 characters, 40 bytes
    with pytest.raises(AgentCommError, match="too short"):
        require_secret("é" * 15)  # 30 bytes


def test_a_secret_that_is_too_short_is_not_in_the_error() -> None:
    secret = "hunter2-hunter2"
    with pytest.raises(AgentCommError) as raised:
        SlimService(identity="acme/lab/mas", url="http://127.0.0.1:46357", secret=secret)
    assert secret not in str(raised.value) and secret not in repr(raised.value)
    with pytest.raises(AgentCommError) as from_params:
        SlimService.from_params({"identity": "acme/lab/mas", "url": "http://127.0.0.1:46357", "secret": secret})
    assert secret not in str(from_params.value)


def _hang_and_report(state: list[str]):
    async def hang() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    return hang()


def test_a_call_that_times_out_is_cancelled_on_the_loop() -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")
    state: list[str] = []
    with pytest.raises(AgentCommError, match="timed out"):
        desk._actor.submit(_hang_and_report(state), timeout=0.1)
    _wait_until(lambda: state == ["cancelled"])


def test_an_awaited_call_that_times_out_is_cancelled_on_the_loop() -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")
    state: list[str] = []
    with pytest.raises(AgentCommError, match="timed out"):
        asyncio.run(desk._actor.asubmit(_hang_and_report(state), timeout=0.1))
    _wait_until(lambda: state == ["cancelled"])


def test_an_awaited_call_on_a_stopped_actor_is_refused() -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")
    actor = desk._actor
    desk.close()
    with pytest.raises(AgentCommError, match="stopped"):
        asyncio.run(actor.asubmit(asyncio.sleep(0), timeout=1))


def test_a_link_can_be_stopped_twice_and_before_it_started() -> None:
    FakeWorld.reset()
    desk = _desk()
    a = desk.link("a")
    a.stop()
    a.stop()
    desk.close()
    desk.close()


def test_the_in_process_node_stand_in_ships_in_no_package() -> None:
    import library_ioa

    package = Path(library_ioa.__file__).resolve().parent
    assert [path.name for path in package.rglob("*fake*.py")] == []
    assert Path(slim_fake.__file__).resolve().parent.name == "support"
    assert "fake" not in (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")


def test_no_slim_module_imports_a_private_name_from_another() -> None:
    cross = re.compile(r"from library_ioa\.plugins\.slim\.\w+ import [^\n]*\b_\w+")
    named = [path.name for path in _SLIM_DIR.glob("*.py") if cross.search(path.read_text(encoding="utf-8"))]
    assert named == []


def test_actor_refuses_calls_once_failed_or_stopped_and_reports_its_endpoint() -> None:
    FakeWorld.reset()
    actor = ConnectionActor(endpoint="http://127.0.0.1:46357")
    assert "46357" in repr(actor)

    async def _noop() -> None:
        return None

    actor.failed = True
    with pytest.raises(AgentCommError, match="failed"):
        actor.start()
    pending = _noop()
    with pytest.raises(AgentCommError, match="failed"):
        actor.submit(pending)
    pending.close()
    pending = _noop()
    with pytest.raises(AgentCommError, match="failed"):
        asyncio.run(actor.asubmit(pending, timeout=0.1))
    actor.mark_failed()
    actor.failed = False
    pending = _noop()
    actor.spawn(pending)
    pending.close()
    actor.close_connection(object(), 1)
    actor.stop()
    pending = _noop()
    with pytest.raises(AgentCommError, match="stopped"):
        actor.submit(pending)
    pending.close()
    with pytest.raises(AgentCommError, match="not connected"):
        asyncio.run(actor.open_connection("x"))


def test_a_start_that_raises_from_the_node_is_a_failed_link(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorld.reset()

    async def boom(self) -> None:
        raise RuntimeError("node down")

    monkeypatch.setattr(ConnectionActor, "_aconnect", boom)
    actor = ConnectionActor(endpoint="http://127.0.0.1:46357", timeout_s=0.2)
    with pytest.raises(AgentCommError, match="node down"):
        actor.start()
    assert actor.failed is True
    actor.stop()


def test_disconnect_errors_on_close_do_not_raise() -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")
    actor = desk._actor
    service, conn_id = actor._connections[0]

    def refuse(_conn_id: object) -> None:
        raise RuntimeError("already gone")

    service.disconnect = refuse
    actor.close_connection(service, conn_id)
    actor.close_connection(service, conn_id)


def test_a_failed_or_stopped_link_refuses_start_and_send() -> None:
    FakeWorld.reset()
    desk = _desk()
    a = desk.link("a")
    desk.link("b")
    a._actor.failed = True
    with pytest.raises(AgentCommError, match="failed"):
        a.start()
    with pytest.raises(AgentCommError, match="failed"):
        a.send("b", "x")
    a._actor.failed = False
    a.stop()
    a.stop()
    with pytest.raises(AgentCommError, match="stopped"):
        a.send("b", "x")
    with pytest.raises(AgentCommError, match="stopped"):
        asyncio.run(a.asend("b", "x"))


def test_submit_from_the_loop_thread_is_refused() -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")
    actor = desk._actor

    async def nested() -> None:
        async def _noop() -> None:
            return None

        pending = _noop()
        try:
            actor.submit(pending)
        finally:
            pending.close()

    with pytest.raises(AgentCommError, match="cannot wait"):
        actor.submit(nested())


def test_a_stop_that_fails_still_releases_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorld.reset()
    desk = _desk()
    desk.link("a")

    async def boom(self) -> None:
        raise RuntimeError("shutdown failed")

    monkeypatch.setattr(ConnectionActor, "_ashutdown", boom)
    desk._actor.stop()
    assert desk._actor.running is False


def test_service_repr_names_the_identity() -> None:
    FakeWorld.reset()
    service = SlimService(identity="acme/lab/mas", url="http://127.0.0.1:46357", secret=_SECRET)
    assert "acme/lab/mas" in repr(service)
    service.close()


def test_an_error_from_the_bindings_never_carries_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorld.reset()
    desk = _desk()

    def refuse(self, name, secret):
        raise RuntimeError(f"cannot authenticate with {secret}")

    monkeypatch.setattr(slim_fake.FakeService, "create_app_with_secret", refuse)
    with pytest.raises(AgentCommError) as raised:
        desk.link("a")
    assert _SECRET not in str(raised.value) and "cannot authenticate with ***" in str(raised.value)
    assert raised.value.__cause__ is None  # the original exception, which holds the secret, is not chained

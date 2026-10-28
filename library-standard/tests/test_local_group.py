#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process group link: fabric, names, channels, identity, headers."""

from __future__ import annotations

import asyncio
import copy
import pickle
from typing import Any

import pytest
from mas.library.standard.lib.comm.capabilities import CAPABILITY_FLAGS
from mas.library.standard.lib.comm.headers import MAX_HEADER_COUNT, MAX_HEADER_VALUE, get_headers, validate_headers
from mas.library.standard.lib.comm.names import namespace_of
from mas.library.standard.lib.comm.payload import label_untrusted_peer
from mas.library.standard.lib.comm.send_context import align_send
from mas.library.standard.plugins.agentcomm.layer import BoundLayer
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.agentcomm.local_group import LocalGroupAgentComm, LocalGroupFabric
from mas.library.standard.plugins.agentcomm.stack import StackAgentComm
from mas.library.standard.plugins.agentcomm.trace_context import TraceContextLayer
from mas.library.standard.testing.agent_comm_checks import check_agent_comm, check_agent_comm_async
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext


def _ack(task: str, **_kw: Any) -> str:
    return f"ack:{task}"


def _labelled(link: LocalGroupAgentComm, text: str) -> str:
    return label_untrusted_peer(text, sender=link.agent_id, intent="publish")


def _links(
    n: int = 3,
    *,
    prefix: str = "acme/lab/mas",
    fabric: LocalGroupFabric | None = None,
    **kwargs: Any,
) -> tuple[LocalGroupFabric, list[LocalGroupAgentComm]]:
    fabric = fabric or LocalGroupFabric(seed=1, deterministic=True)
    kwargs.setdefault("on_request", _ack)
    names = [chr(ord("a") + i) for i in range(n)] if n <= 26 else [str(i) for i in range(n)]
    links = [LocalGroupAgentComm(name=f"{prefix}/{name}", fabric=fabric, **kwargs) for name in names]
    return fabric, links


def _on_request(links: list[LocalGroupAgentComm]):
    def _set(index: int, handler) -> None:
        links[index]._on_request = handler

    return _set


def test_check_agent_comm_against_local_group() -> None:
    _, links = _links(3)
    check_agent_comm(links, on_request=_on_request(links))


@pytest.mark.asyncio
async def test_check_agent_comm_async_against_local_group() -> None:
    _, links = _links(2)
    await check_agent_comm_async(links)


def test_check_agent_comm_against_stack_and_trace_context() -> None:
    _, links = _links(3)
    stacked = [StackAgentComm(layers=[link, TraceContextLayer()]) for link in links]
    check_agent_comm(stacked, on_request=_on_request(links))


def test_stack_injects_fabric_and_run_turn() -> None:
    fabric = LocalGroupFabric(deterministic=True)

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        del agent_id
        return f"turn:{task}"

    a = StackAgentComm(
        layers=[{"type": "local-group", "params": {"name": "acme/lab/mas/a"}}],
        fabric=fabric,
        run_turn=run_turn,
    )
    b = StackAgentComm(
        layers=[{"type": "local-group", "params": {"name": "acme/lab/mas/b"}}],
        fabric=fabric,
        run_turn=run_turn,
    )
    assert b.agent_id == "acme/lab/mas/b"
    assert a.send("b", "hi") == "turn:hi"


def test_run_turn_is_the_handler() -> None:
    seen: list[tuple[str, str, int, str, str]] = []

    def run_turn(agent_id: str, task: str, correlation_id: int, caller_call_id: str, context_id: str) -> str:
        seen.append((agent_id, task, correlation_id, caller_call_id, context_id))
        return f"turn:{task}"

    fabric = LocalGroupFabric(deterministic=True)
    a = LocalGroupAgentComm(run_turn, name="acme/lab/mas/a", fabric=fabric)
    b = LocalGroupAgentComm(run_turn, name="acme/lab/mas/b", fabric=fabric)
    assert a.send("b", "hello", correlation_id=7, caller_call_id="call-9", context_id="ctx") == "turn:hello"
    assert seen == [(b.agent_id, "hello", 7, "call-9", "ctx")]
    assert b.agent_id == "acme/lab/mas/b"


@pytest.mark.asyncio
async def test_asend_uses_run_turn_asend() -> None:
    class Handler:
        def __init__(self) -> None:
            self.sync = 0
            self.async_calls: list[tuple[str, str, int, str, str]] = []

        def __call__(self, agent_id: str, task: str, correlation_id: int, caller_call_id: str, context_id: str) -> str:
            self.sync += 1
            return f"sync:{task}"

        async def asend(
            self, agent_id: str, task: str, correlation_id: int, caller_call_id: str, context_id: str
        ) -> str:
            self.async_calls.append((agent_id, task, correlation_id, caller_call_id, context_id))
            return f"async:{task}"

    handler = Handler()
    fabric = LocalGroupFabric(deterministic=True)
    a = LocalGroupAgentComm(handler, name="acme/lab/mas/a", fabric=fabric)
    b = LocalGroupAgentComm(handler, name="acme/lab/mas/b", fabric=fabric)
    assert await a.asend("b", "hello", correlation_id=7, caller_call_id="call-9", context_id="ctx") == "async:hello"
    assert handler.sync == 0
    assert handler.async_calls == [(b.agent_id, "hello", 7, "call-9", "ctx")]
    assert a.send("b", "sync-path") == "sync:sync-path"
    assert handler.sync == 1


def test_no_handler_raises_instead_of_acking() -> None:
    fabric = LocalGroupFabric(deterministic=True)
    a = LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric, on_request=_ack)
    LocalGroupAgentComm(name="acme/lab/mas/b", fabric=fabric)
    with pytest.raises(AgentCommError, match="no handler"):
        a.send("b", "ping")


def test_string_fabric_is_rejected_and_sessions_do_not_share_state() -> None:
    with pytest.raises(AgentCommError, match="LocalGroupFabric"):
        LocalGroupAgentComm(name="acme/lab/mas/a", fabric="default")  # type: ignore[arg-type]
    first = LocalGroupFabric(deterministic=True)
    LocalGroupAgentComm(name="acme/lab/mas/a", fabric=first, on_request=_ack)
    LocalGroupAgentComm(name="acme/lab/mas/b", fabric=first, on_request=_ack)
    second = LocalGroupFabric(deterministic=True)
    a2 = LocalGroupAgentComm(name="acme/lab/mas/a", fabric=second, on_request=_ack)
    LocalGroupAgentComm(name="acme/lab/mas/b", fabric=second, on_request=_ack)
    assert a2.send("b", "hi") == "ack:hi"
    assert not hasattr(LocalGroupFabric, "named")
    assert not hasattr(LocalGroupFabric, "reset")


def test_stop_unregisters_drains_and_rejects() -> None:
    fabric, (a, b) = _links(2, fabric=LocalGroupFabric(inbox_limit=8, deterministic=True))
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    channel = a.create_channel("room", members=["a", "b"], options={})
    a.publish(channel, "one")
    assert seen == [_labelled(a, "one")]
    assert b.name in fabric.nodes
    assert b.receive() is None
    b.stop()
    assert b.name not in fabric.nodes
    assert list(b._inbox) == []
    with pytest.raises(AgentCommError, match="no peer"):
        a.send("b", "late")
    with pytest.raises(AgentCommError, match="stopped"):
        b.send("a", "late")
    with pytest.raises(AgentCommError, match="stopped"):
        b.publish(channel, "late")
    LocalGroupAgentComm(name=b.name, fabric=fabric, on_request=_ack)
    assert a.send("b", "again") == "ack:again"


def test_stack_starts_the_link_once() -> None:
    class CountStart(LocalGroupAgentComm):
        def start(self) -> None:
            self._starts = getattr(self, "_starts", 0) + 1
            super().start()

        def stop(self) -> None:
            self._stops = getattr(self, "_stops", 0) + 1
            super().stop()

    fabric = LocalGroupFabric(deterministic=True)
    link = CountStart(name="acme/lab/mas/a", fabric=fabric, on_request=_ack)
    stack = StackAgentComm(layers=[link, TraceContextLayer()])
    stack.start()
    stack.start()
    assert link._starts == 1
    stack.stop()
    stack.stop()
    assert link._stops == 1


def test_sender_comes_from_the_link() -> None:
    _, (a, b) = _links(2)
    seen: list[str] = []

    def handler(task: str, **kw: Any) -> str:
        seen.append(str(kw.get("sender") or ""))
        return f"ack:{task}"

    b._on_request = handler
    forged = SendContext(headers={"k": "v"}, sender="forged")
    assert a.send("b", "hi", context=forged) == "ack:hi"
    assert seen == [a.agent_id]
    assert a.agent_id == "acme/lab/mas/a"


def test_redaction_layer_can_drop_baggage() -> None:
    class Redact(BoundLayer):
        def send(self, target_agent_id: str, task: str, **kwargs: Any) -> str:
            headers, context = align_send(kwargs.get("headers"), kwargs.get("context"))
            headers.pop("baggage", None)
            kwargs["headers"] = headers
            kwargs["context"] = context
            return super().send(target_agent_id, task, **kwargs)

    _, (a, b, _c) = _links(3)
    stacked = StackAgentComm(layers=[a, Redact()])
    seen: list[dict[str, str]] = []

    def handler(task: str, **_kw: Any) -> str:
        seen.append(get_headers())
        return f"ack:{task}"

    b._on_request = handler
    hop = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    assert stacked.send(b.agent_id, "x", headers={"traceparent": hop, "baggage": "secret=1"}) == "ack:x"
    assert seen[0].get("traceparent") == hop
    assert "baggage" not in seen[0]


def test_authorization_does_not_ride_the_next_send() -> None:
    _, (a, b, c) = _links(3)
    hopped: list[dict[str, str]] = []

    def on_b(task: str, **_kw: Any) -> str:
        return b.send("c", f"fwd:{task}")

    def on_c(task: str, **_kw: Any) -> str:
        hopped.append(get_headers())
        return f"ack:{task}"

    b._on_request = on_b
    c._on_request = on_c
    a.send("b", "hi", headers={"authorization": "secret", "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"})
    assert "authorization" not in hopped[0]
    assert "traceparent" in hopped[0]


def test_send_context_copy_and_pickle() -> None:
    ctx = SendContext(headers={"k": "v"}, intent="task", sender="x")
    assert copy.copy(ctx).headers["k"] == "v"
    assert copy.deepcopy(ctx).headers["k"] == "v"
    loaded = pickle.loads(pickle.dumps(ctx))
    assert dict(loaded.headers) == {"k": "v"}
    with pytest.raises(TypeError):
        loaded.headers["k"] = "nope"  # type: ignore[index]


def test_headers_are_copied_per_receiver() -> None:
    _, (a, b, c) = _links(3)
    channel = a.create_channel("room", members=["a", "b", "c"], options={})
    got: list[dict[str, str]] = []
    b.subscribe(lambda msg: (msg["headers"].update(tampered="yes"), got.append(get_headers())))
    c.subscribe(lambda msg: got.append(dict(msg["headers"])))
    a.publish(channel, "x", headers={"k": "v"})
    assert got == [{"k": "v"}, {"k": "v"}]
    seen: list[dict[str, str]] = []
    b._on_request = lambda task, **_kw: seen.append(get_headers()) or f"ack:{task}"
    assert a.send("b", "t", headers={"k": "turn"}) == "ack:t"
    assert seen == [{"k": "turn"}]
    assert get_headers() == {}


def test_capabilities_are_honest() -> None:
    _, links = _links(1)
    caps = links[0].capabilities()
    assert set(caps) == set(CAPABILITY_FLAGS)
    assert caps["unicast"] is True
    assert caps["anycast"] is True
    assert caps["group"] is True
    assert caps["ordered"] is True
    assert caps["isolation"] is True
    assert caps["reliable"] is False
    assert caps["e2e"] is False
    assert caps["persistent_groups"] is False


def test_anycast_is_sticky_and_seedable() -> None:
    def on_request(task: str, **kw: Any) -> str:
        return str(kw["agent_id"])

    fabric = LocalGroupFabric(seed=7, deterministic=True)
    LocalGroupAgentComm(name="acme/lab/worker/i1", fabric=fabric, on_request=on_request)
    LocalGroupAgentComm(name="acme/lab/worker/i2", fabric=fabric, on_request=on_request)
    chooser = LocalGroupAgentComm(name="acme/lab/client/c", fabric=fabric, on_request=_ack)
    seen = {chooser.send("acme/lab/worker", "who") for _ in range(8)}
    assert len(seen) == 1
    fabric_b = LocalGroupFabric(seed=7, deterministic=True)
    LocalGroupAgentComm(name="acme/lab/worker/i1", fabric=fabric_b, on_request=on_request)
    LocalGroupAgentComm(name="acme/lab/worker/i2", fabric=fabric_b, on_request=on_request)
    chooser_b = LocalGroupAgentComm(name="acme/lab/client/c", fabric=fabric_b, on_request=_ack)
    assert chooser_b.send("acme/lab/worker", "who") == next(iter(seen))


def test_namespace_isolation() -> None:
    fabric = LocalGroupFabric(seed=1, deterministic=True)
    a = LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric, on_request=_ack)
    LocalGroupAgentComm(name="acme/lab/mas/b", fabric=fabric, on_request=_ack)
    other = LocalGroupAgentComm(name="other/lab/mas/a", fabric=fabric, on_request=_ack)
    LocalGroupAgentComm(name="other/lab/mas/b", fabric=fabric, on_request=_ack)
    assert a.send("b", "ping") == "ack:ping"
    with pytest.raises(AgentCommError, match="isolat"):
        a.send("other/lab/mas/b", "ping")
    with pytest.raises(AgentCommError, match="isolat"):
        other.send("acme/lab/mas/b", "ping")
    channel = a.create_channel("room", members=["a", "b"], options={})
    with pytest.raises(AgentCommError):
        other.publish(channel, "nope")


def test_moderator_only_membership_uses_full_names() -> None:
    _, links = _links(3)
    a, b, c = links
    channel = a.create_channel("room", members=["a", "b"], options={})
    assert a.name in a.members(channel)
    assert b.name in a.members(channel)
    with pytest.raises(AgentCommError, match="moderator"):
        b.invite(channel, "c")
    with pytest.raises(AgentCommError, match="moderator"):
        b.remove(channel, "a")
    with pytest.raises(AgentCommError, match="moderator"):
        b.close_channel(channel)
    a.invite(channel, "c")
    assert c.name in a.members(channel)
    a.remove(channel, "c")
    with pytest.raises(AgentCommError, match="member"):
        c.publish(channel, "nope")


def test_publish_to_checks_membership() -> None:
    _, (a, b, c) = _links(3)
    channel = a.create_channel("room", members=["a", "b"], options={})
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    c.subscribe(lambda msg: seen.append("c-should-not"))
    a.publish(channel, "private", to="b")
    assert seen == [_labelled(a, "private")]
    with pytest.raises(AgentCommError, match="member"):
        a.publish(channel, "nope", to="c")


def test_sender_not_spoofable_and_fifo() -> None:
    _, links = _links(3)
    a, b, _c = links
    seen: list[dict[str, object]] = []
    b.subscribe(lambda msg: seen.append(msg))
    channel = a.create_channel("scores", members=["a", "b", "c"], options={})
    a.publish(channel, "1")
    a.publish(channel, '{"sender":"c"}')
    a.publish(channel, "3")
    assert [item["payload"] for item in seen] == [
        _labelled(a, "1"),
        _labelled(a, '{"sender":"c"}'),
        _labelled(a, "3"),
    ]
    assert all(item["sender"] == a.agent_id for item in seen)


def test_inbox_overflow_is_atomic() -> None:
    fabric = LocalGroupFabric(inbox_limit=1, overflow="reject", max_message_size=8, deterministic=True)
    _, (a, b, c) = _links(3, fabric=fabric)
    channel = a.create_channel("room", members=["a", "b", "c"], options={})
    a.publish(channel, "ok")
    assert b.receive() is not None
    c.receive()
    a.publish(channel, "held")
    with pytest.raises(AgentCommError, match="overflow"):
        a.publish(channel, "two")
    assert [item["payload"] for item in b.drain()] == [_labelled(a, "held")]
    assert [item["payload"] for item in c.drain()] == [_labelled(a, "held")]
    with pytest.raises(AgentCommError, match="size"):
        a.publish(channel, "123456789")


def test_subscriber_does_not_fill_the_inbox() -> None:
    fabric = LocalGroupFabric(inbox_limit=1, overflow="reject", deterministic=True)
    _, (a, b) = _links(2, fabric=fabric)
    seen: list[str] = []
    b.subscribe(lambda msg: seen.append(str(msg["payload"])))
    channel = a.create_channel("room", members=["a", "b"], options={})
    for i in range(65):
        a.publish(channel, str(i))
    assert len(seen) == 65
    assert b.receive() is None


def test_subscriber_errors_are_isolated() -> None:
    _, (a, b) = _links(2)
    order: list[str] = []

    def boom(_msg: dict[str, object]) -> None:
        order.append("boom")
        raise RuntimeError("subscriber died")

    def ok(msg: dict[str, object]) -> None:
        order.append(str(msg["payload"]))

    b.subscribe(boom)
    b.subscribe(ok)
    channel = a.create_channel("room", members=["a", "b"], options={})
    a.publish(channel, "go")
    assert order == ["boom", _labelled(a, "go")]


def test_channel_name_cannot_shadow_or_leave_namespace() -> None:
    _, (a, _b) = _links(2)
    with pytest.raises(AgentCommError, match="shadow"):
        a.create_channel("a", members=["a"], options={})
    with pytest.raises(AgentCommError, match="shadow"):
        a.create_channel("acme/lab/mas/a", members=["a"], options={})
    with pytest.raises(AgentCommError, match="shadow"):
        a.create_channel("acme/lab/mas", members=["a"], options={})
    with pytest.raises(AgentCommError, match="isolat"):
        a.create_channel("other/lab/room", members=["a"], options={})
    with pytest.raises(AgentCommError):
        a.create_channel("room with space", members=["a"], options={})
    assert a.create_channel("room", members=["a", "b"], options={}) == "room"


def test_deterministic_message_ids() -> None:
    fabric_a = LocalGroupFabric(seed=3, deterministic=True)
    _, first = _links(2, fabric=fabric_a)
    seen_a: list[str] = []
    first[1].subscribe(lambda msg: seen_a.append(str(msg["id"])))
    channel = first[0].create_channel("room", members=["a", "b"], options={})
    first[0].publish(channel, "x")
    fabric_b = LocalGroupFabric(seed=3, deterministic=True)
    _, second = _links(2, fabric=fabric_b)
    seen_b: list[str] = []
    second[1].subscribe(lambda msg: seen_b.append(str(msg["id"])))
    channel_b = second[0].create_channel("room", members=["a", "b"], options={})
    second[0].publish(channel_b, "x")
    assert seen_a == seen_b
    assert seen_a


def test_fanout_n50() -> None:
    _fabric, links = _links(50)
    members = [link.instance for link in links]
    received = {link.agent_id: 0 for link in links[1:]}
    for link in links[1:]:
        link.subscribe(lambda msg, owner=link.agent_id: received.__setitem__(owner, received[owner] + 1))
    channel = links[0].create_channel("blast", members=members, options={})
    links[0].publish(channel, "go")
    assert all(count == 1 for count in received.values())
    assert len(received) == 49


@pytest.mark.asyncio
async def test_asyncio_stress_and_asend_parity() -> None:
    _, links = _links(12)
    a, *peers = links
    replies = await asyncio.gather(*[a.asend(peer.instance, "ping") for peer in peers])
    assert replies == ["ack:ping"] * 11
    assert await a.asend("b", "z") == a.send("b", "z")


def test_drop_oldest_closed_channel_and_name_errors() -> None:
    fabric = LocalGroupFabric(inbox_limit=1, overflow="drop_oldest", deterministic=True)
    _, links = _links(2, fabric=fabric)
    a, b = links
    channel = a.create_channel("room", members=["a", "b"], options={})
    a.publish(channel, "1")
    a.publish(channel, "2")
    assert [item["payload"] for item in b.drain()] == [_labelled(a, "2")]
    a.close_channel(channel)
    with pytest.raises(AgentCommError, match="closed"):
        a.publish(channel, "late")
    with pytest.raises(AgentCommError, match="unknown"):
        a.members("no-such")
    with pytest.raises(AgentCommError, match="org/ns/service/instance"):
        LocalGroupAgentComm(name="only-two/parts", fabric=fabric)
    with pytest.raises(AgentCommError, match="duplicate"):
        LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric)
    with pytest.raises(ValueError, match="overflow"):
        LocalGroupFabric(overflow="nope")
    a.bind(b)
    a.start()
    a.start()
    a.stop()
    a.stop()
    with pytest.raises(AgentCommError, match="stopped"):
        a.start()


def test_name_rejects_empty_parts_and_illegal_characters() -> None:
    fabric = LocalGroupFabric(deterministic=True)
    for bad in ("", "//a/b/c", "acme//lab/mas/a", "/acme/lab/mas/a", "acme/lab/mas/a/", "acme/lab/mas/a b"):
        with pytest.raises(AgentCommError):
            LocalGroupAgentComm(name=bad, fabric=fabric)
    with pytest.raises(AgentCommError, match="org/ns"):
        namespace_of(("only",))


def test_headers_reject_newline_and_oversize() -> None:
    _, (a, _b) = _links(2)
    with pytest.raises(AgentCommError, match="control"):
        a.send("b", "t", headers={"k": "v\n"})
    with pytest.raises(AgentCommError, match="too many"):
        a.send("b", "t", headers={f"h{i}": "v" for i in range(MAX_HEADER_COUNT + 1)})
    with pytest.raises(AgentCommError, match="too long"):
        a.send("b", "t", headers={"k": "x" * (MAX_HEADER_VALUE + 1)})
    with pytest.raises(AgentCommError, match="mapping"):
        validate_headers(["not", "a", "mapping"])  # type: ignore[arg-type]
    with pytest.raises(AgentCommError, match="must be strings"):
        validate_headers({"k": 1})  # type: ignore[dict-item]


def test_channel_history_is_bounded() -> None:
    fabric = LocalGroupFabric(history_limit=1, overflow="reject", deterministic=True)
    _, (a, _b) = _links(2, fabric=fabric)
    channel = a.create_channel("room", members=["a", "b"], options={})
    a.publish(channel, "one")
    with pytest.raises(AgentCommError, match="history"):
        a.publish(channel, "two")
    fabric_drop = LocalGroupFabric(history_limit=1, overflow="drop_oldest", deterministic=True)
    _, (c, _d) = _links(2, fabric=fabric_drop, prefix="acme/lab/other")
    room = c.create_channel("room", members=["a", "b"], options={})
    c.publish(room, "keep-me")
    c.publish(room, "newer")
    assert fabric_drop.channels[f"{c.namespace}/{room}"].history[-1]["payload"] == _labelled(c, "newer")


def test_ambiguous_names_existing_channel_and_private_fabric() -> None:
    fabric = LocalGroupFabric(deterministic=True)
    a = LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric, on_request=_ack)
    LocalGroupAgentComm(name="acme/lab/other/a", fabric=fabric, on_request=_ack)
    with pytest.raises(AgentCommError, match="ambiguous"):
        a.send("a", "x")
    channel = a.create_channel("room", members=["acme/lab/mas/a"], options={})
    assert a.create_channel("room", members=["a"], options={}) == "room"
    with pytest.raises(AgentCommError, match="no peer"):
        a.publish(channel, "loop", to=a.name)
    a.invite(channel, "acme/lab/mas/nobody")
    with pytest.raises(AgentCommError, match="unsupported"):
        a.invite(channel, "acme/lab")
    with pytest.raises(AgentCommError, match="no peer"):
        a.send("acme/lab/missing", "who")
    solo = LocalGroupAgentComm(name="acme/lab/empty/x", on_request=_ack)
    with pytest.raises(AgentCommError, match="no peer"):
        solo.send("a", "x")
    a.close_channel("room")
    assert a.create_channel("room", members=["acme/lab/mas/a"], options={}) == "room"


def test_create_channel_raises_if_another_moderator_owns_it() -> None:
    _, (a, b) = _links(2)
    a.create_channel("room", members=["a", "b"], options={})
    with pytest.raises(AgentCommError, match="already exists"):
        b.create_channel("room", members=["b"], options={})
    with pytest.raises(AgentCommError, match="not supported"):
        a.create_channel("other", members=["a"], options={"ttl": 1})


def test_stop_removes_moderated_channels() -> None:
    fabric, (a, _b) = _links(2)
    a.create_channel("room", members=["a", "b"], options={})
    a.stop()
    again = LocalGroupAgentComm(name=a.name, fabric=fabric, on_request=_ack)
    with pytest.raises(AgentCommError, match="unknown"):
        again.members("room")
    assert again.create_channel("room", members=["a"], options={}) == "room"


def test_history_records_before_subscriber_reply() -> None:
    _, (a, b) = _links(2)
    channel = a.create_channel("room", members=["a", "b"], options={})

    def reply(msg: dict[str, object]) -> None:
        if msg["payload"] == _labelled(b, "first"):
            b.publish(channel, "reply")

    a.subscribe(reply)
    b.publish(channel, "first")
    key = f"{a.namespace}/{channel}"
    assert [item["payload"] for item in fabric_history(a, key)] == [
        _labelled(b, "first"),
        _labelled(b, "reply"),
    ]


def fabric_history(link: LocalGroupAgentComm, key: str) -> list[dict[str, Any]]:
    return link.fabric.channels[key].history


def test_none_task_is_an_agent_comm_error() -> None:
    _, (a, _b) = _links(2)
    with pytest.raises(AgentCommError, match="string"):
        a.send("b", None)  # type: ignore[arg-type]


def test_missing_peer_and_anycast_isolation() -> None:
    fabric = LocalGroupFabric(seed=1, deterministic=True)
    a = LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric, on_request=_ack)
    LocalGroupAgentComm(name="other/lab/worker/i1", fabric=fabric, on_request=_ack)
    with pytest.raises(AgentCommError, match="isolat"):
        a.send("other/lab/worker", "who")
    with pytest.raises(AgentCommError, match="isolat"):
        a.send("other/lab/mas/b", "x")
    with pytest.raises(AgentCommError, match="isolat"):
        a.invite(a.create_channel("room", members=["a"], options={}), "other/lab/mas/x")
    with pytest.raises(AgentCommError, match="unsupported name"):
        a.send("acme/lab", "x")
    ghost = a.create_channel("ghost", members=["a", "nobody"], options={})
    a.publish(ghost, "skip-missing")


def test_local_agent_comm_stays_the_session_bus() -> None:
    def run_turn(target: str, task: str, *_args: object) -> str:
        del target
        return f"bus:{task}"

    local = LocalAgentComm(run_turn)
    assert local.send("peer", "x") == "bus:x"
    assert not hasattr(local, "create_channel")
    assert not hasattr(local, "publish")

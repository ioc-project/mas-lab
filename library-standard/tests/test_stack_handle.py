#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""StackHandle: one stack per local agent, shared services once, closed last, names from the layer."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mas.library.standard.lib.stack import Envelope, Layer, StackError, StackHandle
from mas.library.standard.lib.stack.handle import agent_id_refusal, normalize_layers
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

EVENTS: list[str] = []


class Service:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.started = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1
        EVENTS.append("service.start")

    def close(self) -> None:
        self.closed += 1
        EVENTS.append("service.close")


class Wire(Layer):
    """A transport with a shared service; replies with its own name."""

    needs_lower = False
    made: list[Service] = []

    @classmethod
    def shared_service(cls, params: dict[str, Any]) -> Service:
        service = Service(dict(params))
        cls.made.append(service)
        return service

    @classmethod
    def agent_name(cls, params: dict[str, Any], agent_id: str) -> str:
        if agent_id == "bad":
            raise ValueError("no such name")
        return f"{params.get('prefix', 'wire')}/{agent_id}"

    @classmethod
    def check_agent_id(cls, params: dict[str, Any], agent_id: str) -> None:
        if "." in agent_id:
            raise AgentCommError(f"wire ids hold no dot: {agent_id!r}")

    def __init__(self, *, name: str, service: Service, run_turn: Any = None, agent_id: str = "") -> None:
        super().__init__()
        self.name = name
        self.service = service
        self.run_turn = run_turn
        self.agent_id = agent_id

    def start(self) -> None:
        EVENTS.append(f"{self.agent_id}.start")

    def stop(self) -> None:
        EVENTS.append(f"{self.agent_id}.stop")

    def send(self, envelope: Envelope) -> Any:
        return f"{self.name}->{envelope.meta['target']}:{envelope.payload}"


class Plain(Layer):
    """A transport with no shared service; takes every param it is given."""

    needs_lower = False

    def __init__(self, *, flavour: str = "plain", name: str = "") -> None:
        super().__init__()
        self.flavour = flavour
        self.name = name

    def send(self, envelope: Envelope) -> Any:
        return f"{self.flavour}:{envelope.payload}"


class Upper(Layer):
    """Not a transport: needs a layer below."""

    def send(self, envelope: Envelope) -> Any:
        return "upper:" + str(super().send(envelope))


class Registry:
    """Resolves ids to the classes above, the way the plugin registry does."""

    classes = {"wire": Wire, "plain": Plain, "upper": Upper}

    def resolve_spec(self, kind: str, binding: dict[str, Any]) -> Any:
        if kind == "layer" and binding["type"] != "upper":
            raise KeyError(binding["type"])
        cls = self.classes.get(binding["type"])
        if cls is None:
            raise KeyError(binding["type"])
        return SimpleNamespace(load_class=lambda: cls)


@pytest.fixture(autouse=True)
def _reset() -> None:
    EVENTS.clear()
    Wire.made.clear()


def _layer(name: str, **params: Any) -> dict[str, Any]:
    return {"type": name, "params": params}


def _handle(layers: list[Any] | None = None, run_turn: Any = None) -> StackHandle:
    return StackHandle(
        "desk",
        layers or [_layer("wire", url="x", prefix="acme/lab/mas"), _layer("upper")],
        run_turn,
        registry=Registry(),
    )


def test_layers_are_normalized_to_type_and_params() -> None:
    assert normalize_layers([{"type": "wire", "params": None}]) == [{"type": "wire", "params": {}}]
    assert normalize_layers([{"type": " wire ", "params": {"url": "x"}}]) == [{"type": "wire", "params": {"url": "x"}}]


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([], "at least one layer"),
        (None, "at least one layer"),
        ("wire", "at least one layer"),
        ([{"type": ""}], "cannot be empty"),
        (["wire"], "a {type, params} map"),
        ([{"type": "wire", "extra": 1}], "unknown keys"),
        ([{"type": "wire", "params": [1]}], "params must be a map"),
    ],
)
def test_bad_layer_entries_are_refused(entries: Any, message: str) -> None:
    with pytest.raises(StackError, match=message):
        normalize_layers(entries)


def test_construction_checks_layers_and_transport() -> None:
    with pytest.raises(StackError, match=r"layer 1 \('plain'\) is a transport; only the first layer can be one"):
        StackHandle("s", [_layer("wire"), _layer("plain")], registry=Registry())
    with pytest.raises(StackError, match="unknown layer 'nope'"):
        StackHandle("s", [_layer("nope")], registry=Registry())
    with pytest.raises(StackError, match="the first layer must be a transport; 'upper'"):
        StackHandle("s", [_layer("upper"), _layer("plain")], registry=Registry())
    assert EVENTS == []  # nothing started


def test_each_agent_gets_its_own_started_stack_and_identity() -> None:
    handle = _handle()
    a = handle.link("buyer")
    b = handle.link("seller")
    assert a is not b
    assert a is handle.link("buyer")  # cached
    assert a.send("seller", "quote") == "upper:acme/lab/mas/buyer->seller:quote"
    assert b.send("buyer", "offer") == "upper:acme/lab/mas/seller->buyer:offer"
    wire_a = a._layers[0]
    assert (wire_a.name, wire_a.agent_id) == ("acme/lab/mas/buyer", "buyer")
    assert a._layers[1] is not b._layers[1]
    assert a.fabric_id == b.fabric_id == handle.fabric_id
    assert handle.plugin_id == "wire"
    assert EVENTS == ["service.start", "buyer.start", "seller.start"]


def test_the_shared_service_is_created_once_and_closed_last() -> None:
    handle = _handle()
    handle.start()
    handle.start()
    handle.link("buyer")
    handle.link("seller")
    assert len(Wire.made) == 1
    service = Wire.made[0]
    assert service.params == {"url": "x", "prefix": "acme/lab/mas"}
    assert service.started == 1
    assert handle.link("buyer")._layers[0].service is service
    handle.close()
    assert EVENTS[-3:] == ["buyer.stop", "seller.stop", "service.close"]
    assert service.closed == 1


def test_close_is_idempotent_and_a_closed_handle_does_not_restart() -> None:
    handle = _handle()
    handle.link("buyer")
    handle.close()
    handle.close()
    assert Wire.made[0].closed == 1
    with pytest.raises(StackError, match="closed"):
        handle.link("buyer")


def test_a_failing_link_stop_still_closes_the_service() -> None:
    handle = _handle()
    comm = handle.link("buyer")

    def boom() -> None:
        raise RuntimeError("stop failed")

    comm.stop = boom
    with pytest.raises(RuntimeError, match="stop failed"):
        handle.close()
    assert Wire.made[0].closed == 1


def test_run_turn_is_called_with_the_agent_id() -> None:
    seen: list[tuple[str, str]] = []

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        seen.append((agent_id, task))
        return "ok"

    handle = _handle(run_turn=run_turn)
    turn = handle.link("buyer")._layers[0].run_turn
    assert turn("acme/lab/mas/buyer", "hi", 0, "", "") == "ok"
    override = handle.link("seller", run_turn=lambda agent_id, task, *_: f"own:{agent_id}")
    assert override._layers[0].run_turn("whatever", "x") == "own:seller"
    assert seen == [("buyer", "hi")]


def test_the_name_comes_from_the_layer_class_or_is_the_agent_id() -> None:
    handle = _handle()
    assert handle.agent_name("buyer") == "acme/lab/mas/buyer"
    with pytest.raises(StackError, match="no such name"):
        handle.agent_name("bad")
    with pytest.raises(StackError, match="cannot be empty"):
        handle.agent_name("")
    plain = StackHandle("s", [_layer("plain")], registry=Registry())
    assert plain.agent_name("buyer") == "buyer"
    assert plain.link("buyer")._layers[0].name == "buyer"


def test_a_layer_says_which_agent_ids_it_refuses() -> None:
    params = {"prefix": "w"}
    assert agent_id_refusal(Wire, params, "buyer") == ""
    assert agent_id_refusal(Wire, params, "bad") == "no such name"  # agent_name refuses
    assert agent_id_refusal(Wire, params, "a.b") == "wire ids hold no dot: 'a.b'"  # check_agent_id refuses
    assert agent_id_refusal(Wire, params, " ") == "an agent id cannot be empty"
    assert agent_id_refusal(Plain, {}, "a.b") == ""  # a layer with neither takes any id


def test_a_layer_without_a_service_takes_every_param() -> None:
    handle = StackHandle("s", [_layer("plain", flavour="x")], registry=Registry())
    assert handle.link("a").send("b", "hi") == "x:hi"
    bad = StackHandle("s", [_layer("plain", typo=1)], registry=Registry())
    with pytest.raises(StackError, match="layer 'plain'"):
        bad.link("a")


def test_a_failed_service_start_closes_the_services_already_started() -> None:
    class Broken(Service):
        def start(self) -> None:
            raise RuntimeError("node down")

    class Second(Upper):
        @classmethod
        def shared_service(cls, params: dict[str, Any]) -> Service:
            return Broken(params)

    Registry.classes["second"] = Second
    try:
        handle = StackHandle("s", [_layer("wire"), _layer("second")], registry=Registry())
        with pytest.raises(RuntimeError, match="node down"):
            handle.start()
        assert Wire.made[0].closed == 1
    finally:
        del Registry.classes["second"]


def test_real_registry_resolves_local_group_with_its_identity() -> None:
    handle = StackHandle(
        "room", [_layer("local-group", identity="acme/lab/mas")], run_turn=lambda agent, task, *_: f"{agent}:{task}"
    )
    a = handle.link("a")
    handle.link("b")
    assert a._layers[0].link.name == "acme/lab/mas/a"
    assert a.send("b", "x") == "b:x"
    handle.close()
    with pytest.raises(StackError, match="three-part"):
        StackHandle("room", [_layer("local-group", identity="acme/lab")]).link("a")


def test_a_class_that_declares_no_hook_is_a_plain_chain_member() -> None:
    from mas.library.standard.lib.stack.handle import agent_id_refusal, agent_name, is_direct_link, listen_refusal
    from mas.library.standard.plugins.agentcomm.local import LocalAgentComm

    assert agent_name(LocalAgentComm, {}, "triage") == "triage"
    assert agent_id_refusal(LocalAgentComm, {}, "any.thing") == ""
    assert listen_refusal([LocalAgentComm]) == ""
    assert is_direct_link(LocalAgentComm)  # no ChainMember hooks: one agent at its own address


def test_only_a_session_turn_gives_the_link_an_async_path() -> None:
    import asyncio

    from mas.library.standard.lib.stack.handle import SessionTurn

    async def acall(agent_id: str, task: str, *_args: object) -> str:
        return f"async:{agent_id}:{task}"

    handle = _handle()
    plain = handle.link("buyer", run_turn=lambda agent_id, task, *_: f"sync:{agent_id}:{task}")._layers[0].run_turn
    assert not isinstance(plain, SessionTurn)
    both = handle.link("seller", run_turn=SessionTurn(lambda agent_id, task, *_: "sync", acall))._layers[0].run_turn
    assert isinstance(both, SessionTurn)
    assert both("acme/lab/mas/seller", "x") == "sync"
    assert asyncio.run(both.asend("acme/lab/mas/seller", "x")) == "async:seller:x"


def test_a_failing_link_stop_and_a_failing_service_close_raise_the_link_error() -> None:
    handle = _handle()
    comm = handle.link("buyer")

    def stop_fails() -> None:
        raise RuntimeError("stop failed")

    def close_fails() -> None:
        raise RuntimeError("close failed")

    comm.stop = stop_fails
    Wire.made[0].close = close_fails  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="stop failed"):
        handle.close()


def test_a_layer_that_fails_to_start_leaves_no_layer_of_the_stack_running() -> None:
    class Refuses(Upper):
        def start(self) -> None:
            raise RuntimeError("cannot start")

    class _Registry(Registry):
        classes = {**Registry.classes, "upper": Refuses}

    handle = StackHandle("desk", [_layer("wire", url="x"), _layer("upper")], registry=_Registry())
    with pytest.raises(RuntimeError, match="cannot start"):
        handle.link("buyer")
    assert EVENTS == ["service.start", "buyer.start", "buyer.stop"]
    handle.close()
    assert EVENTS[-1] == "service.close"


def test_the_inbound_chain_calls_the_turn_of_the_agent_it_serves() -> None:
    from mas.library.standard.lib.stack.agent_comm import turn_handler

    handler = turn_handler(lambda agent_id, task, *args: f"{agent_id}:{task}:{args}", "billing")
    reply = handler(Envelope(payload="refund", meta={"correlation_id": 3, "caller_call_id": "c", "context_id": "x"}))
    assert reply == "billing:refund:(3, 'c', 'x')"

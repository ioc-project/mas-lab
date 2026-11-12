# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Stack, BoundLayer, capability flags, and shared unicast checks."""

from __future__ import annotations

import copy
import pickle
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml
from mas.library.standard.lib.comm.capabilities import (
    CAPABILITY_FLAGS,
    SECURITY_FLAGS,
    capability_set,
    declared_capabilities,
    merge_capabilities,
)
from mas.library.standard.lib.comm.headers import get_headers
from mas.library.standard.plugins.agentcomm.layer import BoundLayer
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.agentcomm.stack import StackAgentComm
from mas.library.standard.plugins.agentcomm.trace_context import TraceContextLayer
from mas.library.standard.testing.agent_comm_checks import (
    MemoryLink,
    UnicastOnlyLink,
    check_agent_comm,
    check_agent_comm_async,
    make_unicast_mesh,
)
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext


def _on_request(links: list[MemoryLink]):
    def _set(index: int, handler) -> None:
        links[index]._on_request = handler

    return _set


def test_check_agent_comm_against_memory_mesh() -> None:
    links = make_unicast_mesh()
    check_agent_comm(links, on_request=_on_request(links))


def test_check_agent_comm_against_stack_of_one() -> None:
    links = make_unicast_mesh()
    check_agent_comm([StackAgentComm(layers=[link]) for link in links], on_request=_on_request(links))


def test_check_agent_comm_against_trace_over_memory() -> None:
    links = make_unicast_mesh()
    stacked = [StackAgentComm(layers=[link, TraceContextLayer()]) for link in links]
    check_agent_comm(stacked, on_request=_on_request(links))


def test_empty_stack_rejected() -> None:
    with pytest.raises(ValueError, match="layer"):
        StackAgentComm(layers=[])


def test_bind_layer_cannot_sit_at_the_bottom() -> None:
    with pytest.raises(ValueError, match="bottom"):
        StackAgentComm(layers=[TraceContextLayer(), LocalAgentComm(lambda *_: "x")])


def test_layers_above_bottom_must_bind() -> None:
    class NoBind:
        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "ok"

    with pytest.raises(ValueError, match="bind"):
        StackAgentComm(layers=[LocalAgentComm(lambda *_: "x"), NoBind()])
    with pytest.raises(ValueError, match="send"):
        StackAgentComm(layers=[object()])


def test_stack_of_one_equals_the_layer() -> None:
    links = make_unicast_mesh()
    inner = links[0]
    stacked = StackAgentComm(layers=[inner])
    assert stacked.send("b", "hello") == inner.send("b", "hello")
    assert stacked.capabilities() == inner.capabilities()


def test_or_merge_cannot_remove_a_flag() -> None:
    groupish = capability_set(unicast=True, group=True)
    uni = capability_set(unicast=True)
    merged = merge_capabilities(groupish, uni)
    assert merged is not None
    assert merged["group"] is True
    assert merged["unicast"] is True


def test_unknown_layer_makes_the_stack_unknown() -> None:
    assert merge_capabilities(capability_set(unicast=True), None) is None
    for flag in SECURITY_FLAGS:
        assert flag in CAPABILITY_FLAGS


class _Undeclared:
    pass


class _ClassRegistry:
    def __init__(self, classes: dict[str, type]) -> None:
        self.classes = classes

    def resolve_spec(self, spec_key: str, binding: dict | None) -> object:
        assert spec_key == "agent_comm"
        cls = self.classes[(binding or {}).get("type") or "link"]

        class Info:
            def load_class(self) -> type:
                return cls

        return Info()


def test_declared_capabilities_unknown_type_is_none() -> None:
    assert declared_capabilities("no-such-plugin") is None
    assert declared_capabilities({"type": "local"})["unicast"] is True  # type: ignore[index]


def test_declared_capabilities_read_from_plugin_classes() -> None:
    registry = _ClassRegistry(
        {
            "link": LocalAgentComm,
            "deco": TraceContextLayer,
            "stack": StackAgentComm,
            "bare": _Undeclared,
        }
    )
    assert declared_capabilities("link", registry=registry) == LocalAgentComm.CAPABILITIES
    assert declared_capabilities("deco", registry=registry) == capability_set()
    stacked = {"type": "stack", "params": {"layers": [{"type": "link"}, {"type": "deco"}]}}
    assert declared_capabilities(stacked, registry=registry) == LocalAgentComm.CAPABILITIES
    assert declared_capabilities({"type": "stack", "params": {"layers": ["link", "bare"]}}, registry=registry) is None
    assert declared_capabilities("bare", registry=registry) is None
    with pytest.raises(ValueError, match="unknown"):
        capability_set(teleport=True)


def test_layer_ordering_top_sees_send_first() -> None:
    links = make_unicast_mesh()
    order: list[str] = []

    class Probe:
        def bind(self, lower: object) -> None:
            self.lower = lower

        def capabilities(self) -> dict[str, bool]:
            return capability_set(unicast=True)

        def send(self, target: str, task: str, **kwargs: object) -> str:
            order.append("top")
            return self.lower.send(target, task, **kwargs)  # type: ignore[attr-defined]

    stack = StackAgentComm(layers=[links[0], Probe()])
    stack.send("b", "go")
    assert order == ["top"]


def test_start_runs_once_per_layer() -> None:
    links = make_unicast_mesh()
    deco = TraceContextLayer()
    stack = StackAgentComm(layers=[links[0], deco])
    stack.start()
    stack.start()
    assert links[0]._starts == 1
    stack.stop()
    stack.stop()
    assert links[0]._stops == 1
    single = StackAgentComm(layers=[links[1]])
    single.start()
    single.start()
    assert links[1]._starts == 1


def test_unguarded_link_start_and_stop_run_once() -> None:
    class Unguarded:
        def __init__(self) -> None:
            self.starts = 0
            self.stops = 0
            self.agent_id = "u"

        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "ok"

        def start(self) -> None:
            self.starts += 1

        def stop(self) -> None:
            self.stops += 1

    link = Unguarded()
    stack = StackAgentComm(layers=[link, TraceContextLayer()])
    stack.start()
    stack.start()
    stack.stop()
    stack.stop()
    assert link.starts == 1
    assert link.stops == 1


@pytest.mark.asyncio
async def test_asend_parity() -> None:
    links = make_unicast_mesh()
    stack = StackAgentComm(layers=[links[0]])
    assert await stack.asend("b", "hello") == stack.send("b", "hello")
    await check_agent_comm_async(links)


def test_getattr_does_not_recurse_on_private_names() -> None:
    layer = BoundLayer()
    with pytest.raises(AttributeError):
        _ = layer._nope
    with pytest.raises(AttributeError):
        _ = layer.missing
    assert getattr(layer, "x", None) is None
    assert not hasattr(layer, "x")
    links = make_unicast_mesh()
    stack = StackAgentComm(layers=[links[0]])
    with pytest.raises(AttributeError):
        _ = stack._nope
    with pytest.raises(AttributeError):
        _ = stack.no_such_attribute
    assert not hasattr(stack, "no_such_attribute")
    assert stack.agent_id == "a"


def test_stack_bind_asend_fallback_and_capability_attr() -> None:
    class CapsAttr:
        CAPABILITIES = capability_set(unicast=True)
        agent_id = "c"

        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "sync"

        def bind(self, lower: object) -> None:
            self.lower = lower

    class NoId:
        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "x"

    bottom = CapsAttr()
    stack = StackAgentComm(layers=[bottom])
    marker = object()
    stack.bind(marker)
    assert bottom.lower is marker
    assert stack.capabilities()["unicast"] is True
    import asyncio

    assert asyncio.run(stack.asend("x", "t")) == "sync"
    with pytest.raises(AttributeError, match="agent_id"):
        _ = StackAgentComm(layers=[NoId()]).agent_id
    stacked = StackAgentComm(layers=[NoId()])
    assert stacked.send("x", "t") == "x"

    class NoCaps:
        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "ok"

    layer = BoundLayer()
    layer.bind(NoCaps())
    assert layer.capabilities() == capability_set()


def test_send_context_sender_comes_from_the_link() -> None:
    links = make_unicast_mesh()
    seen: list[SendContext | None] = []

    class Capture(BoundLayer):
        def send(self, target: str, task: str, **kwargs: object) -> str:
            seen.append(kwargs.get("context"))  # type: ignore[arg-type]
            return super().send(target, task, **kwargs)

    stack = StackAgentComm(layers=[links[0], Capture()])
    fake = SendContext(headers={"k": "v"}, sender="evil")
    assert stack.send("b", "x", context=fake) == "ack:x"
    stamped = seen[-1]
    assert stamped is not None
    assert stamped.sender == "a"
    assert stamped.headers["k"] == "v"
    with pytest.raises(FrozenInstanceError):
        stamped.sender = "evil"  # type: ignore[misc]


def test_local_link_stamps_sender() -> None:
    seen: list[SendContext | None] = []

    class Capture(BoundLayer):
        def send(self, target: str, task: str, **kwargs: object) -> str:
            seen.append(kwargs.get("context"))  # type: ignore[arg-type]
            return super().send(target, task, **kwargs)

    comm = LocalAgentComm(lambda *_: "ok", agent_id="alice")
    stack = StackAgentComm(layers=[comm, Capture()])
    fake = SendContext(headers={"k": "v"}, sender="evil")
    assert stack.send("b", "t", context=fake) == "ok"
    stamped = seen[-1]
    assert stamped is not None
    assert stamped.sender == "alice"
    assert stamped.headers["k"] == "v"


def test_trace_layer_keeps_headers_and_context_in_sync() -> None:
    seen: list[tuple[object, object]] = []

    class Spy(BoundLayer):
        def send(self, target: str, task: str, **kwargs: object) -> str:
            seen.append((kwargs.get("headers"), kwargs.get("context")))
            return super().send(target, task, **kwargs)

    comm = LocalAgentComm(lambda *_: "ok", agent_id="alice")
    stack = StackAgentComm(layers=[comm, Spy(), TraceContextLayer()])
    assert stack.send("b", "t", headers={"k": "v"}) == "ok"
    headers, ctx = seen[-1]
    assert isinstance(headers, dict)
    assert isinstance(ctx, SendContext)
    assert headers["k"] == "v"
    assert "traceparent" in headers
    assert dict(ctx.headers) == headers
    assert ctx.sender == "alice"


def test_explicit_headers_replace_context_headers() -> None:
    seen: list[tuple[object, object]] = []

    class Spy(BoundLayer):
        def send(self, target: str, task: str, **kwargs: object) -> str:
            seen.append((kwargs.get("headers"), kwargs.get("context")))
            return super().send(target, task, **kwargs)

    class Redact(BoundLayer):
        def send(self, target: str, task: str, **kwargs: object) -> str:
            incoming = dict(kwargs.get("headers") or {})
            if not incoming and kwargs.get("context") is not None:
                incoming = dict(kwargs["context"].headers)  # type: ignore[union-attr]
            incoming.pop("baggage", None)
            return super().send(
                target,
                task,
                headers=incoming,
                context=kwargs.get("context"),  # type: ignore[arg-type]
            )

    comm = LocalAgentComm(lambda *_: "ok", agent_id="alice")
    stack = StackAgentComm(layers=[comm, Spy(), Redact()])
    assert stack.send("b", "t", headers={"k": "v", "baggage": "secret=1"}) == "ok"
    headers, ctx = seen[-1]
    assert isinstance(headers, dict)
    assert isinstance(ctx, SendContext)
    # the stack stamps a traceparent on every send; everything else is what the caller wrote
    assert {k: v for k, v in headers.items() if k != "traceparent"} == {"k": "v"}
    assert dict(ctx.headers) == headers
    assert "traceparent" in headers
    assert "baggage" not in headers
    assert "baggage" not in ctx.headers


def test_send_context_deepcopy_and_pickle() -> None:
    ctx = SendContext(headers={"k": "v", "baggage": "secret=1"}, intent="task", sender="alice")
    deep = copy.deepcopy(ctx)
    assert dict(deep.headers) == dict(ctx.headers)
    assert deep.intent == "task"
    assert deep.sender == "alice"
    assert deep is not ctx
    loaded = pickle.loads(pickle.dumps(ctx))
    assert dict(loaded.headers) == dict(ctx.headers)
    assert loaded.intent == "task"
    assert loaded.sender == "alice"
    assert loaded.mode == ""


def test_stack_starts_every_layer_even_if_top_does_not_forward() -> None:
    links = make_unicast_mesh()

    class NoForward:
        def bind(self, lower: object) -> None:
            self.lower = lower

        def send(self, *args: object, **kwargs: object) -> str:
            return self.lower.send(*args, **kwargs)  # type: ignore[attr-defined]

        def start(self) -> None:
            self.started = True

    stack = StackAgentComm(layers=[links[0], NoForward()])
    stack.start()
    assert links[0]._starts == 1


def test_failed_start_can_be_retried() -> None:
    class Flaky:
        def __init__(self) -> None:
            self.n = 0

        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "x"

        def start(self) -> None:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("once")

    stack_lower = Flaky()
    stack = StackAgentComm(layers=[stack_lower])
    with pytest.raises(RuntimeError, match="once"):
        stack.start()
    stack.start()
    assert stack_lower.n == 2


def test_stack_rejects_bad_headers_at_the_link() -> None:
    links = make_unicast_mesh()
    stack = StackAgentComm(layers=[links[0]])
    with pytest.raises(AgentCommError, match="control"):
        stack.send("b", "x", headers={"k": "v\n"})


def test_unknown_live_layer_has_no_security_claims() -> None:
    class Bare:
        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "x"

    stack = StackAgentComm(layers=[Bare()])
    assert stack.capabilities() == {}
    for flag in SECURITY_FLAGS:
        assert not stack.capabilities().get(flag)


def test_a_stack_says_who_it_is_from_its_bottom_link_and_forwards_nothing_else() -> None:
    class Bare:
        def send(self, *args: object, **kwargs: object) -> str:
            return "x"

        def extra(self) -> str:
            return "not part of the stack's surface"

    class Named(Bare):
        plugin_id = "named"
        endpoint = "http://127.0.0.1:1"
        agent_id = "a"

    plain = StackAgentComm(layers=[Bare()])
    assert (plain.plugin_id, plain.endpoint, plain.fabric_id) == ("", "", None)
    with pytest.raises(AttributeError):
        _ = plain.agent_id
    named = StackAgentComm(layers=[Named()])
    assert (named.plugin_id, named.endpoint, named.agent_id) == ("named", "http://127.0.0.1:1", "a")
    with pytest.raises(AttributeError):
        _ = named.extra  # a stack has no proxy to the link: the link is ``_layers[0].link``
    assert named._layers[0].link.extra()


def test_a_bound_layer_sees_the_identity_of_the_link_below_and_nothing_else() -> None:
    class Named:
        plugin_id = "named"
        endpoint = "http://127.0.0.1:1"
        agent_id = "a"
        fabric_id = 7
        secret = "kept on the link"

        def send(self, *args: object, **kwargs: object) -> str:
            return "x"

    decorator = BoundLayer()
    StackAgentComm(layers=[Named(), decorator])
    assert (decorator.plugin_id, decorator.endpoint, decorator.agent_id, decorator.fabric_id) == (
        "named",
        "http://127.0.0.1:1",
        "a",
        7,
    )
    with pytest.raises(AttributeError):
        _ = decorator.secret


def test_a_layer_needs_no_hook_to_be_a_layer_and_a_legacy_object_is_adapted_once() -> None:
    from mas.library.standard.lib.stack import Layer
    from mas.library.standard.lib.stack.legacy import LegacyLayer, LinkLayer, adapt_legacy

    class Plain(Layer):
        pass

    layer = Plain()
    assert layer.honoured_send_intents() is None and not layer.wraps_inbound
    assert (layer.plugin_id, layer.endpoint, layer.fabric_id, layer.agent_id) == ("", "", None, "")
    assert Plain.agent_name({}, "a") == "a" and Plain.shared_service({}) is None and Plain.LISTEN_REFUSAL == ""
    Plain.check_agent_id({}, "anything")
    assert adapt_legacy(layer, bottom=False) is layer

    class Link:
        def send(self, *args: object, **kwargs: object) -> str:
            return "x"

    assert isinstance(adapt_legacy(Link(), bottom=True), LinkLayer)
    assert isinstance(adapt_legacy(BoundLayer(), bottom=False), LegacyLayer)
    with pytest.raises(ValueError, match="bind layer cannot sit at the bottom"):
        adapt_legacy(BoundLayer(), bottom=True)
    with pytest.raises(ValueError, match="must be a link with send"):
        adapt_legacy(object(), bottom=True)
    with pytest.raises(ValueError, match="must bind to the layer below"):
        adapt_legacy(Link(), bottom=False)


def test_bound_layer_forwards_and_asend_fallback() -> None:
    import asyncio

    class CapsOnly:
        CAPABILITIES = capability_set(unicast=True)

        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "ok"

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    layer = BoundLayer()
    with pytest.raises(AgentCommError, match="unbound"):
        _ = layer.agent_id
    lower = CapsOnly()
    layer.bind(lower)
    with pytest.raises(AttributeError, match="agent_id"):
        _ = layer.agent_id
    assert layer.capabilities()["unicast"] is True
    layer.start()
    layer.stop()
    assert not hasattr(lower, "started")
    assert not hasattr(lower, "stopped")
    assert asyncio.run(layer.asend("b", "x")) == "ok"


def test_schema_matches_capability_flags() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "docs/schemas/plugins/comm-layer.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    assert schema["required"] == list(CAPABILITY_FLAGS)
    assert set(schema["properties"]) == set(CAPABILITY_FLAGS)


def test_stack_instantiates_dict_layers_via_registry() -> None:
    class Dummy:
        def __init__(self, agent_id: str = "d", run_turn: object | None = None) -> None:
            self.agent_id = agent_id
            self.run_turn = run_turn

        def capabilities(self) -> dict[str, bool]:
            return capability_set(unicast=True)

        def send(self, target_agent_id: str, task: str, **_kwargs: object) -> str:
            del target_agent_id
            return f"dummy:{task}"

        def start(self) -> None:
            return None

    class Info:
        def load_class(self) -> type[Dummy]:
            return Dummy

    class Registry:
        def resolve_spec(self, spec_key: str, binding: dict) -> Info:
            del spec_key, binding
            return Info()

    stack = StackAgentComm(
        layers=[{"type": "dummy", "params": {"agent_id": "d"}}],
        registry=Registry(),
        run_turn=lambda: None,
    )
    assert stack.agent_id == "d"
    assert stack.send("x", "hi") == "dummy:hi"
    stack.start()


def test_local_agent_comm_still_sets_headers() -> None:
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    comm = LocalAgentComm(run_turn)
    assert comm.send("b", "t", headers={"k": "v"}) == "ok"
    assert seen[-1] == {"k": "v"}
    assert comm.capabilities()["unicast"] is True
    assert comm.send("b", "t", context=SendContext(headers={"from-ctx": "1"}, sender="evil")) == "ok"
    assert seen[-1] == {"from-ctx": "1"}


def test_decorator_cannot_strip_flags() -> None:
    class Groupish(MemoryLink):
        CAPABILITIES = capability_set(unicast=True, group=True)

        def capabilities(self) -> dict[str, bool]:
            return dict(self.CAPABILITIES)

    peers: dict[str, MemoryLink] = {}
    lower = Groupish("a", peers)
    stack = StackAgentComm(layers=[lower, UnicastOnlyLink()])
    assert stack.capabilities()["group"] is True
    assert stack.capabilities()["unicast"] is True


def test_check_agent_comm_custom_reply_prefix() -> None:
    links = make_unicast_mesh()

    def handler(task: str, **_kw: object) -> str:
        return f"pong:{task}"

    for link in links:
        link._on_request = handler
    check_agent_comm(links, ping="hi", ack_prefix="pong:")


def test_check_agent_comm_rejects_short_list() -> None:
    with pytest.raises(ValueError, match="three"):
        check_agent_comm([])


def test_stack_honours_intents_of_the_bottom_link() -> None:
    class Bottom:
        agent_id = "bottom"
        HONOURED_SEND_INTENTS = frozenset({"task"})

        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "ok"

    class Top:
        HONOURED_SEND_INTENTS = frozenset({"task", "inform", "ask"})

        def bind(self, lower: object) -> None:
            self._lower = lower

        def send(self, *args: object, **kwargs: object) -> str:
            return self._lower.send(*args, **kwargs)

    stack = StackAgentComm(layers=[Bottom(), Top()])
    assert stack.honoured_send_intents() == frozenset({"task"})


def test_stack_stops_top_first() -> None:
    order: list[str] = []

    class Layer:
        def __init__(self, name: str) -> None:
            self.name = name
            self.agent_id = name

        def send(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "ok"

        def bind(self, lower: object) -> None:
            self._lower = lower

        def start(self) -> None:
            order.append(f"start:{self.name}")

        def stop(self) -> None:
            order.append(f"stop:{self.name}")

    stack = StackAgentComm(layers=[Layer("bottom"), Layer("top")])
    stack.start()
    stack.stop()
    assert order == ["start:bottom", "start:top", "stop:top", "stop:bottom"]


def test_check_agent_comm_fails_a_link_that_never_raises() -> None:
    class Silent:
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id

        def send(self, target: str, task: str, **_kwargs: object) -> str:
            del target
            return f"ack:{task}"

        def capabilities(self) -> dict[str, bool]:
            return {flag: True for flag in CAPABILITY_FLAGS}

    with pytest.raises(AssertionError, match="missing peer"):
        check_agent_comm([Silent("a"), Silent("b"), Silent("c")])


def test_a_link_is_found_before_a_layer_wherever_the_entry_sits() -> None:
    """The lookup order is always the plugin kind first, then ``layer``; no position is involved."""
    from types import SimpleNamespace

    from mas.library.standard.lib.comm.compose import instantiate_layers, resolve_layer_class

    class Link:
        pass

    class Frame:
        pass

    class Registry:
        def resolve_spec(self, kind: str, binding: dict) -> SimpleNamespace:
            table = {"agent_comm": {"link": Link}, "layer": {"link": Frame, "frame": Frame}}[kind]
            if binding["type"] not in table:
                raise KeyError(binding["type"])
            return SimpleNamespace(load_class=lambda: table[binding["type"]])

    registry = Registry()
    assert resolve_layer_class("agent_comm", {"type": "link", "params": {}}, registry) is Link
    assert resolve_layer_class("agent_comm", {"type": "frame", "params": {}}, registry) is Frame
    with pytest.raises(KeyError):
        resolve_layer_class("agent_comm", {"type": "nothing", "params": {}}, registry)
    built = instantiate_layers(
        "agent_comm", [{"type": "link"}, {"type": "frame"}, {"type": "link"}], {"registry": registry}
    )
    assert [type(layer) for layer in built] == [Link, Frame, Link]


def test_stack_takes_a_generic_layer_by_live_object() -> None:
    from mas.library.standard.lib.stack import StackSendError
    from mas.library.standard.testing.layer_conformance import FailingLayer

    link = LocalAgentComm(lambda *_: "ok", agent_id="alice")
    stack = StackAgentComm(layers=[link, FailingLayer(targets=("b",), message="b refused")])
    assert stack.send("c", "t") == "ok"
    with pytest.raises(StackSendError, match="b refused"):
        stack.send("b", "t")


def test_an_adapted_link_or_decorator_starts_and_stops_once() -> None:
    from mas.library.standard.lib.stack.legacy import LegacyLayer, LinkLayer

    class Counted(BoundLayer):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def start(self) -> None:
            self.calls.append("start")

        def stop(self) -> None:
            self.calls.append("stop")

    class Link:
        calls: list[str] = []

        def send(self, *args: object, **kwargs: object) -> str:
            return "x"

        def start(self) -> None:
            self.calls.append("start")

        def stop(self) -> None:
            self.calls.append("stop")

    link, decorator = Link(), Counted()
    for layer, calls in ((LinkLayer(link), link.calls), (LegacyLayer(decorator), decorator.calls)):
        layer.start()
        layer.start()
        layer.stop()
        layer.stop()
        assert calls == ["start", "stop"]

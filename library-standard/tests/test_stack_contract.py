# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Layer contract: header drop, the inbound chain, errors at the agent-call seam,
and the transport conformance check."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.library.standard.lib.stack import (
    Envelope,
    Layer,
    Stack,
    StackError,
    StackHandle,
    StackSendError,
)
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.library.standard.plugins.agentcomm.local_group import LocalGroupAgentComm, LocalGroupFabric
from mas.library.standard.plugins.agentcomm.stack import StackAgentComm
from mas.library.standard.plugins.system_tools.send import SendTool
from mas.library.standard.testing.layer_conformance import FailingLayer, RecordingLower, check_transport
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.engine.tool_dispatch import execute_engine_tool


class _Redact(Layer):
    """Drops one header before the send leaves: the redaction case ``drop_headers`` exists for."""

    def send(self, envelope):
        return super().send(envelope.with_(drop_headers=["Authorization"]))


def test_drop_headers_removes_keys_after_the_merge_and_ignores_case() -> None:
    env = Envelope(payload="p", headers={"authorization": "Bearer x", "traceparent": "t", "keep": "1"})
    out = env.with_(headers={"keep": "2", "added": "3"}, drop_headers=["AUTHORIZATION", "added"])
    assert dict(out.headers) == {"traceparent": "t", "keep": "2"}
    assert dict(env.headers)["authorization"] == "Bearer x"  # the old envelope is untouched
    assert dict(env.with_(drop_headers=["missing"]).headers) == dict(env.headers)
    mixed = Envelope(headers={"X-Api-Key": "k", "Keep": "1"})  # a stored name in any case is still dropped
    assert dict(mixed.with_(drop_headers=["x-api-key"]).headers) == {"Keep": "1"}
    with pytest.raises(TypeError, match="list of header names"):
        env.with_(drop_headers="authorization")


def test_a_redaction_layer_keeps_a_header_off_the_wire() -> None:
    lower = RecordingLower()
    Stack([lower, _Redact()]).send(Envelope(payload="x", headers={"authorization": "secret", "a": "1"}))
    sent = dict(lower.seen[-1].headers)
    assert "authorization" not in sent
    assert sent["a"] == "1" and "traceparent" in sent


class _Frame(Layer):
    """A protocol layer: frames the payload on the way down, unframes inbound requests."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag

    def send(self, envelope):
        return super().send(envelope.with_(payload=f"{self.tag}({envelope.payload})"))

    def serve(self, handler):
        def unframe(envelope):
            text = str(envelope.payload)
            assert text.startswith(f"{self.tag}(") and text.endswith(")"), text
            return f"{self.tag}<{handler(envelope.with_(payload=text[len(self.tag) + 1 : -1]))}>"

        return unframe


class _Wire(Layer):
    """A transport that keeps the inbound chain it is given."""

    needs_lower = False

    def __init__(self) -> None:
        super().__init__()
        self.handler = None

    def serve(self, handler):
        self.handler = handler
        return handler

    def send(self, envelope):
        return envelope.payload


def test_serve_wraps_the_handler_top_first_and_hands_the_chain_to_the_transport() -> None:
    wire = _Wire()
    stack = Stack([wire, _Frame("inner"), _Frame("outer")])
    assert stack.wraps_inbound
    stack.serve(lambda env: f"turn:{env.payload}")
    # what arrives is what a peer's same stack sent: framed by the top layer first, so the inner frame is outside
    assert wire.handler(Envelope(payload="inner(outer(hi))")) == "inner<outer<turn:hi>>"
    assert Stack([_Wire(), FailingLayer()]).wraps_inbound is False


def test_a_link_without_serve_refuses_a_protocol_layer_above_it() -> None:
    class Plain:
        def send(self, target, task, **_):
            return task

    with pytest.raises(StackError, match="cannot pass inbound requests"):
        LinkLayer(Plain()).serve(lambda env: "")
    with pytest.raises(StackError, match="cannot pass inbound requests"):
        StackAgentComm([Plain(), _Frame("f")]).serve(lambda env: "")


class _Registry:
    """The real registry, plus the test-local ``failing`` layer."""

    def resolve_spec(self, kind: str, binding: dict):
        if binding.get("type") == "failing":
            if kind != "layer":
                raise KeyError("failing")
            return SimpleNamespace(load_class=lambda: FailingLayer)
        from mas.runtime.registry import get_registry

        return get_registry().resolve_spec(kind, binding)


def _room(*upper: dict) -> StackHandle:
    return StackHandle(
        "room",
        [{"type": "local-group", "params": {"identity": "acme/lab/mas"}}, *upper],
        run_turn=lambda agent, task, *_: f"{agent}:{task}",
        registry=_Registry(),
    )


def _failing(*targets: str, message: str) -> dict:
    return {"type": "failing", "params": {"targets": targets, "message": message}}


def test_layer_errors_come_out_as_agent_comm_errors_with_the_cause() -> None:
    import asyncio

    failing = _room(_failing("b", message="broken"))
    a = failing.link("a")
    failing.link("b")
    with pytest.raises(StackSendError, match="broken") as failed:
        a.send("b", "x")
    assert isinstance(failed.value, AgentCommError)
    assert isinstance(failed.value.__cause__, StackError) and not isinstance(failed.value.__cause__, AgentCommError)
    with pytest.raises(StackSendError, match="broken"):
        asyncio.run(a.asend("b", "x"))
    failing.close()


def test_a_failed_send_comes_back_from_the_model_facing_tool_as_a_failure_string() -> None:
    """The ``delegate_to_b`` tool through ``execute_engine_tool``, as an engine runs it."""
    handle = _room(_failing("b", message="b link down"))
    a = handle.link("a")
    handle.link("b")
    ctx = SimpleNamespace(
        agent_id="a",
        manifest=None,
        agent_comm=a,
        agent_comm_routes={"b": AgentCommRoute("b", handle.plugin_id, a)},
        delegation_peer_descriptions=None,
        send_peer_descriptions=None,
    )
    tool = SendTool(allowed_peers=["b"])
    result = execute_engine_tool("delegate_to_b", engine_contracts=[tool], ctx=ctx, arguments={"task": "refund"})
    assert result == "[send] agent 'b' via local-group failed: b link down"
    import asyncio

    assert asyncio.run(tool.acall("delegate_to_b", {"task": "refund"}, ctx=ctx)) == result
    handle.close()


def test_send_intents_are_the_ones_every_layer_honours() -> None:
    class TaskOnly(Layer):
        def honoured_send_intents(self) -> frozenset[str]:
            return frozenset({"task"})

    handle = _room()
    assert handle.link("a").honoured_send_intents() == frozenset({"task", "inform", "ask"})
    handle.close()
    fabric = LocalGroupFabric()
    comm = StackAgentComm([LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric), TaskOnly()])
    assert comm.honoured_send_intents() == frozenset({"task"})
    assert "intent 'inform' is not supported" in SendTool(comm=comm, allowed_peers=["b"]).send(
        "b", "x", intent="inform"
    )


def test_local_group_passes_the_transport_check() -> None:
    fabric = LocalGroupFabric()
    seen: list[str] = []

    def turn(agent: str, task: str, *_: object) -> str:
        seen.append(task)
        return f"{agent}:{task}"

    LocalGroupAgentComm(turn, name="acme/lab/mas/b", fabric=fabric).start()
    a = LocalGroupAgentComm(name="acme/lab/mas/a", fabric=fabric)
    envelope = Envelope(payload="ping", meta={"target": "b", "intent": "task", "context": SendContext()})
    check_transport(lambda: LinkLayer(a), envelope=envelope, reply="acme/lab/mas/b:ping", delivered=lambda: len(seen))

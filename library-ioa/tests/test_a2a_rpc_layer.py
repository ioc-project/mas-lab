#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The a2a-rpc layer: the A2A SDK over slimrpc, on a real SLIM node and in unit parts."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
from library_ioa.plugins.a2a.layer import A2ALayer, _hop_map, reply_text
from library_ioa.plugins.slim.actor import _SHARED_LOOP
from library_ioa.plugins.slim.agent_comm import SlimAgentComm
from library_ioa.plugins.slim.service import SlimService
from mas.library.standard.lib.comm.capabilities import CAPABILITY_FLAGS
from mas.library.standard.lib.comm.headers import bound_headers, get_headers
from mas.library.standard.lib.stack import Envelope, StackError, StackHandle
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.library.standard.plugins.system_tools.send import SendTool
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext, inbound_send
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.engine.tool_dispatch import aexecute_engine_tool

_TRACE = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

_SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
_HANDLES: list[StackHandle] = []


async def _until(pred: Any, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


def _task(
    state: int, *, text: str = "", artifacts: list[str] | None = None, history: list[Any] | None = None
) -> StreamResponse:
    status = TaskStatus(state=state)
    if text:
        status.message.CopyFrom(new_text_message(text, role=Role.ROLE_AGENT))
    task = Task(id="t", status=status, history=history or [])
    for index, value in enumerate(artifacts or []):
        task.artifacts.append(Artifact(artifact_id=str(index), parts=[Part(text=value)]))
    return StreamResponse(task=task)


# --- reading the answer -----------------------------------------------------------------------------------------


def test_a_message_answer_is_its_text() -> None:
    answer = StreamResponse(message=new_text_message("hi", role=Role.ROLE_AGENT))
    assert reply_text([answer]) == "hi"


def test_a_completed_task_reads_artifacts_then_status_text_then_the_agent_history() -> None:
    done = TaskState.TASK_STATE_COMPLETED
    assert reply_text([_task(done, text="status", artifacts=["a", "b"])]) == "ab"
    assert reply_text([_task(done, text="status")]) == "status"
    history = [new_text_message("q", role=Role.ROLE_USER), new_text_message("from history", role=Role.ROLE_AGENT)]
    assert reply_text([_task(done, history=history)]) == "from history"
    assert reply_text([_task(done)]) == ""


@pytest.mark.parametrize(
    ("state", "word"),
    [
        (TaskState.TASK_STATE_FAILED, "failed"),
        (TaskState.TASK_STATE_REJECTED, "rejected"),
        (TaskState.TASK_STATE_CANCELED, "canceled"),
    ],
)
def test_a_failed_remote_task_raises_without_the_peer_text(state: int, word: str) -> None:
    with pytest.raises(AgentCommError, match=f"^remote A2A task {word}$"):
        reply_text([_task(state, text="why")])
    with pytest.raises(AgentCommError, match=f"^remote A2A task {word}$"):
        reply_text([_task(state)])


def test_no_response_raises() -> None:
    with pytest.raises(AgentCommError, match="no response"):
        reply_text([])


def test_a_stream_that_ends_on_a_status_update_replies_with_its_artifact_text() -> None:
    def artifact(text: str) -> StreamResponse:
        return StreamResponse(artifact_update=TaskArtifactUpdateEvent(artifact=Artifact(parts=[Part(text=text)])))

    done = StreamResponse(
        status_update=TaskStatusUpdateEvent(task_id="t", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    )
    assert reply_text([artifact("po"), artifact("ng"), done]) == "pong"


def test_a_status_update_with_only_a_status_message_is_its_text() -> None:
    done = StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id="t",
            status=TaskStatus(
                state=TaskState.TASK_STATE_COMPLETED, message=new_text_message("from status", role=Role.ROLE_AGENT)
            ),
        )
    )
    assert reply_text([done]) == "from status"


def test_a_status_message_does_not_replace_artifact_text() -> None:
    artifact = StreamResponse(artifact_update=TaskArtifactUpdateEvent(artifact=Artifact(parts=[Part(text="pong")])))
    done = StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id="t",
            status=TaskStatus(
                state=TaskState.TASK_STATE_COMPLETED, message=new_text_message("done", role=Role.ROLE_AGENT)
            ),
        )
    )
    assert reply_text([artifact, done]) == "pong"


def test_hop_map_keeps_only_trace_headers() -> None:
    assert _hop_map(
        {
            "TraceParent": _TRACE,
            "baggage": "k=v",
            "tracestate": "vendor=1",
            "mas.correlation_id": "7",
            "authorization": "secret",
        }
    ) == {"traceparent": _TRACE, "baggage": "k=v", "tracestate": "vendor=1"}


# --- the layer's own checks -------------------------------------------------------------------------------------


def test_the_layer_needs_a_slim_link_below_it() -> None:
    with pytest.raises(StackError, match="runs over slim"):
        A2ALayer().bind(object())  # type: ignore[arg-type]


async def test_the_layer_refuses_another_intent_a_non_string_and_a_missing_target() -> None:
    service = SlimService(identity="acme/lab/desk", url="http://127.0.0.1:9", secret=_SECRET)
    layer = A2ALayer()
    layer.bind(LinkLayer(SlimAgentComm(name="acme/lab/desk/a", service=service)))
    with pytest.raises(AgentCommError, match="intent 'inform' is not supported"):
        await layer.asend(Envelope(payload="x", meta={"target": "b", "intent": "inform"}))
    with pytest.raises(AgentCommError, match="must be a string"):
        await layer.asend(Envelope(payload=1, meta={"target": "b"}))
    with pytest.raises(AgentCommError, match="needs a target"):
        await layer.asend(Envelope(payload="x", meta={}))


def test_bind_does_not_claim_inbound_serve_does() -> None:
    link = SlimAgentComm(
        name="acme/lab/desk/a", service=SlimService(identity="acme/lab/desk", url="http://127.0.0.1:9", secret=_SECRET)
    )
    layer = A2ALayer()
    layer.bind(LinkLayer(link))
    assert link._inbound_claimed is False
    layer.serve(lambda _envelope: None)
    assert link._inbound_claimed is True


def test_the_layer_refuses_an_http_lower() -> None:
    from library_ioa.plugins.http_layer.transport import HttpLayer

    with pytest.raises(StackError, match="runs over slim"):
        A2ALayer().bind(HttpLayer(url="http://127.0.0.1:9"))


def test_start_and_stop_are_idempotent_without_a_handler() -> None:
    layer = A2ALayer()
    layer.start()
    layer.start()
    layer.stop()
    layer.stop()


async def test_the_layer_passes_the_unbound_lifecycle_and_capability_checks() -> None:
    unbound = A2ALayer()
    with pytest.raises(StackError, match="unbound"):
        await unbound.asend(Envelope(payload="x", meta={"target": "b"}))
    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.start()
    layer.start()
    layer.stop()
    layer.stop()
    caps = layer.capabilities()
    assert set(caps) == set(CAPABILITY_FLAGS)
    below = layer._require().capabilities()
    for name, value in below.items():
        assert caps[name] or not value


async def test_an_unbound_send_raises() -> None:
    with pytest.raises(StackError, match="unbound"):
        await A2ALayer().asend(Envelope(payload="x", meta={"target": "b"}))


def test_start_registers_an_app_listener_once() -> None:
    link = SlimAgentComm(
        name="acme/lab/desk/a", service=SlimService(identity="acme/lab/desk", url="http://127.0.0.1:9", secret=_SECRET)
    )
    layer = A2ALayer()
    layer.bind(LinkLayer(link))
    layer.serve(lambda _envelope: None)
    layer.start()
    layer.start()
    assert link._app_listeners.count(layer._on_app_ready) == 1


async def test_a_new_app_restarts_the_rpc_server() -> None:
    link = SlimAgentComm(
        name="acme/lab/desk/a", service=SlimService(identity="acme/lab/desk", url="http://127.0.0.1:9", secret=_SECRET)
    )
    layer = A2ALayer()
    layer.bind(LinkLayer(link))
    layer.serve(lambda _envelope: None)
    layer._astart_server = AsyncMock()  # type: ignore[method-assign]
    await layer._on_app_ready()
    layer._astart_server.assert_awaited_once_with(link)


async def test_recover_does_not_drop_a_server_a_concurrent_reconnect_already_started() -> None:
    """A connect+listen send may reconnect and start RPC before recover runs. Recover must not kill it."""
    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    live = SimpleNamespace(shutdown_async=AsyncMock())
    layer._server = live

    async def start(_link: Any) -> None:
        if layer._server is None:
            layer._server = SimpleNamespace(shutdown_async=AsyncMock())

    layer._astart_server = start  # type: ignore[method-assign]
    stub = _running_stub(ensure=AsyncMock(), mark=lambda: None)

    async def fail() -> None:
        raise RuntimeError("node gone")

    task = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="node gone"):
        await task
    layer._serving = task
    layer._on_serving_done(task, stub)
    await asyncio.sleep(0)
    assert layer._server is live
    live.shutdown_async.assert_not_awaited()


async def test_rpc_server_exit_marks_the_link_dead_and_reconnects() -> None:
    counts = {"ensure": 0, "dead": 0}

    def mark() -> None:
        counts["dead"] += 1

    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    layer._astart_server = AsyncMock()  # type: ignore[method-assign]

    async def ensure() -> None:
        counts["ensure"] += 1

    stub = _running_stub(ensure=ensure, mark=mark)

    async def fail() -> None:
        raise RuntimeError("node gone")

    task = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="node gone"):
        await task
    layer._serving = task
    layer._on_serving_done(task, stub)
    await asyncio.sleep(0)
    assert counts["dead"] == 1
    assert counts["ensure"] == 1
    layer._astart_server.assert_awaited_once()


async def test_rpc_recover_retries_until_the_link_is_up() -> None:
    counts = {"ensure": 0}

    async def ensure() -> None:
        counts["ensure"] += 1
        if counts["ensure"] == 1:
            raise AgentCommError("slim link is failed")

    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    layer._astart_server = AsyncMock()  # type: ignore[method-assign]
    stub = _running_stub(ensure=ensure, mark=lambda: None)

    async def fail() -> None:
        raise RuntimeError("node gone")

    task = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="node gone"):
        await task
    layer._serving = task
    layer._on_serving_done(task, stub)
    await asyncio.wait_for(_until(lambda: counts["ensure"] >= 2), timeout=1.0)
    assert counts["ensure"] >= 2
    layer._astart_server.assert_awaited()


def _rpc_conn(conn_id: str) -> Any:
    return SimpleNamespace(rpc_endpoint=lambda: (None, None, conn_id))


async def test_starting_the_rpc_server_is_a_noop_when_one_is_already_serving() -> None:
    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    serving = asyncio.get_running_loop().create_task(asyncio.sleep(60))
    sentinel = object()
    layer._serving = serving
    layer._server = sentinel
    layer._rpc_conn_id = "conn-1"
    await layer._astart_server(_rpc_conn("conn-1"))
    assert layer._server is sentinel
    serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await serving


async def test_starting_the_rpc_server_replaces_a_server_from_a_previous_connection() -> None:
    """After the node reconnects, a hung serve on the old connection must not block RPC on the new one."""
    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    serving = asyncio.get_running_loop().create_task(asyncio.sleep(60))
    old = SimpleNamespace(shutdown_async=AsyncMock())
    layer._serving = serving
    layer._server = old
    layer._rpc_conn_id = "old"
    with pytest.raises(AttributeError):
        await layer._astart_server(_rpc_conn("new"))
    old.shutdown_async.assert_awaited()
    assert layer._server is None
    assert serving.done()


async def test_a_cancelled_rpc_server_reconnects_unless_the_layer_is_stopping() -> None:
    counts = {"dead": 0}

    def mark() -> None:
        counts["dead"] += 1

    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    stub = _running_stub(ensure=AsyncMock(), mark=mark)
    task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    layer._serving = task
    layer._on_serving_done(task, stub)
    assert counts["dead"] == 1
    counts["dead"] = 0
    layer.stop()
    layer._serving = task
    layer._on_serving_done(task, stub)
    assert counts["dead"] == 0


async def test_a_stopped_layer_does_not_restart_the_rpc_server() -> None:
    link = SlimAgentComm(
        name="acme/lab/desk/a", service=SlimService(identity="acme/lab/desk", url="http://127.0.0.1:9", secret=_SECRET)
    )
    layer = A2ALayer()
    layer.bind(LinkLayer(link))
    layer.serve(lambda _envelope: None)
    layer.start()
    layer.stop()
    layer._astart_server = AsyncMock()  # type: ignore[method-assign]
    await layer._on_app_ready()
    layer._astart_server.assert_not_called()


async def test_astart_server_returns_when_the_layer_is_stopped() -> None:
    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    layer.stop()
    await layer._astart_server(SimpleNamespace())


async def test_astart_server_does_not_start_after_stop_during_drop() -> None:
    """stop() can win while recover holds the lock and has already dropped. Do not start RPC afterwards."""
    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    layer.serve(lambda _envelope: None)
    real_drop = layer._adrop_server

    async def drop() -> None:
        await real_drop()
        layer.stop()

    layer._adrop_server = drop  # type: ignore[method-assign]
    try:
        await layer._astart_server(_rpc_conn("new"))
    except AttributeError:
        raise AssertionError("a stopped layer tried to start RPC") from None
    assert layer._server is None
    assert layer._stopped


def _rpc_link() -> SlimAgentComm:
    return SlimAgentComm(
        name="acme/lab/desk/a", service=SlimService(identity="acme/lab/desk", url="http://127.0.0.1:9", secret=_SECRET)
    )


def _running_stub(*, ensure: Any, mark: Any) -> SimpleNamespace:
    class _Actor:
        running = True

        async def asubmit(self, coro: Any, timeout: float | None = None) -> Any:
            del timeout
            return await coro

        def spawn(self, coro: Any) -> None:
            asyncio.get_running_loop().create_task(coro)

    return SimpleNamespace(
        actor=_Actor(),
        reply_timeout_s=1.0,
        timeout_s=0.1,
        aensure=ensure,
        send_timeout_s=1.4,
        ready=True,
        mark_dead=mark,
    )


@pytest.mark.parametrize("failure", ["node dropped", "remote A2A call failed: connection reset"])
async def test_asend_never_resends_a_request_that_may_have_reached_the_peer(failure: str) -> None:
    counts = {"ensure": 0, "dead": 0}

    async def ensure() -> None:
        counts["ensure"] += 1

    def mark() -> None:
        counts["dead"] += 1

    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    stub = _running_stub(ensure=ensure, mark=mark)
    layer._slim_link = lambda _lower: stub  # type: ignore[method-assign]
    calls: list[int] = []

    async def acall(_link: Any, _target: str, _envelope: Envelope) -> str:
        calls.append(1)
        raise AgentCommError(failure)

    layer._acall = acall  # type: ignore[method-assign]
    with pytest.raises(AgentCommError, match=failure):
        await layer.asend(Envelope(payload="x", meta={"target": "b"}))
    assert calls == [1]  # one SendMessage: a second would run the peer's task twice
    assert counts["dead"] == 0  # one peer's failure does not tear down the link every peer shares


async def test_asend_does_not_retry_a_peer_protocol_error() -> None:
    counts = {"dead": 0}

    async def ensure() -> None:
        return None

    def mark() -> None:
        counts["dead"] += 1

    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    stub = _running_stub(ensure=ensure, mark=mark)
    layer._slim_link = lambda _lower: stub  # type: ignore[method-assign]
    calls = {"n": 0}

    async def acall(_link: Any, _target: str, _envelope: Envelope) -> str:
        from library_ioa.plugins.a2a.client import peer_error
        from slim_bindings import RpcCode, RpcError

        calls["n"] += 1
        raise peer_error(RpcError.Rpc(RpcCode.NOT_FOUND, "TaskNotFoundError: missing", b""))

    layer._acall = acall  # type: ignore[method-assign]
    with pytest.raises(AgentCommError, match="answered an error: NOT_FOUND") as raised:
        await layer.asend(Envelope(payload="x", meta={"target": "b"}))
    assert "TaskNotFound" not in str(raised.value)
    assert calls["n"] == 1
    assert counts["dead"] == 0


async def test_asend_does_not_retry_a_failed_remote_task() -> None:
    counts = {"dead": 0}

    async def ensure() -> None:
        return None

    def mark() -> None:
        counts["dead"] += 1

    layer = A2ALayer()
    layer.bind(LinkLayer(_rpc_link()))
    stub = _running_stub(ensure=ensure, mark=mark)
    layer._slim_link = lambda _lower: stub  # type: ignore[method-assign]
    calls = {"n": 0}

    async def acall(_link: Any, _target: str, _envelope: Envelope) -> str:
        calls["n"] += 1
        raise AgentCommError("remote A2A task failed")

    layer._acall = acall  # type: ignore[method-assign]
    with pytest.raises(AgentCommError, match="remote A2A task failed"):
        await layer.asend(Envelope(payload="x", meta={"target": "b"}))
    assert calls["n"] == 1
    assert counts["dead"] == 0


async def test_inbound_hop_headers_are_ambient_during_the_turn() -> None:
    seen: dict[str, Any] = {}

    async def handler(envelope: Any) -> str:
        seen["headers"] = get_headers()
        seen["envelope"] = dict(envelope.headers)
        seen["inbound"] = inbound_send()
        return "ok"

    layer = A2ALayer()
    layer.serve(handler)
    message = new_text_message("hi")
    message.metadata.update(
        {
            "traceparent": _TRACE,
            "tracestate": "vendor=1",
            "baggage": "k=v",
            "mas.correlation_id": "7",
            "mas.caller_call_id": "c1",
            "authorization": "secret",
        }
    )
    from a2a.auth.user import UnauthenticatedUser

    ctx = SimpleNamespace(message=message, context_id="ctx-1", call_context=SimpleNamespace(user=UnauthenticatedUser()))
    assert await layer._turn("hi", ctx) == "ok"
    assert seen["headers"] == {"traceparent": _TRACE, "tracestate": "vendor=1", "baggage": "k=v"}
    assert seen["envelope"] == seen["headers"]
    assert seen["inbound"] is not None
    assert seen["inbound"].intent == "task"
    assert seen["inbound"].sender == ""  # no verified caller: empty, not a guess
    assert "authorization" not in seen["headers"]


async def test_inbound_sender_is_the_caller_slim_verified_never_metadata() -> None:
    from slima2a.identity import SlimUser

    seen: dict[str, Any] = {}

    async def handler(envelope: Any) -> str:
        seen["sender"] = envelope.meta["sender"]
        seen["bound"] = inbound_send().sender
        return "ok"

    layer = A2ALayer()
    layer.serve(handler)
    message = new_text_message("hi")
    message.metadata.update({"sender": "forged", "mas.sender": "forged"})
    ctx = SimpleNamespace(message=message, context_id="c", call_context=SimpleNamespace(user=SlimUser("acme/lab/peer")))
    assert await layer._turn("hi", ctx) == "ok"
    assert seen == {"sender": "acme/lab/peer", "bound": "acme/lab/peer"}


def test_slimrpc_card_uses_the_manifest_name_and_a_slimrpc_interface() -> None:
    card = slimrpc_agent_card(
        "billing",
        "acme/lab/desk/billing",
        manifest={
            "metadata": {"name": "qa-agent", "version": "1.2.3"},
            "spec": {"description": "Answer questions.", "skills": ["general-knowledge"]},
        },
    )
    assert card.name == "qa-agent"
    assert card.description == "Answer questions."
    assert card.version == "1.2.3"
    assert card.skills[0].id == "general-knowledge"
    assert card.supported_interfaces[0].protocol_binding == "slimrpc"
    assert card.supported_interfaces[0].url == "acme/lab/desk/billing"


def test_slimrpc_card_without_a_manifest_uses_the_agent_name() -> None:
    card = slimrpc_agent_card("billing", "acme/lab/desk/billing", description="bills")
    assert card.name == "billing"
    assert card.description == "bills"
    assert card.skills[0].id == "billing"


# --- on a real node ---------------------------------------------------------------------------------------------


@pytest.fixture()
def desk(slim_node: str):
    """``[slim, a2a-rpc]`` with a recording turn: ``calls`` holds what each turn received."""
    calls: list[tuple[Any, ...]] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        calls.append((agent_id, task, *rest))
        if task == "boom":
            raise RuntimeError("server side detail")
        return f"{agent_id}:{task}"

    slim = {
        "type": "slim",
        "params": {"url": slim_node, "identity": "acme/lab/desk", "secret": _SECRET, "timeout_s": 5.0},
    }
    handle = StackHandle("desk", [slim, {"type": "a2a-rpc"}], run_turn)
    _HANDLES.append(handle)
    try:
        yield handle, calls
    finally:
        handle.close()
        _HANDLES.clear()
        assert _SHARED_LOOP._refs == 0, "a test left a SLIM actor running"


async def test_an_a2a_request_crosses_slimrpc_with_its_ids_and_unicode(desk) -> None:
    handle, calls = desk
    triage = handle.link("triage")
    handle.link("billing")
    reply = await triage.asend("billing", "héllo ✓", correlation_id=7, caller_call_id="call-9", context_id="ctx-1")
    assert reply == "billing:héllo ✓"
    assert calls[-1] == ("billing", "héllo ✓", 7, "call-9", "ctx-1")


async def test_a_send_without_a_context_id_gets_one_on_the_peer(desk) -> None:
    handle, calls = desk
    triage = handle.link("triage")
    handle.link("billing")
    await triage.asend("billing", "x")
    assert calls[-1][-1]


async def test_a_failing_turn_is_a_failed_task_without_the_server_detail(desk) -> None:
    handle, _ = desk
    triage = handle.link("triage")
    handle.link("billing")
    with pytest.raises(AgentCommError, match="remote A2A task failed") as raised:
        await triage.asend("billing", "boom")
    assert "server side detail" not in str(raised.value)
    assert await triage.asend("billing", "after") == "billing:after"


async def test_only_the_task_intent_goes_out(desk) -> None:
    handle, calls = desk
    triage = handle.link("triage")
    handle.link("billing")
    with pytest.raises(AgentCommError, match="not supported"):
        await triage.asend("billing", "x", context=SendContext(intent="inform"))
    assert calls == []


def test_a_slimrpc_not_found_is_a_closed_reason() -> None:
    from library_ioa.plugins.a2a.client import peer_error
    from slim_bindings import RpcCode, RpcError

    error = peer_error(RpcError.Rpc(RpcCode.NOT_FOUND, "TaskNotFoundError: missing", b""))
    assert "TaskNotFound" not in str(error)
    assert str(error) == "remote A2A peer answered an error: NOT_FOUND"
    assert str(peer_error(RpcError.Rpc(RpcCode.UNAVAILABLE, "gone", b""))) == "remote A2A call failed"
    assert str(peer_error(RpcError.Rpc(RpcCode.DEADLINE_EXCEEDED, "slow", b""))) == "remote A2A peer timed out"


def test_a_wrapped_unavailable_rpc_error_is_a_dead_link() -> None:
    from a2a.client.errors import A2AClientError
    from library_ioa.plugins.a2a.client import peer_error
    from slim_bindings import RpcCode, RpcError

    inner = RpcError.Rpc(RpcCode.UNAVAILABLE, "gone", b"")
    try:
        raise A2AClientError("call failed") from inner
    except A2AClientError as exc:
        assert str(peer_error(exc)) == "remote A2A call failed"


def test_an_implicit_rpc_error_context_is_still_a_dead_link() -> None:
    from a2a.client.errors import A2AClientError
    from library_ioa.plugins.a2a.client import peer_error
    from slim_bindings import RpcCode, RpcError

    wrapped = A2AClientError("call failed")
    wrapped.__context__ = RpcError.Rpc(RpcCode.UNAVAILABLE, "gone", b"")
    assert str(peer_error(wrapped)) == "remote A2A call failed"


def test_an_sdk_error_over_slimrpc_is_a_closed_reason() -> None:
    # kills: the SDK's exception text (the peer's own message) carried into the send result
    from a2a.client.errors import A2AClientError
    from library_ioa.plugins.a2a.client import peer_error

    canary = "Bearer sk-canary-rpc"
    error = peer_error(A2AClientError(f"JSON-RPC Error -32099: bad {canary}"))
    assert canary not in str(error)
    assert str(error) == "remote A2A peer answered JSON-RPC error -32099"


def test_a_connect_only_stack_does_not_claim_inbound(slim_node: str) -> None:
    slim = {
        "type": "slim",
        "params": {"url": slim_node, "identity": "acme/lab/desk", "secret": _SECRET, "timeout_s": 5.0},
    }
    handle = StackHandle("desk", [slim, {"type": "a2a-rpc"}])
    _HANDLES.append(handle)
    try:
        comm = handle.link("triage")
        assert comm._layers[0].link._inbound_claimed is False
        assert comm._layers[0].link._listening is True
        assert comm._layers[-1]._handler is None
        assert comm._layers[-1]._server is None
    finally:
        handle.close()
        _HANDLES.clear()
        assert _SHARED_LOOP._refs == 0, "a test left a SLIM actor running"


async def test_inbound_hop_headers_are_ambient_and_continue_on_a_later_send(slim_node: str) -> None:
    hops: list[tuple[str, dict[str, str]]] = []
    box: list[StackHandle] = []

    async def run_turn(agent_id: str, task: str, *_rest: Any) -> str:
        hops.append((agent_id, get_headers()))
        if agent_id == "billing" and task == "go":
            return await box[0].link("billing").asend("outage", "from-billing")
        return f"{agent_id}:{task}"

    slim = {
        "type": "slim",
        "params": {"url": slim_node, "identity": "acme/lab/desk", "secret": _SECRET, "timeout_s": 5.0},
    }
    handle = StackHandle("desk", [slim, {"type": "a2a-rpc"}], run_turn)
    box.append(handle)
    _HANDLES.append(handle)
    try:
        triage = handle.link("triage")
        handle.link("billing")
        handle.link("outage")
        reply = await triage.asend(
            "billing",
            "go",
            headers={"traceparent": _TRACE, "tracestate": "vendor=1", "baggage": "k=v"},
        )
        assert reply == "outage:from-billing"
        billing = next(headers for agent, headers in hops if agent == "billing")
        outage = next(headers for agent, headers in hops if agent == "outage")
        assert billing["baggage"] == "k=v"
        assert billing["tracestate"] == "vendor=1"
        assert billing["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")
        assert billing["traceparent"] != _TRACE
        assert outage["baggage"] == "k=v"
        assert outage["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")
        assert outage["traceparent"] != billing["traceparent"]
    finally:
        handle.close()
        _HANDLES.clear()
        assert _SHARED_LOOP._refs == 0, "a test left a SLIM actor running"


async def test_delegate_to_carries_ambient_hop_headers_over_slimrpc(slim_node: str) -> None:
    hops: list[tuple[str, dict[str, str]]] = []

    async def run_turn(agent_id: str, task: str, *_rest: Any) -> str:
        hops.append((agent_id, get_headers()))
        return f"{agent_id}:{task}"

    slim = {
        "type": "slim",
        "params": {"url": slim_node, "identity": "acme/lab/desk", "secret": _SECRET, "timeout_s": 5.0},
    }
    handle = StackHandle("desk", [slim, {"type": "a2a-rpc"}], run_turn)
    _HANDLES.append(handle)
    try:
        triage = handle.link("triage")
        handle.link("billing")
        tool = SendTool(comm=triage, allowed_peers=["billing"])
        ctx = SimpleNamespace(
            agent_id="triage",
            manifest=None,
            agent_comm=triage,
            agent_comm_routes={"billing": AgentCommRoute("billing", "a2a-rpc", triage)},
            delegation_peer_descriptions=None,
            send_peer_descriptions=None,
        )
        with bound_headers({"traceparent": _TRACE, "baggage": "k=v", "tracestate": "vendor=1"}):
            result = await aexecute_engine_tool(
                "delegate_to_billing", engine_contracts=[tool], ctx=ctx, arguments={"task": "hello"}
            )
        assert result == "billing:hello"
        billing = next(headers for agent, headers in hops if agent == "billing")
        assert billing["baggage"] == "k=v"
        assert billing["tracestate"] == "vendor=1"
        assert billing["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")
        assert billing["traceparent"] != _TRACE
    finally:
        handle.close()
        _HANDLES.clear()
        assert _SHARED_LOOP._refs == 0, "a test left a SLIM actor running"


def test_only_card_keys_of_the_row_reach_the_card_spec() -> None:
    layer = A2ALayer(description="d", version="2", token="secret", url="http://x")
    assert layer._card_spec == {"description": "d", "version": "2"}


async def test_a_server_that_keeps_dying_backs_off_before_reconnecting() -> None:
    waits: list[float] = []
    layer = A2ALayer()
    layer._handler = lambda _e: "x"  # type: ignore[assignment]

    class _Actor:
        running = True

        def spawn(self, coro: Any) -> None:
            coro.close()

    marks: list[int] = []
    link = SimpleNamespace(actor=_Actor(), mark_dead=lambda: marks.append(1))
    layer._arecover = lambda _link, wait=0.0: (waits.append(wait), asyncio.sleep(0))[1]  # type: ignore[method-assign]
    for _ in range(3):
        task = asyncio.get_running_loop().create_future()
        task.set_result(None)
        layer._serving = task  # type: ignore[assignment]
        layer._serving_since = time.monotonic()
        layer._on_serving_done(task, link)  # type: ignore[arg-type]
    assert waits == [0.5, 1.0, 2.0]
    assert len(marks) == 3

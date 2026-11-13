# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Prepared A2A calls use the SDK-selected interface through dispatch."""

from __future__ import annotations

import asyncio
import socket
import time

import httpx
import pytest
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.webserver import UvicornWebServer
from mas.library.standard.plugins.system_tools.send import SendTool
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.boundary.connection import BindingUnavailable, ConnectionTarget


@pytest.fixture
def peer_factory():
    opened = []

    def create():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        received = []
        handle = A2AExposure(
            endpoint={"protocol": "a2a", "usage": "listen", "url": url},
            webserver=UvicornWebServer(),
        ).serve_background(
            {"metadata": {"name": "worker"}, "spec": {"description": "worker"}},
            lambda prompt, **_: received.append(prompt) or {"text": f"worker:{prompt}"},
        )
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise
                time.sleep(0.01)
        comm = A2AAgentComm(url=url, timeout=5)
        opened.append((comm, handle))
        return comm, received, url

    try:
        yield create
    finally:
        for comm, handle in reversed(opened):
            comm.close()
            handle.close()


@pytest.fixture
def peer(peer_factory):
    return peer_factory()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_preparation_discovers_but_does_not_execute_and_preserves_native_send(peer, asynchronous):
    comm, received, url = peer
    operation = comm.prepare_send("worker", "write once", context_id="context-a")
    assert received == []
    assert operation.connection.protocol == "a2a"
    assert operation.connection.transport == "http"
    assert operation.connection.binding_ref == "a2a:worker"
    assert operation.connection.matches_target(ConnectionTarget(endpoint=url))
    assert not operation.connection.matches_target(ConnectionTarget(endpoint=url + "/other"))
    # No card fetch or destination renegotiation is needed during dispatch.
    comm._client.url = "http://127.0.0.1:1"
    if asynchronous:
        result = asyncio.run(operation.ainvoke(correlation_id=7, caller_call_id="parent"))
    else:
        result = operation.invoke(correlation_id=7, caller_call_id="parent")
    assert result == "worker:write once"
    assert received == ["write once"]
    operation.close()


def test_route_replacement_invalidates_pending_call_without_execution(peer):
    comm, received, _ = peer
    tool = SendTool(routes={"worker": AgentCommRoute("worker", "a2a", comm)}, allowed_peers=["worker"])
    pending = tool.prepare_call("delegate_to_worker", {"task": "write once"})
    tool.set_routes({})
    with pytest.raises(BindingUnavailable):
        pending.invoke()
    assert received == []


def test_sdk_selection_records_the_actual_interface():
    from a2a.client import ClientConfig
    from a2a.types import AgentCard, AgentInterface
    from library_ioa.plugins.a2a.client.binding import RecordingClientFactory

    card = AgentCard(
        supported_interfaces=[
            AgentInterface(url="https://worker.test/rpc", protocol_binding="JSONRPC", protocol_version="1.0"),
            AgentInterface(url="https://worker.test/rest", protocol_binding="HTTP+JSON", protocol_version="1.0"),
        ]
    )

    # The SDK's actual choice is REST, despite the other interface and HTTPS.
    async def resolve():
        async with httpx.AsyncClient() as http:
            factory = RecordingClientFactory(
                ClientConfig(
                    httpx_client=http,
                    supported_protocol_bindings=["HTTP+JSON", "JSONRPC"],
                    use_client_preference=True,
                )
            )
            client = factory.create(card)
            try:
                return factory.selection(card, headers={"Authorization": "secret"}, timeout=5)
            finally:
                await client.close()

    selected = asyncio.run(resolve())
    assert selected.transport == "http"
    assert selected.url == "https://worker.test/rest"
    assert [i.url for i in selected.agent_card().supported_interfaces] == [selected.url]
    assert "secret" not in repr(selected)


def test_poll_uses_its_current_http_connection_and_authenticated_recipient():
    from a2a.auth.user import User
    from a2a.server.context import ServerCallContext
    from a2a.types import Task, TaskState, TaskStatus
    from fastapi.testclient import TestClient
    from library_ioa.plugins.a2a.delivery import DeliveryController, DeliveryDecision
    from library_ioa.plugins.a2a.server.app import build_app
    from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
    from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
    from starlette.middleware.authentication import AuthenticationMiddleware

    candidates = []

    class Policy:
        async def authorize(self, candidate):
            candidates.append(candidate)
            return DeliveryDecision.ALLOW

        async def aclose(self):
            pass

    class FixtureAuthentication(AuthenticationBackend):
        async def authenticate(self, request):
            return AuthCredentials(["authenticated"]), SimpleUser(request.headers["fixture-principal"])

    app = build_app(
        {"name": "worker", "url": "http://localhost:9003", "skills": [], "version": "1.0"},
        MasLabAgentExecutor(lambda *_args, **_kwargs: {"text": "reply"}),
        delivery=DeliveryController("worker", Policy()),
    )
    app.add_middleware(AuthenticationMiddleware, backend=FixtureAuthentication())
    with TestClient(app) as client:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {"message": {"messageId": "input", "role": "ROLE_USER", "parts": [{"text": "hello"}]}},
            },
            headers={"A2A-Version": "1.0", "fixture-principal": "caller-a"},
        )
        response.raise_for_status()
        assert candidates
        task_id = "stored-task"

        class Owner(User):
            is_authenticated = True
            user_name = "caller-a"

        # Immediate native replies need not create retrievable tasks. Seed the
        # task store explicitly; retrieval still uses the actual HTTP path.
        client.portal.call(
            app.state.a2a_request_handler.task_store.save,
            Task(id=task_id, context_id="context", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
            ServerCallContext(user=Owner()),
        )
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "GetTask",
                "params": {"id": task_id},
            },
            headers={"A2A-Version": "1.0", "fixture-principal": "caller-b"},
        )
        response.raise_for_status()
        assert "error" in response.json()  # Native task ownership rejects another caller before release.
        assert len(candidates) == 1
        response = client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 3, "method": "GetTask", "params": {"id": task_id}},
            headers={"A2A-Version": "1.0", "fixture-principal": "caller-a"},
        )
        assert "error" not in response.json()
    sent, polled = candidates
    assert sent.connection.transport == polled.connection.transport == "http"
    assert sent.connection.binding_id != polled.connection.binding_id
    assert sent.connection.matches_target(ConnectionTarget(principal="caller-a"))
    assert polled.connection.matches_target(ConnectionTarget(principal="caller-a"))
    assert not polled.connection.matches_target(ConnectionTarget(principal="caller-b"))
    assert polled.kind == "get_task"


@pytest.mark.asyncio
async def test_push_pins_its_own_callback_and_credentials_while_awaiting_policy():
    from a2a.server.tasks import InMemoryPushNotificationConfigStore
    from a2a.types import Task, TaskPushNotificationConfig, TaskState, TaskStatus
    from library_ioa.plugins.a2a.delivery import DeliveryController, DeliveryDecision
    from library_ioa.plugins.a2a.exposure import A2APushNotificationSender

    entered, proceed = asyncio.Event(), asyncio.Event()
    candidates, calls = [], []

    class Policy:
        async def authorize(self, candidate):
            candidates.append(candidate)
            entered.set()
            await proceed.wait()
            return DeliveryDecision.ALLOW

        async def aclose(self):
            pass

    async def receive(request):
        calls.append((str(request.url), request.headers["X-A2A-Notification-Token"]))
        return httpx.Response(200)

    delivery = DeliveryController("worker", Policy())
    sender = A2APushNotificationSender(
        httpx.AsyncClient(transport=httpx.MockTransport(receive)),
        InMemoryPushNotificationConfigStore(),
        delivery=delivery,
    )
    config = TaskPushNotificationConfig(url="https://callback-a.test/result", token="original")
    sending = asyncio.create_task(
        sender._dispatch_notification(
            Task(id="task", context_id="context", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
            config,
            "task",
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        config.url, config.token = "https://callback-b.test/result", "replacement"
        proceed.set()
        assert await sending
        assert calls == [("https://callback-a.test/result", "original")]
        assert candidates[0].connection.matches_target(ConnectionTarget(endpoint=calls[0][0]))
        assert candidates[0].connection.binding_ref == "a2a:push"
        assert candidates[0].connection.transport == "http"
    finally:
        sending.cancel()
        await sender.aclose()
        await delivery.aclose()


def test_unchanged_binding_retains_distinct_pending_messages(peer):
    comm, received, _ = peer
    first = comm.prepare_send("worker", "first")
    second = comm.prepare_send("worker", "second")
    assert first.connection.binding_id == second.connection.binding_id
    assert first.invoke() == "worker:first"
    assert second.invoke() == "worker:second"
    assert received == ["first", "second"]


def test_sdk_endpoint_change_with_same_http_transport_invalidates_pending_call(peer_factory):
    from library_ioa.plugins.a2a.client import A2AClient

    first_comm, first_received, first_url = peer_factory()
    _, second_received, second_url = peer_factory()
    client = A2AClient(url=first_url, timeout=5)
    try:
        pending = client.prepare_message("worker", "old write")
        client.url = second_url
        replacement = client.prepare_message("worker", "new write")
        assert replacement.connection.transport == pending.connection.transport == "http"
        with pytest.raises(BindingUnavailable):
            pending.invoke()
        response = asyncio.run(replacement.ainvoke())
        assert "new write" in str(response)
        assert first_received == []
        assert second_received == ["new write"]
    finally:
        client.close()
    with pytest.raises(BindingUnavailable, match="closed"):
        client.prepare_message("worker", "after close")


def test_direct_prepared_client_send_preserves_native_result(peer):
    comm, received, _ = peer
    operation = comm._client.prepare_message("worker", "direct")
    assert "worker:direct" in str(operation.invoke(correlation_id=2, caller_call_id="parent"))
    assert received == ["direct"]


def test_unresolved_or_unknown_sdk_interface_fails_clearly():
    from a2a.client import ClientConfig
    from a2a.types import AgentCard
    from library_ioa.plugins.a2a.client.binding import RecordingClientFactory, SelectedInterface

    async def unresolved():
        async with httpx.AsyncClient() as http:
            factory = RecordingClientFactory(ClientConfig(httpx_client=http))
            with pytest.raises(BindingUnavailable, match="did not resolve"):
                factory.selection(AgentCard(), headers={}, timeout=5)

    asyncio.run(unresolved())
    assert SelectedInterface(b"", "GRPC", "grpc.test:9000", (), 5).transport == "grpc"
    with pytest.raises(BindingUnavailable, match="cannot be described"):
        _ = SelectedInterface(b"", "OTHER", "endpoint", (), 5).transport


@pytest.mark.parametrize("asynchronous", [False, True])
def test_prepared_delegation_preserves_native_error_text(peer, monkeypatch, asynchronous):
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    comm, received, _ = peer
    tool = SendTool(routes={"worker": AgentCommRoute("worker", "a2a", comm)}, allowed_peers=["worker"])
    pending = tool.prepare_call("delegate_to_worker", {"task": "fails"})

    def failed(*args, **kwargs):
        raise AgentCommError("remote failure")

    monkeypatch.setattr(comm._client, "send_message", failed)
    expected = tool.send("worker", "fails")
    actual = asyncio.run(pending.ainvoke()) if asynchronous else pending.invoke()
    assert actual == expected == "[send] agent 'worker' via a2a failed: remote failure"
    assert received == []


def test_replacing_communication_client_cannot_retarget_a_captured_operation(peer_factory):
    from library_ioa.plugins.a2a.client import A2AClient

    comm, original_received, _ = peer_factory()
    _, replacement_received, replacement_url = peer_factory()
    pending = comm.prepare_send("worker", "original destination")
    original = comm._client
    comm._client = A2AClient(url=replacement_url)
    try:
        assert pending.invoke() == "worker:original destination"
        assert original_received == ["original destination"]
        assert replacement_received == []
    finally:
        original.close()

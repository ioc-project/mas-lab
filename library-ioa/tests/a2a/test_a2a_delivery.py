# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Delivery policy checks use SDK handlers and real HTTP serialization, without models."""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    Message,
    Part,
    SendMessageRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidAgentResponseError, UnsupportedOperationError
from fastapi.testclient import TestClient
from library_ioa.plugins.a2a.delivery import DeliveryCandidate, DeliveryController, DeliveryDecision
from library_ioa.plugins.a2a.exposure import A2AExposure, A2APushNotificationSender
from library_ioa.plugins.a2a.server.app import build_app
from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
from library_ioa.plugins.webserver import UvicornWebServer


class Policy:
    def __init__(self, decision=DeliveryDecision.ALLOW):
        self.decision = decision
        self.seen: list[DeliveryCandidate] = []
        self.closed = 0

    async def authorize(self, candidate):
        self.seen.append(candidate)
        return self.decision

    async def aclose(self):
        self.closed += 1


def task(name="task"):
    return Task(
        id=name,
        context_id="context",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        history=[Message(role=2, message_id="reply", parts=[Part(text="private reply")])],
    )


def app_for(policy=None):
    delivery = DeliveryController("assistant", policy) if policy else None
    return build_app(
        {
            "name": "assistant",
            "description": "Test",
            "url": "http://localhost:9003",
            "version": "1.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
            "capabilities": {"streaming": True},
        },
        MasLabAgentExecutor(lambda *args, **kwargs: {"text": "private reply"}),
        delivery=delivery,
    )


def send(client):
    return client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": "input",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                    "metadata": {"mas.caller_call_id": "parent"},
                },
            },
        },
        headers={"A2A-Version": "1.0"},
    )


@pytest.mark.parametrize("decision", [DeliveryDecision.ALLOW, DeliveryDecision.DENY])
def test_http_send_is_gated_and_streaming_advertisement_disabled(decision):
    policy = Policy(decision)
    app = app_for(policy)
    with TestClient(app) as client:
        card = client.get("/.well-known/agent-card.json").json()
        assert not card["capabilities"].get("streaming", False)
        response = send(client)
        assert ("private reply" in response.text) == (decision is DeliveryDecision.ALLOW)
        if decision is DeliveryDecision.DENY:
            assert "error" in response.json()
    assert policy.closed == 1
    assert len(policy.seen) == 1
    assert policy.seen[0].task_id
    assert policy.seen[0].context_id
    assert json.loads(policy.seen[0].request_metadata)["message"]["mas.caller_call_id"] == "parent"


def test_no_policy_retains_native_response_and_capabilities():
    with TestClient(app_for()) as client:
        assert client.get("/.well-known/agent-card.json").json()["capabilities"]["streaming"]
        assert "private reply" in send(client).text


@pytest.mark.asyncio
async def test_poll_and_list_cannot_bypass_denial():
    policy = Policy(DeliveryDecision.DENY)
    app = app_for(policy)
    handler = app.state.a2a_request_handler
    context = ServerCallContext()
    await handler.task_store.save(task(), context)
    for method, params in [
        (handler.on_get_task, GetTaskRequest(id="task")),
        (handler.on_list_tasks, ListTasksRequest(include_artifacts=True)),
    ]:
        with pytest.raises(InvalidAgentResponseError):
            await method(params, context)
    assert [c.kind for c in policy.seen] == ["get_task", "list_tasks"]
    assert all(b"private reply" in c.payload for c in policy.seen)
    await handler.aclose()


@pytest.mark.asyncio
async def test_cancel_result_is_gated_after_native_cancellation(monkeypatch):
    native = AsyncMock(return_value=task())
    monkeypatch.setattr(DefaultRequestHandler, "on_cancel_task", native)
    handler = app_for(Policy(DeliveryDecision.DENY)).state.a2a_request_handler
    with pytest.raises(InvalidAgentResponseError):
        await handler.on_cancel_task(CancelTaskRequest(id="task"), ServerCallContext())
    native.assert_awaited_once()
    await handler.aclose()


@pytest.mark.asyncio
async def test_stream_and_subscribe_rejected_before_execution():
    policy = Policy()
    handler = app_for(policy).state.a2a_request_handler
    for generator in [
        handler.on_message_send_stream(SendMessageRequest(), ServerCallContext()),
        handler.on_subscribe_to_task(GetTaskRequest(id="task"), ServerCallContext()),
    ]:
        with pytest.raises(UnsupportedOperationError):
            await anext(generator)
    assert not policy.seen
    await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [DeliveryDecision.ALLOW, DeliveryDecision.DENY])
async def test_push_authorized_before_http_transport(decision):
    calls = []

    async def receive(request):
        calls.append(request)
        return httpx.Response(200)

    policy = Policy(decision)
    delivery = DeliveryController("assistant", policy)
    sender = A2APushNotificationSender(
        httpx.AsyncClient(transport=httpx.MockTransport(receive)),
        InMemoryPushNotificationConfigStore(),
        delivery=delivery,
    )
    allowed = await sender._dispatch_notification(
        task(),
        TaskPushNotificationConfig(url="https://callback.example/result", token="callback-secret"),
        "task",
    )
    assert allowed == (decision is DeliveryDecision.ALLOW)
    assert len(calls) == int(allowed)
    assert policy.seen[0].kind == "push"
    assert policy.seen[0].destination == "https://callback.example/result"
    assert b"callback-secret" not in policy.seen[0].payload
    if allowed:
        assert calls[0].headers["X-A2A-Notification-Token"] == "callback-secret"
    await sender.aclose()
    await delivery.aclose()


@pytest.mark.asyncio
async def test_hold_is_async_and_releases_frozen_snapshot():
    entered, proceed = asyncio.Event(), asyncio.Event()

    class Holding(Policy):
        async def authorize(self, candidate):
            self.seen.append(candidate)
            entered.set()
            await proceed.wait()
            return DeliveryDecision.ALLOW

    policy = Holding()
    delivery = DeliveryController("agent", policy)
    original = task()
    pending = asyncio.create_task(delivery.release(original, kind="send"))
    await entered.wait()
    assert not pending.done()
    original.history[0].parts[0].text = "changed after authorization started"
    with pytest.raises(FrozenInstanceError):
        policy.seen[0].task_id = "other"
    proceed.set()
    released = await pending
    assert released.history[0].parts[0].text == "private reply"
    await delivery.aclose()


@pytest.mark.asyncio
async def test_concurrent_candidates_and_authenticated_recipients_are_distinct():
    class Identity(User):
        is_authenticated = True

        def __init__(self, name):
            self.name = name

        @property
        def user_name(self):
            return self.name

    policy = Policy()
    delivery = DeliveryController("agent", policy)
    await asyncio.gather(
        *(
            delivery.release(task(str(i)), kind="get_task", context=ServerCallContext(user=Identity(str(i))))
            for i in range(4)
        )
    )
    assert {(c.task_id, c.recipient_id) for c in policy.seen} == {(str(i), str(i)) for i in range(4)}
    await delivery.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid", "exception", "protocol_error", "timeout", "cancel", "close"])
async def test_failures_never_release_payload_and_cleanup(failure):
    started = asyncio.Event()

    class Failing(Policy):
        async def authorize(self, candidate):
            started.set()
            if failure == "invalid":
                return "allow"
            if failure == "exception":
                raise RuntimeError("private reply")
            if failure == "protocol_error":
                raise InvalidAgentResponseError(message="private reply")
            await asyncio.Event().wait()

    policy = Failing()
    delivery = DeliveryController("agent", policy, timeout=0.01 if failure == "timeout" else 30)
    pending = asyncio.create_task(delivery.release(task(), kind="send"))
    await started.wait()
    if failure == "cancel":
        pending.cancel()
    if failure == "close":
        await delivery.aclose()
    with pytest.raises((InvalidAgentResponseError, asyncio.CancelledError)) as error:
        await pending
    assert "private reply" not in str(error.value)
    await delivery.aclose()
    assert not delivery._pending
    assert policy.closed == 1
    with pytest.raises(InvalidAgentResponseError):
        await delivery.release(task(), kind="send")


@pytest.mark.asyncio
async def test_failure_recovery():
    policy = Policy(DeliveryDecision.DENY)
    delivery = DeliveryController("agent", policy)
    with pytest.raises(InvalidAgentResponseError):
        await delivery.release(task(), kind="send")
    policy.decision = DeliveryDecision.ALLOW
    await delivery.release(task("another"), kind="send")
    await delivery.release(task("third"), kind="send")
    await delivery.aclose()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_deadline(timeout):
    with pytest.raises(ValueError):
        DeliveryController("agent", Policy(), timeout=timeout)


def test_endpoint_resolves_policy_through_native_registry(monkeypatch):
    import mas.runtime.registry

    registry = mas.runtime.registry.PluginRegistry()
    monkeypatch.setattr(mas.runtime.registry, "get_registry", lambda: registry)
    mas.runtime.registry.register_plugin(
        "test.a2a_delivery_policy.test_delivery",
        Policy,
        shortcuts=["test_delivery"],
        attributes={"plugin_type": "a2a_delivery_policy"},
    )
    exposure = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "listen",
            "url": "http://localhost:9100",
            "a2a": {"delivery_policy": {"type": "test_delivery"}, "delivery_timeout": 12},
        },
        webserver=UvicornWebServer(),
    )
    app = exposure.build_app({"metadata": {"name": "agent"}}, lambda *a, **kw: {"text": "ok"})
    with TestClient(app):
        policy = app.state.a2a_delivery.policy
        assert isinstance(policy, Policy)
        assert app.state.a2a_delivery.timeout == 12
    assert policy.closed == 1


def test_invalid_policy_rejected_before_serving():
    with pytest.raises(TypeError, match="authorize and aclose"):
        DeliveryController("agent", object())


def test_conflicting_configuration_rejected():
    with pytest.raises(ValueError, match="either directly"):
        A2AExposure(
            endpoint={
                "protocol": "a2a",
                "usage": "listen",
                "url": "http://localhost:9100",
                "a2a": {"delivery_policy": "missing"},
            },
            delivery_policy=Policy(),
        )


@pytest.mark.asyncio
async def test_release_outcomes_withhold_denied_content_and_isolate_observer_errors():
    policy = Policy(DeliveryDecision.DENY)
    outcomes = []
    policy.on_release = outcomes.append
    delivery = DeliveryController("agent", policy)
    with pytest.raises(InvalidAgentResponseError):
        await delivery.release(task(), kind="send")
    assert outcomes[-1].status == "denied" and outcomes[-1].payload is None
    policy.decision = DeliveryDecision.ALLOW
    await delivery.release(task(), kind="send")
    assert outcomes[-1].status == "allowed" and outcomes[-1].payload

    def broken(outcome):
        raise RuntimeError("observer failed")

    policy.on_release = broken
    assert (await delivery.release(task(), kind="send")).id == "task"
    await delivery.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["send", "get_task", "push"])
@pytest.mark.parametrize("stop", ["timeout", "cancel", "close"])
async def test_late_allow_cannot_release_or_notify_approval(kind, stop):
    entered, cancelled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    outcomes = []

    class Suppressing(Policy):
        async def authorize(self, candidate):
            entered.set()
            while not finish.is_set():
                try:
                    await finish.wait()
                except asyncio.CancelledError:
                    cancelled.set()
            return DeliveryDecision.ALLOW

        on_release = staticmethod(outcomes.append)

    policy = Suppressing()
    controller = DeliveryController("worker", policy, timeout=0.02)
    release = asyncio.create_task(controller.release(task(), kind=kind))
    await entered.wait()
    if stop == "cancel":
        release.cancel()
    elif stop == "close":
        await asyncio.wait_for(controller.aclose(), timeout=1)
    with pytest.raises(asyncio.CancelledError if stop == "cancel" else InvalidAgentResponseError):
        await asyncio.wait_for(release, timeout=1)
    await cancelled.wait()
    assert len(outcomes) == 1
    assert outcomes[0].status != "allowed"
    assert outcomes[0].payload is None
    pending = tuple(controller._pending)
    finish.set()
    await asyncio.gather(*pending)
    assert len(outcomes) == 1
    await controller.aclose()


@pytest.mark.asyncio
async def test_cancellation_suppressing_cleanup_is_bounded():
    entered, finish = asyncio.Event(), asyncio.Event()

    class SlowClose(Policy):
        async def aclose(self):
            entered.set()
            while not finish.is_set():
                try:
                    await finish.wait()
                except asyncio.CancelledError:
                    pass

    controller = DeliveryController("worker", SlowClose(), timeout=0.02)
    close = asyncio.create_task(controller.aclose())
    await entered.wait()
    await asyncio.wait_for(close, timeout=1)
    finish.set()

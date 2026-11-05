from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from library_ioa.plugins.a2a import cli as a2a_cli
from library_ioa.plugins.a2a.agentcard import agent_card_from_manifest
from library_ioa.plugins.a2a.client import A2AClient
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.a2a.server import start_grpc_server
from library_ioa.plugins.a2a.server.app import (
    _ClosingActiveTaskRegistry,
    _ignore_unknown_proto_fields,
    build_app,
)
from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor


def test_a2a_sdk_telemetry_is_disabled_by_default() -> None:
    for configured, expected in ((None, "false"), ("true", "true")):
        env = dict(os.environ)
        if configured is None:
            env.pop("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", None)
        else:
            env["OTEL_INSTRUMENTATION_A2A_SDK_ENABLED"] = configured
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; import library_ioa.plugins.a2a; import a2a.utils.telemetry; "
                "print(os.environ['OTEL_INSTRUMENTATION_A2A_SDK_ENABLED']); "
                "print(getattr(a2a.utils.telemetry, 'otel_enabled', 'missing'))",
            ],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )

        env_value, enabled_value = result.stdout.strip().splitlines()
        assert env_value == expected
        assert enabled_value == ("True" if expected == "true" else "False")


def test_importing_a2a_plugin_package_does_not_import_a2a_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import library_ioa.plugins.a2a; print('loaded' if 'a2a' in sys.modules else 'lazy')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "lazy"


def test_a2a_sdk_defaults_to_enabled_without_the_mas_pin() -> None:
    """Recent a2a-sdk enables tracing whenever OpenTelemetry is importable."""
    env = dict(os.environ)
    env.pop("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import a2a.utils.telemetry; "
            "print(a2a.utils.telemetry.otel_installed); "
            "print(a2a.utils.telemetry.otel_enabled)",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    installed, enabled = result.stdout.strip().splitlines()
    if installed != "True":
        pytest.skip("opentelemetry is not installed")
    assert enabled == "True"


def test_importing_library_ioa_pins_a2a_telemetry_before_sdk_import() -> None:
    env = dict(os.environ)
    env.pop("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import library_ioa; import a2a.utils.telemetry; print(a2a.utils.telemetry.otel_enabled)",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.stdout.strip() == "False"


def test_sdk_backed_a2a_contract_is_available() -> None:
    assert hasattr(A2AClient, "get_agent_card")
    assert hasattr(A2AClient, "send_message")
    assert hasattr(MasLabAgentExecutor, "execute")

    app = build_app(
        {
            "name": "demo-agent",
            "description": "Demo agent",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"echo:{prompt}"}),
    )

    assert app is not None
    with TestClient(app) as client:
        response = client.get("/.well-known/agent-card.json")

        assert response.status_code == 200
        assert "max-age=" in response.headers["cache-control"]
        assert response.headers["etag"].startswith(('"', 'W/"'))
        assert response.headers["last-modified"]


def test_mas_a2a_cli_sends_one_message_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakeA2AClient:
        def __init__(self, *, url: str, transport: str) -> None:
            calls.append(("init", url, transport))

        def send_message(self, message: str) -> dict[str, str]:
            calls.append(("send", message, ""))
            return {"status": "completed", "result": "hello back"}

        def close(self) -> None:
            calls.append(("close", "", ""))

    monkeypatch.setattr(a2a_cli, "A2AClient", FakeA2AClient)

    result = a2a_cli.main(
        [
            "--a2a",
            "http://127.0.0.1:41242",
            "--a2a-transport",
            "JSONRPC",
            "--message",
            "hello",
        ]
    )

    assert result == 0
    assert calls == [
        ("init", "http://127.0.0.1:41242", "JSONRPC"),
        ("send", "hello", ""),
        ("close", "", ""),
    ]
    assert '"result": "hello back"' in capsys.readouterr().out


def test_a2a_routes_ignore_unknown_request_fields() -> None:
    app = build_app(
        {
            "name": "demo-agent",
            "description": "Demo agent",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"echo:{prompt}"}),
    )
    message = {
        "role": "ROLE_USER",
        "parts": [{"text": "hello"}],
        "messageId": "message-1",
        "tckUnknownField": "ignored",
    }

    with TestClient(app) as client:
        jsonrpc = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {"message": message, "tckExtraParam": 42},
            },
            headers={"A2A-Version": "1.0"},
        )
        rest = client.post(
            "/a2a/rest/message:send",
            json={"message": message, "tckExtraParam": 42},
            headers={"A2A-Version": "1.0"},
        )

        assert jsonrpc.status_code == 200
        assert rest.status_code == 200
        assert rest.headers["content-type"].split(";", 1)[0] == "application/a2a+json"
        typed = client.post(
            "/a2a/rest/message:send",
            content='{"message":{"role":"ROLE_USER","parts":[{"text":"hello"}],"messageId":"message-2"}}',
            headers={"Content-Type": "application/a2a+json", "A2A-Version": "1.0"},
        )
        assert typed.status_code == 200
        assert typed.headers["content-type"].split(";", 1)[0] == "application/a2a+json"


def test_proto_field_compatibility_does_not_read_legacy_label() -> None:
    class CurrentField:
        name = "known"
        json_name = "known"
        message_type = None
        is_repeated = False

        @property
        def label(self) -> int:
            raise AssertionError("legacy label must not be read")

    class Descriptor:
        fields = [CurrentField()]

    assert _ignore_unknown_proto_fields(
        {"known": "value", "unknown": "discarded"},
        Descriptor(),
    ) == {"known": "value"}


def test_executor_returns_input_required_task_state() -> None:
    app = build_app(
        {
            "name": "approval-agent",
            "description": "Agent awaiting operator approval",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(
            lambda prompt, **_: {
                "text": "Approval required",
                "task_state": "input_required",
            }
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": "approve this action"}],
                        "messageId": "approval-1",
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

    assert response.status_code == 200
    assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"


def test_executor_marks_task_failed_when_handler_raises() -> None:
    def fail_handler(prompt: str, **_: Any) -> dict[str, str]:
        raise RuntimeError("backend failed")

    app = build_app(
        {
            "name": "failing-agent",
            "description": "Agent used to verify failed task handling",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(fail_handler),
    )

    with TestClient(app) as client:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": "run"}],
                        "messageId": "failure-1",
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

    assert response.status_code == 200
    assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_FAILED"


def test_executor_cancellation_signals_cooperative_handler() -> None:
    started = threading.Event()
    finished = threading.Event()
    observed_cancel = threading.Event()

    def handler(prompt: str, *, cancel_event: threading.Event, **_: Any) -> dict[str, str]:
        started.set()
        while not cancel_event.wait(0.01):
            pass
        observed_cancel.set()
        finished.set()
        return {"text": "cancelled"}

    executor = MasLabAgentExecutor(handler)

    class EventQueue:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def enqueue_event(self, event: Any) -> None:
            self.events.append(event)

    context = type(
        "Context",
        (),
        {
            "task_id": "cancel-me",
            "context_id": "context-1",
            "current_task": None,
            "get_user_input": lambda self: "work",
        },
    )()
    event_queue = EventQueue()

    async def run() -> None:
        execution = asyncio.create_task(executor.execute(context, event_queue))
        await asyncio.to_thread(started.wait, 1)
        await executor.cancel(context, event_queue)
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(run())

    assert observed_cancel.wait(1)
    assert finished.is_set()
    assert event_queue.events


def test_a2a_exposure_maps_protocol_identity_to_runtime_ids() -> None:
    calls: list[tuple[str, str | None, str | None, dict[str, Any]]] = []

    def runtime_handler(
        prompt: str, *, turn_id: str | None = None, session_id: str | None = None, **kwargs: Any
    ) -> dict[str, str]:
        calls.append((prompt, turn_id, session_id, kwargs))
        return {"text": "runtime response"}

    exposed_app = A2AExposure().build_app(
        {
            "metadata": {"name": "exposed-agent"},
            "spec": {"description": "A2A identity mapping test"},
        },
        runtime_handler,
    )
    with TestClient(exposed_app) as client:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                        "messageId": "message-1",
                        "contextId": "context-1",
                        "metadata": {
                            "mas.correlation_id": "42",
                            "mas.caller_call_id": "tool-call-1",
                        },
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

    assert response.status_code == 200
    assert calls == [
        (
            "hello",
            "message-1",
            "context-1",
            {"parent_call_id": "tool-call-1", "upstream_correlation_id": 42},
        )
    ]


def test_a2a_omitted_context_id_mints_a_session_and_returns_it() -> None:
    seen: list[str] = []

    def runtime_handler(
        prompt: str, *, turn_id: str | None = None, session_id: str | None = None, **kwargs: Any
    ) -> dict[str, str]:
        seen.append(str(session_id or ""))
        return {"text": "ok"}

    exposed_app = A2AExposure().build_app(
        {
            "metadata": {"name": "exposed-agent"},
            "spec": {"description": "A2A session mint"},
        },
        runtime_handler,
    )
    with TestClient(exposed_app) as client:
        first = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                        "messageId": "message-1",
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )
        second = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": "again"}],
                        "messageId": "message-2",
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert seen[0] and seen[1]
    assert seen[0] != seen[1]


def test_active_task_cleanup_closes_event_queues() -> None:
    class ActiveTaskStub:
        task_id = "failed-start"

        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    task = ActiveTaskStub()
    registry = _ClosingActiveTaskRegistry(
        agent_executor=object(),
        task_store=object(),
    )
    registry._active_tasks[task.task_id] = task

    async def cleanup() -> None:
        registry._on_active_task_cleanup(task)
        await asyncio.gather(*tuple(registry._cleanup_tasks))

    asyncio.run(cleanup())

    assert task.closed
    assert task.task_id not in registry._active_tasks


def test_nonexistent_task_request_returns_protocol_error_and_drains_cleanup() -> None:
    app = build_app(
        {
            "name": "demo-agent",
            "description": "Demo agent",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"echo:{prompt}"}),
    )

    with TestClient(app) as client:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "GetTask",
                "params": {"id": "missing-task"},
            },
            headers={"A2A-Version": "1.0"},
        )

        assert response.status_code == 200
        assert "error" in response.json()


def test_get_missing_task_returns_jsonrpc_error_and_drains_cleanup() -> None:
    app = build_app(
        {
            "name": "demo-agent",
            "description": "Demo agent",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"echo:{prompt}"}),
    )

    with TestClient(app) as client:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "GetTask",
                "params": {"id": "missing-task"},
            },
            headers={"A2A-Version": "1.0"},
        )

        assert response.status_code == 200
        assert "error" in response.json()


def test_a2a_routes_reject_unsupported_content_type() -> None:
    app = build_app(
        {
            "name": "demo-agent",
            "description": "Demo agent",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"echo:{prompt}"}),
    )

    with TestClient(app) as client:
        jsonrpc = client.post(
            "/",
            content='{"jsonrpc":"2.0","id":4,"method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"hello"}],"messageId":"message-1"}}}',
            headers={"Content-Type": "text/plain", "A2A-Version": "1.0"},
        )
        rest = client.post(
            "/a2a/rest/message:send",
            content='{"message":{"role":"ROLE_USER","parts":[{"text":"hello"}],"messageId":"message-1"}}',
            headers={"Content-Type": "text/plain", "A2A-Version": "1.0"},
        )

        assert jsonrpc.status_code == 200
        assert jsonrpc.json()["error"]["code"] == -32005
        assert rest.status_code == 400
        assert rest.headers["content-type"].split(";", 1)[0] == "application/a2a+json"
        assert rest.json()["error"]["code"] == 400
        assert rest.json()["error"]["status"] == "INVALID_ARGUMENT"
        assert rest.json()["error"]["details"][0]["reason"] == "CONTENT_TYPE_NOT_SUPPORTED"
        unsupported_part = client.post(
            "/a2a/rest/message:send",
            json={
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"raw": "dGNr", "mediaType": "application/x-unsupported-tck-type"}],
                    "messageId": "message-unsupported",
                }
            },
            headers={"A2A-Version": "1.0"},
        )
        assert unsupported_part.status_code == 400
        assert unsupported_part.json()["error"]["details"][0]["reason"] == "CONTENT_TYPE_NOT_SUPPORTED"


@pytest.mark.parametrize("transport", [None, "JSONRPC", "HTTP+JSON", "GRPC"])
def test_exposed_agent_can_delegate_outbound_over_a2a(transport: str | None) -> None:
    if transport == "GRPC":
        pytest.importorskip("grpc")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        peer_port = listener.getsockname()[1]
    grpc_port = None
    if transport == "GRPC":
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            grpc_port = listener.getsockname()[1]
    peer_url = f"http://127.0.0.1:{peer_port}"
    peer_manifest = {
        "name": "remote-peer",
        "description": "Remote A2A peer",
        "url": peer_url,
        "version": "1.0.0",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [],
    }
    peer_card = (
        agent_card_from_manifest(
            {
                "metadata": {"name": "remote-peer", "version": "1.0.0"},
                "spec": {"description": "Remote A2A peer"},
            },
            url=peer_url,
            grpc_url=f"127.0.0.1:{grpc_port}",
        )
        if grpc_port is not None
        else peer_manifest
    )
    peer_app = build_app(
        peer_card,
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"peer:{prompt}"}),
    )
    peer_started = threading.Event()

    class ReadyServer(uvicorn.Server):
        grpc_server: Any = None

        async def startup(self, sockets: Any = None) -> None:
            await super().startup(sockets=sockets)
            if grpc_port is not None:
                self.grpc_server = await start_grpc_server(
                    peer_app.state.a2a_request_handler,
                    host="127.0.0.1",
                    port=grpc_port,
                )
            peer_started.set()

        async def shutdown(self, sockets: Any = None) -> None:
            if self.grpc_server is not None:
                await self.grpc_server.stop(grace=0)
            await super().shutdown(sockets=sockets)

    peer_server = ReadyServer(
        uvicorn.Config(
            peer_app,
            host="127.0.0.1",
            port=peer_port,
            log_level="critical",
            ws="none",
        )
    )
    peer_thread = threading.Thread(target=peer_server.run, daemon=True)
    peer_thread.start()

    try:
        assert peer_started.wait(timeout=10)

        def delegate(prompt: str, **_: Any) -> dict[str, str]:
            client = A2AClient(url=peer_url, transport=transport)
            try:
                result = client.send_message(prompt)
            finally:
                client.close()
            return {"text": str(result.get("result") or "")}

        exposed_app = A2AExposure().build_app(
            {
                "metadata": {"name": "exposed-moderator"},
                "spec": {"description": "Exposed agent that calls an A2A peer"},
            },
            delegate,
        )
        with TestClient(exposed_app) as client:
            response = client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                            "messageId": "outer-message",
                        }
                    },
                },
                headers={"A2A-Version": "1.0"},
            )

        assert response.status_code == 200
        assert "peer:hello" in response.text
    finally:
        peer_server.should_exit = True
        peer_thread.join(timeout=10)
        assert not peer_thread.is_alive()


def test_grpc_maps_an_unsupported_version_to_failed_precondition() -> None:
    pytest.importorskip("grpc")
    import grpc
    from a2a.types import a2a_pb2
    from a2a.types.a2a_pb2_grpc import A2AServiceStub
    from a2a.utils.constants import VERSION_HEADER

    app = build_app(
        {
            "name": "demo-agent",
            "description": "Demo agent",
            "url": "http://127.0.0.1:9003",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        MasLabAgentExecutor(lambda prompt, **_: {"text": f"echo:{prompt}"}),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    async def _run() -> None:
        server = await start_grpc_server(app.state.a2a_request_handler, host="127.0.0.1", port=port)
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = A2AServiceStub(channel)
                with pytest.raises(grpc.aio.AioRpcError) as version_error:
                    await stub.GetTask(
                        a2a_pb2.GetTaskRequest(id="missing"),
                        metadata=((VERSION_HEADER.lower(), "0.1"),),
                    )
                assert version_error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
        finally:
            await server.stop(grace=0)

    asyncio.run(_run())

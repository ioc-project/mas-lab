# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import ssl
import uuid
from types import SimpleNamespace

import httpx
import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Task, TaskState, TaskStatus
from library_ioa.plugins.a2a.delivery import DeliveryController
from mas.library.enforcement_proxy.bridge import AuthorizationUnavailable, Bridge, canonical
from mas.library.enforcement_proxy.plugins import ReleasePolicy, RequestPolicy
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.manifest_tool_provider import ManifestToolProvider
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.ingress import EngineIoReturn


class _Identity(User):
    is_authenticated = True
    user_name = "worker"


@pytest.fixture
def config(tmp_path, monkeypatch):
    tls = SimpleNamespace(load_cert_chain=lambda *a: None)
    monkeypatch.setattr(ssl, "create_default_context", lambda **kw: tls)
    data = {
        "mas_id": "mas",
        "adapter_url": "https://adapter.test",
        "otlp_url": "https://collector.test/v1/traces",
        "ca_file": "ca.pem",
        "timeout": 1,
        "identities": {
            "planner": {
                "id": "id-planner",
                "certificate_file": "cert.pem",
                "key_file": "key.pem",
                "tools": {"write": {"destination": "worker", "kind": "tool", "tool": "write", "transport": "http"}},
            },
            "worker": {"id": "id-worker"},
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return str(path)


def wire(monkeypatch, *, allow=True, missing=False, malformed=False, wait=None):
    calls = []

    async def transport(request):
        body = json.loads(request.content)
        calls.append((request.url.host, body))
        if request.url.host == "collector.test":
            if missing:
                return httpx.Response(200, json={"partialSuccess": {"rejectedSpans": "1"}})
            return httpx.Response(200, json={})
        if wait:
            await wait.wait()
        return httpx.Response(
            200,
            json={
                "evaluation_id": "wrong" if malformed else body["evaluation_id"],
                "decision": "allow" if allow else "deny",
                "authorization_record_id": str(uuid.uuid4()),
            },
        )

    monkeypatch.setattr(Bridge, "client", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_publish_before_reference_only_authorization(config, monkeypatch, allowed):
    transport = "http"
    calls = wire(monkeypatch, allow=allowed)
    result = await Bridge(config, "planner").authorize(
        destination="worker",
        kind="tool",
        transport=transport,
        tool="write",
        payload=canonical({"arguments": {"value": "candidate-text"}}),
        context={"session_id": "session"},
    )
    assert result.allowed is allowed
    assert [host for host, body in calls] == ["collector.test", "adapter.test", "collector.test"]
    assert "candidate-text" in json.dumps(calls[0][1])
    assert "candidate-text" not in json.dumps(calls[1][1])
    span = calls[0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert (calls[1][1]["trace_id"], calls[1][1]["span_id"]) == (span["traceId"], span["spanId"])
    assert result.recipient == "worker"
    assert calls[1][1]["transport"] == transport
    attrs = {item["key"]: item["value"]["stringValue"] for item in span["attributes"]}
    assert json.loads(attrs["ioc.activity.body"])["transport"] == transport


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "malformed", "cancel", "timeout", "closed"])
async def test_no_release_on_failure(config, monkeypatch, mode):
    wait = asyncio.Event() if mode in {"cancel", "timeout"} else None
    calls = wire(monkeypatch, missing=mode == "missing", malformed=mode == "malformed", wait=wait)
    bridge = Bridge(config, "planner")
    bridge.timeout = 0.01 if mode == "timeout" else 1
    if mode == "closed":
        bridge.close()
    pending = asyncio.create_task(
        bridge.authorize(destination="worker", kind="delegation", transport="http", payload=b"{}", context={})
    )
    if mode == "cancel":
        await asyncio.sleep(0)
        pending.cancel()
    with pytest.raises((AuthorizationUnavailable, asyncio.CancelledError)):
        await pending
    if mode == "missing":
        assert all(host != "adapter.test" for host, body in calls)


@pytest.mark.parametrize("allowed", [True, False])
def test_native_governance_prevents_write_execution(config, monkeypatch, allowed):
    calls = wire(monkeypatch, allow=allowed)
    policy = RequestPolicy(config, "planner")

    effects = []

    class Provider:
        origin = "external"
        kind = "test"
        provider_name = "records"
        tools_claim = ("write",)

        def list_tools(self, **kwargs):
            return [{"name": "write", "parameters": {"type": "object"}}]

        def call_tool(self, name, args, **kwargs):
            effects.append(args["value"])
            return "written"

    engine = LiveLlmEngine(model="scripted", tool_provider=ManifestToolProvider([Provider()]))
    steps = iter(["TOOL_CALL", "STOP"])
    monkeypatch.setattr(
        engine,
        "_llm_call",
        lambda io: EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step=next(steps, "STOP"),
            tool_name="write",
            tool_arguments={"value": "kept", "destination": "untrusted", "transport": "grpc"},
            text="done",
        ),
    )
    instance = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=policy))
    instance.driver.agent_id = "planner"
    instance.capture_session_baseline()
    try:
        trace = instance.run_user_text("save a value")
        assert effects == (["kept"] if allowed else [])
        request = next(body for host, body in calls if host == "adapter.test")
        assert request["destination"] == "worker" and request["transport"] == "http"
        assert trace.client_responses
    finally:
        engine.stop()
        policy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["send", "get_task", "push"])
async def test_release_binds_recipient_and_exact_content(config, monkeypatch, kind):
    calls = wire(monkeypatch)
    policy = ReleasePolicy(config, "planner")
    policy.bridge.identity["push_destinations"] = {"https://worker.test/callback": "worker"}
    controller = DeliveryController("planner", policy)

    class Identity(User):
        is_authenticated = True
        user_name = "worker"

    identity = Identity()
    task = Task(
        id="task",
        context_id="conversation",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        history=[Message(message_id="reply", role=2, parts=[Part(text="exact response")])],
    )
    released = await controller.release(
        task,
        kind=kind,
        context=ServerCallContext(user=identity),
        destination="https://worker.test/callback" if kind == "push" else "",
    )
    assert released == task
    assert calls[1][1]["destination"] == "worker"
    await controller.aclose()


@pytest.mark.asyncio
async def test_unauthenticated_release_is_denied_before_publication(config, monkeypatch):
    from a2a.utils.errors import InvalidAgentResponseError

    calls = wire(monkeypatch)
    controller = DeliveryController("planner", ReleasePolicy(config, "planner"))
    with pytest.raises(InvalidAgentResponseError):
        await controller.release(Task(id="task"), kind="get_task", context=ServerCallContext())
    assert not calls
    await controller.aclose()


@pytest.mark.parametrize("patch", [{"timeout": 0}, {"adapter_url": "http://insecure.test"}])
def test_invalid_configuration_fails_before_connecting(config, patch):
    from pathlib import Path

    path = Path(config)
    path.write_text(json.dumps(json.loads(path.read_text()) | patch))
    with pytest.raises(ValueError):
        Bridge(config, "planner")


@pytest.mark.asyncio
async def test_unknown_destination_and_evidence_limit_fail_before_transport(config, monkeypatch):
    calls = wire(monkeypatch)
    bridge = Bridge(config, "planner")
    with pytest.raises(AuthorizationUnavailable, match="destination"):
        await bridge.authorize(destination="unknown", kind="tool", transport="http", payload=b"{}", context={})
    bridge.config["max_evidence_bytes"] = 1
    with pytest.raises(AuthorizationUnavailable, match="bound"):
        await bridge.authorize(destination="worker", kind="tool", transport="http", payload=b"{}", context={})
    assert not calls


def test_requests_without_active_context_or_route_do_not_execute(config, monkeypatch):
    from mas.runtime.kernel.coupling import GovDecision

    calls = wire(monkeypatch)
    policy = RequestPolicy(config, "planner")
    intent = SimpleNamespace(op="TOOL_CALL", tool_name="unknown", correlation_id=1, tool_arguments={})
    assert policy.evaluate_egress(intent, config=None)[0] is GovDecision.BLOCK
    policy.on_transition(
        SimpleNamespace(
            agent_id="planner",
            kind="USER_INPUT_RECEIVED",
            session_id="session",
            task_id="task",
            attributes={"text": "request"},
        )
    )
    assert policy.evaluate_egress(intent, config=None)[0] is GovDecision.BLOCK
    with pytest.raises(AuthorizationUnavailable):
        policy.on_transition(SimpleNamespace(agent_id="unexpected"))
    policy.close()
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_notifications_follow_final_delivery_decision(config, monkeypatch, allowed):
    from a2a.utils.errors import InvalidAgentResponseError

    wire(monkeypatch, allow=allowed)
    policy = ReleasePolicy(config, "planner")
    observed = []
    policy.subscribe(observed.append)
    policy.subscribe(observed.append)

    def broken(outcome):
        raise RuntimeError("observer failure")

    policy.subscribe(broken)
    controller = DeliveryController("planner", policy)
    user = _Identity()
    if allowed:
        await controller.release(Task(id="task"), kind="send", context=ServerCallContext(user=user))
    else:
        with pytest.raises(InvalidAgentResponseError):
            await controller.release(Task(id="task"), kind="send", context=ServerCallContext(user=user))
    assert len(observed) == 1
    assert (observed[0].payload is not None) == allowed
    policy.unsubscribe(observed.append)
    policy.unsubscribe(broken)
    await controller.aclose()


@pytest.mark.asyncio
async def test_publication_failure_and_wrong_producer_cannot_release(config, monkeypatch):
    from a2a.utils.errors import InvalidAgentResponseError

    calls = wire(monkeypatch, missing=True)
    policy = ReleasePolicy(config, "planner")
    user = _Identity()
    for name in ("unexpected", "planner"):
        controller = DeliveryController(name, policy)
        with pytest.raises(InvalidAgentResponseError):
            await controller.release(Task(id="task"), kind="send", context=ServerCallContext(user=user))
    assert len(calls) == 1
    await controller.aclose()


def test_example_mcp_write_and_read(tmp_path):
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path

    from library_ioa.plugins.mcp.provider import MCPToolProvider

    example = Path(__file__).resolve().parents[1] / "examples/records.py"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    provider = None
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, str(example), str(tmp_path / "record.json"), str(port)], stdout=log, stderr=log
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("Example MCP HTTP server did not start")
                    time.sleep(0.01)
            provider = MCPToolProvider.from_provider_spec(
                {
                    "name": "records",
                    "kind": "mcp",
                    "transport": "streamable-http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                }
            )
            assert {tool["name"] for tool in provider.list_tools()} == {"records_read", "records_write"}
            provider.call_tool("records_write", {"value": "saved value"})
            assert "saved value" in str(provider.call_tool("records_read", {}))
        finally:
            if provider is not None:
                provider.close()
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.parametrize(
    "patch",
    [
        {"transport": None},
        {"transport": "grpc"},
        {"transport": "stdio"},
        {"kind": "delegation", "transport": "stdio"},
        {"destination": "unknown"},
        {"tool": ""},
    ],
)
def test_invalid_configured_routes_fail_before_transport(config, monkeypatch, patch):
    from pathlib import Path

    calls = wire(monkeypatch)
    path = Path(config)
    data = json.loads(path.read_text())
    data["identities"]["planner"]["tools"]["write"].update(patch)
    path.write_text(json.dumps(data))
    with pytest.raises(AuthorizationUnavailable, match="route"):
        Bridge(config, "planner")
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, transport", [("tool", "grpc"), ("tool", "stdio"), ("delegation", "stdio"), ("release", "stdio")]
)
async def test_unsupported_operation_transport_never_publishes(config, monkeypatch, kind, transport):
    calls = wire(monkeypatch)
    with pytest.raises(AuthorizationUnavailable, match="HTTP"):
        await Bridge(config, "planner").authorize(
            destination="worker", kind=kind, transport=transport, payload=b"{}", context={}
        )
    assert not calls

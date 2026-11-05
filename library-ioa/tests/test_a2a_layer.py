#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The a2a protocol layer over SLIM (fake node) and over HTTP (the a2a exposure on loopback),
and the http transport."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import slim_node_fake as slim_fake
import uvicorn
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict, ParseDict
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.a2a.layer import A2ALayer, decode_response, encode_request
from library_ioa.plugins.http_layer import HttpLayer
from library_ioa.plugins.http_layer.transport import check_url
from library_ioa.plugins.slim.actor import _SHARED_LOOP, ConnectionActor
from mas.library.standard.lib.stack import Envelope, StackError, StackHandle
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.library.standard.testing.layer_conformance import check_layer, check_transport
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext

FakeWorld = slim_fake.FakeWorld

_SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
_SLIM = {
    "type": "slim",
    "params": {"url": "http://127.0.0.1:46357", "identity": "acme/lab/desk", "secret": _SECRET, "timeout_s": 0.25},
}
_A2A = {"type": "a2a-framing"}
_HANDLES: list[StackHandle] = []


@pytest.fixture(autouse=True)
def _fake_node(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("library_ioa.plugins.slim.actor.load_slim_bindings", lambda: slim_fake)
    monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
    monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)
    FakeWorld.reset()
    yield
    for handle in _HANDLES:
        handle.close()
    _HANDLES.clear()
    FakeWorld.reset()
    assert _SHARED_LOOP._refs == 0, "a test left a SLIM actor running"


def _a2a_over_slim(run_turn) -> StackHandle:
    handle = StackHandle("desk", [_SLIM, _A2A], run_turn)
    _HANDLES.append(handle)
    return handle


def _frames() -> list[dict[str, Any]]:
    return [json.loads(frame) for frame in FakeWorld.default().frames]


# --- the wire ---------------------------------------------------------------------------------------------------


def test_the_request_is_the_one_the_sdk_client_sends() -> None:
    body = json.loads(encode_request("hi", context_id="c1", correlation_id=3, caller_call_id="k", request_id="r"))
    assert (body["jsonrpc"], body["id"], body["method"]) == ("2.0", "r", "SendMessage")
    expected = SendMessageRequest(message=new_text_message("hi", context_id="c1", role=Role.ROLE_USER))
    expected.message.metadata.update({"mas.correlation_id": "3", "mas.caller_call_id": "k"})
    expected.message.message_id = body["params"]["message"]["messageId"]
    assert body["params"] == MessageToDict(expected)
    parsed = ParseDict(body["params"], SendMessageRequest())
    assert parsed.message.role == Role.ROLE_USER and parsed.message.parts[0].text == "hi"


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({"error": {"code": -32601, "message": "Method not found"}}, "A2A error -32601: Method not found"),
        ({"result": {"task": {"id": "t", "status": {"state": "TASK_STATE_FAILED"}}}}, "remote A2A task failed"),
        ({"result": {"task": {"id": "t", "status": {"state": "TASK_STATE_REJECTED"}}}}, "remote A2A task failed"),
        ({"result": {}}, "neither a message nor a task"),
        ({}, "no result"),
    ],
)
def test_a_failed_call_raises(result: dict[str, Any], message: str) -> None:
    with pytest.raises(AgentCommError, match=message):
        decode_response(json.dumps({"jsonrpc": "2.0", "id": "1", **result}))
    with pytest.raises(AgentCommError, match="not JSON"):
        decode_response("<html>")


def test_decode_response_refuses_a_body_that_is_not_a_send_message_result() -> None:
    with pytest.raises(AgentCommError, match="not text"):
        decode_response(1)
    with pytest.raises(AgentCommError, match="not a JSON-RPC object"):
        decode_response("[]")
    with pytest.raises(AgentCommError, match="not a SendMessage response"):
        decode_response(json.dumps({"jsonrpc": "2.0", "id": "1", "result": {"message": {"parts": "x"}}}))


def test_a_completed_task_with_no_artifacts_reads_the_agent_history() -> None:
    body = {
        "result": {
            "task": {
                "id": "t",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "history": [
                    {"messageId": "u", "role": "ROLE_USER", "parts": [{"text": "q"}]},
                    {"messageId": "a", "role": "ROLE_AGENT", "parts": [{"text": "from history"}]},
                ],
            }
        }
    }
    assert decode_response(json.dumps(body)) == "from history"
    empty = {"result": {"task": {"id": "t", "status": {"state": "TASK_STATE_COMPLETED"}, "history": []}}}
    assert decode_response(json.dumps(empty)) == ""


def test_the_layer_refuses_a_non_string_payload_and_a_bad_inbound_frame() -> None:
    layer = A2ALayer()

    class Lower:
        def send(self, envelope: Envelope) -> str:
            del envelope
            return json.dumps(
                {"jsonrpc": "2.0", "id": "1", "result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "x"}]}}}
            )

    layer.bind(Lower())
    with pytest.raises(AgentCommError, match="must be a string"):
        layer.send(Envelope(payload={"not": "text"}, meta={"intent": "task"}))
    answer = layer.serve(lambda envelope: envelope.payload)
    invalid = json.loads(answer(Envelope(payload='{"jsonrpc":"1.0","id":7,"method":"SendMessage"}')))
    assert invalid["error"]["code"] == -32600
    bad_params = json.loads(
        answer(
            Envelope(
                payload=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 8,
                        "method": "SendMessage",
                        "params": {"message": {"parts": "x"}},
                    }
                )
            )
        )
    )
    assert bad_params["error"]["code"] == -32602
    seen: list[int] = []

    def capture(envelope: Envelope) -> str:
        seen.append(int(envelope.meta["correlation_id"]))
        return "ok"

    framed = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "hi"}],
                    "metadata": {"mas.correlation_id": "not-a-number"},
                }
            },
        }
    )
    reply = json.loads(layer.serve(capture)(Envelope(payload=framed)))
    assert seen == [0]
    assert reply["result"]["message"]["parts"][0]["text"] == "ok"


def test_a_completed_task_reads_its_artifacts_then_its_status_text() -> None:
    artifact = {
        "result": {
            "task": {
                "id": "t",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"artifactId": "a", "parts": [{"text": "from "}, {"text": "artifact"}]}],
            }
        }
    }
    assert decode_response(json.dumps(artifact)) == "from artifact"
    status = {
        "result": {
            "task": {
                "id": "t",
                "status": {
                    "state": "TASK_STATE_COMPLETED",
                    "message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "done"}]},
                },
            }
        }
    }
    assert decode_response(json.dumps(status)) == "done"


def test_the_a2a_layer_conforms() -> None:
    reply = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {"message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "ok"}]}},
        }
    )
    check_layer(A2ALayer, lower_answer=reply, expect="ok")


# --- over SLIM --------------------------------------------------------------------------------------------------


def test_a2a_over_slim_round_trip_carries_unicode_ids_and_the_context() -> None:
    seen: list[tuple] = []

    def run_turn(agent: str, task: str, *rest: object) -> str:
        seen.append((agent, task, *rest))
        return f"{agent} répond: {task} ✓"

    handle = _a2a_over_slim(run_turn)
    triage = handle.link("triage")
    handle.link("billing")
    reply = triage.send(
        "billing", "facturé deux fois — 二重請求", correlation_id=7, caller_call_id="call-9", context_id="ctx-42"
    )
    assert reply == "billing répond: facturé deux fois — 二重請求 ✓"
    assert seen == [("billing", "facturé deux fois — 二重請求", 7, "call-9", "ctx-42")]
    request, answer = _frames()[:2]
    rpc = json.loads(request["task"])  # the SLIM frame holds the A2A request, not the bare task
    assert rpc["method"] == "SendMessage"
    assert rpc["params"]["message"]["contextId"] == "ctx-42"
    assert rpc["params"]["message"]["metadata"] == {"mas.correlation_id": "7", "mas.caller_call_id": "call-9"}
    assert request["headers"]["a2a-version"] == "1.0"
    response = json.loads(answer["task"])
    assert response["id"] == rpc["id"]
    assert response["result"]["message"]["contextId"] == "ctx-42"
    assert response["result"]["message"]["role"] == "ROLE_AGENT"
    assert asyncio.run(triage.asend("billing", "async")) == "billing répond: async ✓"


def test_a2a_over_slim_without_a_context_id_gets_a_new_one() -> None:
    contexts: list[str] = []

    def run_turn(agent: str, task: str, correlation_id: int, caller_call_id: str, context_id: str) -> str:
        contexts.append(context_id)
        return "ok"

    handle = _a2a_over_slim(run_turn)
    handle.link("b")
    assert handle.link("a").send("b", "x") == "ok"
    assert contexts and contexts[0]


def test_a_failing_handler_is_a_failed_task_and_the_caller_gets_an_agent_comm_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def run_turn(agent: str, task: str, *_: object) -> str:
        raise RuntimeError("database password is hunter2")

    handle = _a2a_over_slim(run_turn)
    handle.link("b")
    with pytest.raises(AgentCommError, match="remote A2A task failed: Agent execution failed") as raised:
        handle.link("a").send("b", "x")
    assert "hunter2" not in str(raised.value)  # the error text stays with the server
    response = json.loads(_frames()[1]["task"])
    assert response["result"]["task"]["status"]["state"] == "TASK_STATE_FAILED"


def test_only_the_task_intent_goes_out_and_nothing_leaves_for_another() -> None:
    handle = _a2a_over_slim(lambda *_: "ok")
    a = handle.link("a")
    handle.link("b")
    assert a.honoured_send_intents() == frozenset({"task"})
    for intent in ("inform", "ask"):
        with pytest.raises(AgentCommError, match=f"intent {intent!r} is not supported"):
            a.send("b", "x", context=SendContext(intent=intent))
    assert FakeWorld.default().frames == []


def test_a_plain_frame_on_an_a2a_stack_is_a_json_rpc_parse_error() -> None:
    handle = _a2a_over_slim(lambda *_: "never")
    b = handle.link("b")._layers[0].link
    assert b._serve_handler is not None
    answer = json.loads(b._serve_handler(Envelope(payload="not json")))
    assert answer["error"]["code"] == -32700
    other = json.loads(b._serve_handler(Envelope(payload=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "GetTask"}))))
    assert other["error"]["code"] == -32601


def test_a_plain_slim_stack_installs_no_inbound_chain() -> None:
    handle = StackHandle("desk", [_SLIM], lambda agent, task, *_: f"{agent}:{task}")
    _HANDLES.append(handle)
    a = handle.link("a")
    handle.link("b")
    assert a._layers[0].link._serve_handler is None
    assert a.send("b", "plain") == "b:plain"
    assert _frames()[0]["task"] == "plain"


def test_a2a_over_local_group_is_refused_because_local_group_cannot_serve_the_layer() -> None:
    handle = StackHandle("room", [{"type": "local-group", "params": {"identity": "acme/lab/mas"}}, _A2A], lambda *_: "")
    with pytest.raises(StackError, match="cannot pass inbound requests"):
        handle.link("a")
    handle.close()


def test_the_slim_layer_as_a_stack_wires_it_passes_the_transport_check() -> None:
    """The bottom layer a ``StackHandle`` builds for ``slim`` (the link in its ``LinkLayer``), checked as it is."""
    seen: list[str] = []
    handle = StackHandle("desk", [_SLIM], lambda agent, task, *_: seen.append(task) or f"{agent}:{task}")
    _HANDLES.append(handle)
    bottom = handle.link("a")._layers[0]
    handle.link("b")
    assert isinstance(bottom, LinkLayer) and type(bottom.link).__name__ == "SlimAgentComm"
    envelope = Envelope(payload="ping", meta={"target": "b", "intent": "task"})
    check_transport(lambda: bottom, envelope=envelope, reply="b:ping", delivered=lambda: len(seen))


def test_the_a2a_layer_above_slim_as_a_stack_wires_it_passes_the_layer_check() -> None:
    handle = StackHandle("desk", [_SLIM, _A2A], lambda agent, task, *_: f"{agent}:{task}")
    _HANDLES.append(handle)
    upper = handle.link("a")._layers[1]
    assert isinstance(upper, A2ALayer)
    framed = json.dumps(
        {"jsonrpc": "2.0", "id": "1", "result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "ok"}]}}}
    )
    check_layer(lambda: A2ALayer(), lower_answer=framed, expect="ok")


# --- over HTTP ----------------------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture()
def exposed_agent():
    """The a2a exposure (the server ``mas-ctl serve`` runs) on loopback, with a recording handler."""
    calls: list[dict[str, Any]] = []

    def handler(prompt: str, **kwargs: Any) -> dict[str, str]:
        calls.append({"prompt": prompt, **kwargs})
        if prompt == "boom":
            raise RuntimeError("server side detail")
        return {"text": f"served: {prompt}"}

    port = _free_port()
    app = A2AExposure().build_app({"metadata": {"name": "billing"}, "spec": {"description": "Billing"}}, handler)
    started = threading.Event()

    class Ready(uvicorn.Server):
        async def startup(self, sockets: Any = None) -> None:
            await super().startup(sockets=sockets)
            started.set()

    server = Ready(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", ws="none"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert started.wait(timeout=10)
    try:
        yield f"http://127.0.0.1:{port}", calls
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a2a_over_http_reaches_the_a2a_exposure(exposed_agent) -> None:
    url, calls = exposed_agent
    handle = StackHandle("remote", [{"type": "http", "params": {"url": url}}, _A2A])
    _HANDLES.append(handle)
    triage = handle.link("triage")
    assert triage.send("billing", "héllo ✓", correlation_id=5, caller_call_id="c-1", context_id="ctx-7") == (
        "served: héllo ✓"
    )
    call = calls[-1]
    assert call["session_id"] == "ctx-7"
    assert call["upstream_correlation_id"] == 5
    assert call["parent_call_id"] == "c-1"
    assert asyncio.run(triage.asend("billing", "again")) == "served: again"
    with pytest.raises(AgentCommError, match="remote A2A task failed") as raised:
        triage.send("billing", "boom")
    assert "server side detail" not in str(raised.value)
    with pytest.raises(AgentCommError, match="not supported"):
        triage.send("billing", "x", context=SendContext(intent="inform"))


def test_the_http_transport_passes_the_transport_check_and_reports_http_errors() -> None:
    bodies: list[tuple[bytes, dict[str, str]]] = []

    class Echo(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            bodies.append((body, {k.lower(): v for k, v in self.headers.items()}))
            status = 503 if body == b"down" else 200
            out = b"echo:" + body
            self.send_response(status)
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *_: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/rpc"
    try:
        envelope = Envelope(payload="ping ✓", headers={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"})
        check_transport(
            lambda: HttpLayer(url=url, headers={"x-static": "1"}),
            envelope=envelope,
            reply="echo:ping ✓",
            delivered=lambda: len(bodies),
        )
        body, headers = bodies[-1]
        assert body == "ping ✓".encode()
        assert headers["x-static"] == "1" and headers["traceparent"].startswith("00-")
        assert headers["content-type"] == "application/json"
        layer = HttpLayer(url=url)
        with pytest.raises(AgentCommError, match="sends text or bytes"):
            layer.send(Envelope(payload={"not": "bytes"}))
        with pytest.raises(AgentCommError, match="answered 503"):
            layer.send(Envelope(payload="down"))
        layer.stop()
        with pytest.raises(AgentCommError, match="stopped"):
            layer.send(Envelope(payload="late"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with pytest.raises(AgentCommError, match="ConnectError"):
        HttpLayer(url=url, timeout=1).send(Envelope(payload="nobody"))


def test_the_http_transport_caps_the_reply_and_never_shows_the_url_query() -> None:
    class Big(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            status = 500 if self.path.startswith("/fail") else 200
            out = b"x" * 64
            self.send_response(status)
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *_: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Big)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert HttpLayer(url=f"{base}/ok", max_message_size=64).send(Envelope(payload="a")) == "x" * 64
        with pytest.raises(AgentCommError, match="is over 63 bytes"):
            HttpLayer(url=f"{base}/ok", max_message_size=63).send(Envelope(payload="a"))
        layer = HttpLayer(url=f"{base}/fail?sig=hidden#frag")
        with pytest.raises(AgentCommError, match="answered 500") as err:
            layer.send(Envelope(payload="a"))
        assert "hidden" not in str(err.value) and "hidden" not in repr(layer)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with pytest.raises(AgentCommError) as err:
        HttpLayer(url=f"{base}/gone?sig=hidden", timeout=1).send(Envelope(payload="a"))
    assert "hidden" not in str(err.value)
    for bad in (0, -1, True, "1"):
        with pytest.raises(AgentCommError, match="max_message_size"):
            HttpLayer(url=base, max_message_size=bad)  # type: ignore[arg-type]


def test_the_http_transport_sends_each_header_once_whatever_its_spelling() -> None:
    seen: list[dict[str, list[str]]] = []

    class Record(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            seen.append({name.lower(): self.headers.get_all(name) or [] for name in self.headers})
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Record)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    layer = HttpLayer(url=url, headers={"Content-Type": "text/plain", "X-Static": "1"})
    try:
        layer.send(Envelope(payload="hi", headers={"x-static": "2"}))
    finally:
        layer.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert seen[0]["content-type"] == ["text/plain"]
    assert seen[0]["x-static"] == ["2"]  # the envelope's header wins over the row's


@pytest.mark.parametrize(
    "headers",
    [{"x-a": "b\r\nx-injected: 1"}, {"bad name": "1"}, {"x\r\ny": "1"}, {"content-type": "a\nb"}],
)
def test_the_http_transport_refuses_a_header_it_could_not_send_as_one_line(headers: dict[str, str]) -> None:
    with pytest.raises(AgentCommError):
        HttpLayer(url="http://127.0.0.1:9", headers=headers)
    with pytest.raises(AgentCommError):
        HttpLayer(url="http://127.0.0.1:9", content_type="text/plain\r\nx: 1")


@pytest.mark.parametrize("name", ["Host", "content-length", "Transfer-Encoding", "connection"])
def test_the_http_transport_refuses_a_framing_header_from_a_row_or_a_send(name: str) -> None:
    with pytest.raises(AgentCommError, match="sets .* itself"):
        HttpLayer(url="http://127.0.0.1:9", headers={name: "x"})
    layer = HttpLayer(url="http://127.0.0.1:9")
    with pytest.raises(AgentCommError, match="sets .* itself"):
        layer.send(Envelope(payload="hi", headers={name: "x"}))  # refused before anything connects
    layer.stop()


def test_a_stack_declares_the_flags_of_a_generic_layer_id() -> None:
    from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

    flags = StackAgentComm.declared_capabilities({"layers": [{"type": "http"}, {"type": "a2a-framing"}]})
    assert flags is not None and flags["unicast"]
    assert StackAgentComm.declared_capabilities({"layers": [{"type": "no-such-layer"}]}) is None


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "requires url"),
        ("http://10.0.0.1:9000", "only allowed on 127.0.0.1"),
        ("ftp://127.0.0.1", "scheme 'ftp'"),
        ("http://user:pw@127.0.0.1", "credentials"),
        ("http://127.0.0.1\\@evil", "backslash"),
        ("https://", "missing a host"),
    ],
)
def test_the_http_url_is_checked(url: str, message: str) -> None:
    with pytest.raises(AgentCommError, match=message):
        check_url(url)
    check_url("https://agents.example.test")
    check_url("http://[::1]:9000")


def test_the_http_transport_cannot_listen() -> None:
    from mas.library.standard.lib.stack.handle import layer_classes, listen_refusal

    reason = listen_refusal(layer_classes([{"type": "http", "params": {}}, {"type": "a2a-framing", "params": {}}]))
    assert "only connects" in reason and "a2a agent_expose" in reason
    assert listen_refusal(layer_classes([{"type": "slim", "params": {}}, {"type": "a2a-framing", "params": {}}])) == ""


def test_the_released_agent_comm_stack_keeps_a2a_as_the_sdk_link() -> None:
    """``agent_comm/stack`` resolves ``a2a`` as the ``agent_comm`` link (the A2A SDK client), not the framing layer."""
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

    stack = StackAgentComm(layers=[{"type": "a2a", "params": {"url": "http://127.0.0.1:9"}}])
    assert isinstance(stack._layers[0].link, A2AAgentComm)
    assert stack._layers[0].link.endpoint == "http://127.0.0.1:9"

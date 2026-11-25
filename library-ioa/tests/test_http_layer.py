#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The http transport, and the released a2a link."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from library_ioa.plugins.http_layer import HttpLayer
from library_ioa.plugins.http_layer.transport import check_url
from mas.library.standard.lib.stack import Envelope, StackHandle
from mas.library.standard.testing.layer_conformance import check_transport
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

_HANDLES: list[StackHandle] = []


@pytest.fixture(autouse=True)
def _close_handles():
    yield
    for handle in _HANDLES:
        handle.close()
    _HANDLES.clear()


async def test_the_http_transport_passes_the_transport_check_and_reports_http_errors() -> None:
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/rpc"
    try:
        envelope = Envelope(payload="ping ✓", headers={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"})
        await check_transport(
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
            await layer.asend(Envelope(payload={"not": "bytes"}))
        with pytest.raises(AgentCommError, match="answered 503"):
            await layer.asend(Envelope(payload="down"))
        layer.stop()
        with pytest.raises(AgentCommError, match="stopped"):
            await layer.asend(Envelope(payload="late"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with pytest.raises(AgentCommError, match="ConnectError"):
        await HttpLayer(url=url, timeout=1).asend(Envelope(payload="nobody"))


async def test_the_http_transport_caps_the_reply_and_never_shows_the_url_query() -> None:
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert (await HttpLayer(url=f"{base}/ok", max_message_size=64).asend(Envelope(payload="a"))) == "x" * 64
        with pytest.raises(AgentCommError, match="is over 63 bytes"):
            await HttpLayer(url=f"{base}/ok", max_message_size=63).asend(Envelope(payload="a"))
        layer = HttpLayer(url=f"{base}/fail?sig=hidden#frag")
        with pytest.raises(AgentCommError, match="answered 500") as err:
            await layer.asend(Envelope(payload="a"))
        assert "hidden" not in str(err.value) and "hidden" not in repr(layer)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with pytest.raises(AgentCommError) as err:
        await HttpLayer(url=f"{base}/gone?sig=hidden", timeout=1).asend(Envelope(payload="a"))
    assert "hidden" not in str(err.value)
    for bad in (0, -1, True, "1"):
        with pytest.raises(AgentCommError, match="max_message_size"):
            HttpLayer(url=base, max_message_size=bad)  # type: ignore[arg-type]


async def test_the_http_transport_sends_each_header_once_whatever_its_spelling() -> None:
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    layer = HttpLayer(url=url, headers={"Content-Type": "text/plain", "X-Static": "1"})
    try:
        await layer.asend(Envelope(payload="hi", headers={"x-static": "2"}))
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
async def test_the_http_transport_refuses_a_framing_header_from_a_row_or_a_send(name: str) -> None:
    with pytest.raises(AgentCommError, match="sets .* itself"):
        HttpLayer(url="http://127.0.0.1:9", headers={name: "x"})
    layer = HttpLayer(url="http://127.0.0.1:9")
    with pytest.raises(AgentCommError, match="sets .* itself"):
        await layer.asend(Envelope(payload="hi", headers={name: "x"}))  # refused before anything connects
    layer.stop()


def test_a_stack_declares_the_flags_of_a_generic_layer_id() -> None:
    from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

    flags = StackAgentComm.declared_capabilities({"layers": [{"type": "slim"}, {"type": "a2a-rpc"}]})
    assert flags is not None and flags["unicast"]
    assert StackAgentComm.declared_capabilities({"layers": [{"type": "no-such-layer"}]}) is None


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "requires url"),
        ("http://10.0.0.1:9000", "only allowed on a loopback host"),
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

    reason = listen_refusal(layer_classes([{"type": "http", "params": {}}, {"type": "a2a-rpc", "params": {}}]))
    assert "only connects" in reason and "a2a agent_expose" in reason
    assert listen_refusal(layer_classes([{"type": "slim", "params": {}}, {"type": "a2a-rpc", "params": {}}])) == ""


def test_the_released_agent_comm_stack_keeps_a2a_as_the_sdk_link() -> None:
    """``agent_comm/stack`` resolves ``a2a`` as the ``agent_comm`` link (the A2A SDK client), not the framing layer."""
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

    stack = StackAgentComm(layers=[{"type": "a2a", "params": {"url": "http://127.0.0.1:9"}}])
    assert isinstance(stack._layers[0].link, A2AAgentComm)
    assert stack._layers[0].link.endpoint == "http://127.0.0.1:9"

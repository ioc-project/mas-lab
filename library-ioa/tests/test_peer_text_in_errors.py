#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Text a peer or a server sent is bounded and printable wherever an error or a log line quotes it."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest
from library_ioa.plugins.a2a.layer import decode_response
from library_ioa.utils import translate_mcp_error
from mas.library.standard.lib.comm.text import MAX_ERROR_TEXT
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

HOSTILE = "line one\nERROR forged\x1b[31m" + "A" * 5000 + "‮"
SRC = Path(__file__).resolve().parents[1] / "src" / "library_ioa"


def _assert_clean(text: str) -> None:
    assert "\n" not in text and "\x1b" not in text and "‮" not in text
    assert len(text) <= MAX_ERROR_TEXT + 80  # the fixed words around the quoted text


def test_a2a_error_object_from_a_peer_is_its_code_only() -> None:
    # kills: a peer's JSON-RPC error message (which may echo the caller's credential) copied into the error
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": HOSTILE}})
    with pytest.raises(AgentCommError) as raised:
        decode_response(raw)
    assert str(raised.value) == "A2A error -32000"


def test_a2a_error_code_that_is_not_a_number_cannot_smuggle_text() -> None:
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": HOSTILE, "message": "m"}})
    with pytest.raises(AgentCommError) as raised:
        decode_response(raw)
    assert str(raised.value) == "A2A error"


def test_failed_remote_task_status_text_is_not_carried() -> None:
    # kills: the failed-task status text of a peer copied into the error unbounded
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "task": {
                    "id": "t",
                    "status": {
                        "state": "TASK_STATE_FAILED",
                        "message": {"role": "ROLE_AGENT", "parts": [{"text": HOSTILE}]},
                    },
                }
            },
        }
    )
    with pytest.raises(AgentCommError) as raised:
        decode_response(raw)
    assert str(raised.value) == "remote A2A task failed"


def test_mcp_error_result_is_cleaned() -> None:
    # kills: an MCP server's error text reaching the model as a tool result unbounded
    out = translate_mcp_error(RuntimeError(HOSTILE))
    _assert_clean(out["error"])
    assert out["is_error"] is True and out["type"] == "RuntimeError"


def test_mcp_failure_log_line_is_cleaned(caplog) -> None:
    # kills: a log line that repeats a server's error text with its newlines
    import asyncio

    from library_ioa.plugins.mcp.provider import MCPToolProvider

    class Client:
        def call_tool(self, *_a, **_k):
            raise RuntimeError(HOSTILE)

    provider = MCPToolProvider(Client(), tools_claim="*")
    with caplog.at_level(logging.ERROR):
        asyncio.run(provider.acall_tool("t\nx", {}))
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert messages and all("\n" not in m and "\x1b" not in m for m in messages)


def test_http_transport_error_is_cleaned() -> None:
    # kills: an httpx error (which can quote what the server sent) copied into the error unbounded
    import asyncio

    import httpx
    from library_ioa.plugins.http_layer import HttpLayer
    from mas.library.standard.lib.stack import Envelope

    class Failing:
        def stream(self, *_a, **_k):
            raise httpx.ConnectError(HOSTILE)

    layer = HttpLayer(url="https://peer.example/x")
    layer._clients.get = lambda _factory: Failing()  # type: ignore[method-assign]
    with pytest.raises(AgentCommError) as raised:
        asyncio.run(layer.asend(Envelope(payload=b"x")))
    _assert_clean(str(raised.value))
    assert str(raised.value).startswith("http transport: ConnectError: line one ERROR forged")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("task-not-found", "remote A2A peer answered an error: TASK_NOT_FOUND (-32001)"),
        ("json-rpc", "remote A2A peer answered JSON-RPC error -32099"),
        ("status", "remote A2A peer: HTTPStatusError: HTTP 401"),
        ("timeout", "remote A2A peer timed out"),
        ("card", "remote A2A agent card could not be read: HTTP 403"),
        ("sse", "remote A2A peer sent an answer that is not valid A2A"),
    ],
)
def test_an_sdk_error_of_an_a2a_peer_is_a_closed_reason(kind: str, expected: str) -> None:
    # kills: the SDK's exception text (the peer's JSON-RPC message, an HTTP body) carried into the send result
    import httpx
    from a2a.client.errors import A2AClientError, A2AClientTimeoutError, AgentCardResolutionError
    from a2a.utils.errors import TaskNotFoundError
    from library_ioa.plugins.a2a.client import peer_error

    canary = "Bearer sk-canary-A2a7"
    if kind == "task-not-found":
        exc: Exception = TaskNotFoundError(f"bad {canary}", data={"token": canary})
    elif kind == "json-rpc":
        exc = A2AClientError(f"JSON-RPC Error -32099: bad {canary}")
    elif kind == "status":
        request = httpx.Request("POST", "http://127.0.0.1:9/")
        response = httpx.Response(401, text=f"bad {canary}", request=request)
        try:
            try:
                raise httpx.HTTPStatusError(f"bad {canary}", request=request, response=response)
            except httpx.HTTPStatusError as cause:
                raise A2AClientError(f"HTTP Error 401: {cause}") from cause
        except A2AClientError as raised:
            exc = raised
    elif kind == "timeout":
        exc = A2AClientTimeoutError("Client Request timed out")
    elif kind == "card":
        exc = AgentCardResolutionError(f"card refused: {canary}", status_code=403)
    else:
        exc = A2AClientError(f"SSE stream error: {canary}")
    error = peer_error(exc)
    assert isinstance(error, AgentCommError)
    assert str(error) == expected


def test_a2a_comm_raises_the_closed_reason_without_the_sdk_error_in_its_chain() -> None:
    # kills: ``AgentCommError(...) from exc``: the peer's message printed again in the traceback's cause
    import asyncio
    import traceback

    from a2a.utils.errors import TaskNotFoundError
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm

    canary = "Bearer sk-canary-A2a8"
    comm = A2AAgentComm("http://127.0.0.1:9")

    async def fail(*_a, **_k):
        raise TaskNotFoundError(f"bad {canary}")

    comm._client.asend_message = fail  # type: ignore[method-assign]
    with pytest.raises(AgentCommError) as raised:
        asyncio.run(comm.asend("peer", "hi"))
    assert canary not in "".join(traceback.format_exception(raised.value))
    assert str(raised.value) == "remote A2A peer answered an error: TASK_NOT_FOUND (-32001)"


def _quotes_raw_exception(node: ast.AST) -> bool:
    """``str(<name>)`` or ``{<name>}`` of an exception-like name, outside ``safe_text(...)``."""
    risky = {"exc", "e", "err", "error", "message", "status_text"}
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "safe_text":
        return False
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "str":
        return any(isinstance(a, ast.Name) and a.id in risky for a in node.args)
    if isinstance(node, ast.FormattedValue):
        return isinstance(node.value, ast.Name) and node.value.id in risky
    return any(_quotes_raw_exception(child) for child in ast.iter_child_nodes(node))


def test_no_error_path_quotes_a_raw_exception_or_peer_string() -> None:
    # kills: any AgentCommError in the A2A, SLIM and HTTP layers going back to str(exc) or f"{message}"
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AgentCommError":
                if any(_quotes_raw_exception(arg) for arg in node.args):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, "AgentCommError quotes raw text: " + ", ".join(offenders)

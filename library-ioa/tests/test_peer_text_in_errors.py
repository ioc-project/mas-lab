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


def test_a2a_error_object_from_a_peer_is_cleaned() -> None:
    # kills: a peer's JSON-RPC error message copied into the error unbounded
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": HOSTILE}})
    with pytest.raises(AgentCommError) as raised:
        decode_response(raw)
    _assert_clean(str(raised.value))
    assert str(raised.value).startswith("A2A error -32000: line one ERROR forged")


def test_a2a_error_code_that_is_not_a_number_cannot_smuggle_text() -> None:
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": HOSTILE, "message": "m"}})
    with pytest.raises(AgentCommError) as raised:
        decode_response(raw)
    _assert_clean(str(raised.value))


def test_failed_remote_task_status_text_is_cleaned() -> None:
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
    _assert_clean(str(raised.value))


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

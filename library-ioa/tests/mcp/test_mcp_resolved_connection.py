# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Connection metadata holds tools/call, separately from MCP initialization."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from library_ioa.plugins.mcp.provider import MCPToolProvider
from mas.runtime.boundary.connection import BindingUnavailable, ConnectionTarget

_SERVER = Path(__file__).with_name("_binding_server.py")


@pytest.fixture(params=["http", "stdio"])
def records(request, tmp_path):
    state = tmp_path / "records.txt"
    process = None
    if request.param == "stdio":
        spec = {
            "name": "records",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(_SERVER), str(state)],
            "cwd": str(tmp_path),
        }
        target = ConnectionTarget(command=(sys.executable, str(_SERVER), str(state)), cwd=str(tmp_path))
    else:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, str(_SERVER), str(state), str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    process.terminate()
                    process.wait(5)
                    pytest.fail("MCP HTTP fixture did not become ready")
                time.sleep(0.01)
        spec = {"name": "records", "transport": "streamable-http", "url": f"http://127.0.0.1:{port}/mcp"}
        target = ConnectionTarget(endpoint=spec["url"])
    provider = MCPToolProvider.from_provider_spec(spec)
    try:
        yield provider, state, target, request.param
    finally:
        provider.close()
        if process is not None:
            process.terminate()
            process.wait(5)


def test_discovery_and_preparation_do_not_execute_a_denied_write(records):
    provider, state, target, transport = records
    assert {tool["name"] for tool in provider.discover_tools()} == {"read", "write"}
    assert state.with_suffix(".started").exists()  # Discovery may initialize a subprocess.
    operation = provider.prepare_call("write", {"value": "denied"})
    assert operation.connection.transport == transport
    assert operation.connection.protocol == "mcp"
    assert operation.connection.binding_ref == "mcp:records"
    assert operation.connection.matches_target(target)
    operation.close()  # A denial releases the operation without invoking it.
    assert not state.exists()
    with pytest.raises(BindingUnavailable):
        operation.invoke()


def test_pinned_sync_and_async_calls_preserve_effects_and_native_conversion(records):
    provider, state, _, _ = records
    write = provider.prepare_call("write", {"value": "once"})
    assert not write.invoke().get("is_error")
    assert state.read_text() == "once\n"
    read = provider.prepare_call("read", {})
    result = asyncio.run(read.ainvoke())
    assert "once" in str(result)
    assert state.read_text() == "once\n"
    write.close()
    read.close()


def test_replacing_a_provider_rejects_its_pending_write(records):
    provider, state, _, _ = records
    pending = provider.prepare_call("write", {"value": "denied"})
    provider.retire_bindings()
    with pytest.raises(BindingUnavailable):
        pending.invoke()
    with pytest.raises(BindingUnavailable):
        asyncio.run(pending.ainvoke())
    assert not state.exists()


def test_missing_connection_capability_fails_without_transport():
    from types import SimpleNamespace

    provider = MCPToolProvider(SimpleNamespace())
    with pytest.raises(BindingUnavailable, match="no resolved"):
        provider.prepare_call("write", {})


def test_native_business_error_retains_tool_identity_and_no_write(records):
    provider, state, _, _ = records
    # The real server rejects a missing required argument. No write executes.
    result = provider.prepare_call("write", {}).invoke()
    assert result["is_error"]
    assert result["tool"] == "write"
    assert not state.exists()


@pytest.mark.asyncio
async def test_async_first_call_owns_session_cleanup(records):
    provider, state, _, _ = records
    result = await provider.prepare_call("read", {}).ainvoke()
    assert "empty" in str(result)
    assert state.with_suffix(".started").exists()
    assert not state.exists()


def test_native_engine_stop_closes_owned_mcp_client(records):
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.engine.manifest_tool_provider import ManifestToolProvider

    provider, state, _, _ = records
    engine = LiveLlmEngine(model="scripted", tool_provider=ManifestToolProvider([provider]))
    operation = provider.prepare_call("read", {})
    assert "empty" in str(operation.invoke())
    assert provider._client._loop.started
    engine.stop()
    assert not provider._client._loop.started
    with pytest.raises(BindingUnavailable):
        operation.invoke()
    engine.stop()

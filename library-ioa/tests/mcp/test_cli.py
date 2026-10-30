#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from library_ioa.plugins.mcp.server import _build_parser, main


def test_parser_serve_and_tools_commands() -> None:
    parser = _build_parser()
    serve = parser.parse_args(
        [
            "serve",
            "--tool-manifest",
            "tool.yaml",
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--port",
            "9001",
        ]
    )
    assert serve.command == "serve"
    assert serve.transport == "streamable-http"
    assert serve.host == "0.0.0.0"
    assert serve.port == 9001
    listed = parser.parse_args(["tools", "list", "--url", "http://example.invalid/mcp"])
    assert listed.tools_command == "list"
    called = parser.parse_args(["tools", "call", "--tool", "web-search", "--arguments", '{"query":"x"}'])
    assert called.tools_command == "call"
    assert called.tool == "web-search"


def test_main_tools_list_and_call(capsys) -> None:
    client = MagicMock()
    client.list_tools = AsyncMock(return_value=[{"name": "web-search"}])
    client.call_tool = AsyncMock(return_value={"content": [{"type": "text", "text": "ok"}]})

    with patch("library_ioa.plugins.mcp.client.MCPClient", return_value=client):
        assert main(["tools", "list", "--url", "http://127.0.0.1:9001/mcp"]) == 0
        assert json.loads(capsys.readouterr().out) == [{"name": "web-search"}]
        assert main(["tools", "call", "--tool", "web-search", "--arguments", '{"query":"Apple"}']) == 0
        assert json.loads(capsys.readouterr().out)["content"][0]["text"] == "ok"
    client.call_tool.assert_called()


def test_main_tools_call_rejects_non_object_arguments() -> None:
    with pytest.raises(SystemExit):
        main(["tools", "call", "--tool", "web-search", "--arguments", '["not", "an", "object"]'])


def test_main_serve_stdio_and_http(tmp_path) -> None:
    tool_manifest = tmp_path / "t.yaml"
    tool_manifest.write_text(
        "apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: test-tools\n",
        encoding="utf-8",
    )
    factory = MagicMock()
    factory._tools = [{"name": "sample-tool"}]
    with patch("library_ioa.plugins.mcp.server.MCPToolServerFactory.from_manifest", return_value=factory) as build:
        assert main(["serve", "--tool-manifest", str(tool_manifest)]) == 0
        build.assert_called_with(tool_manifest, tool_name=None, server_name="test-tools")
        factory.run.assert_called_with(transport="stdio")
        assert (
            main(
                [
                    "serve",
                    "--tool-manifest",
                    str(tmp_path / "t.yaml"),
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9001",
                    "--no-json-response",
                    "--no-stateless-http",
                ]
            )
            == 0
        )
        factory.run.assert_called_with(
            transport="streamable-http",
            host="127.0.0.1",
            port=9001,
            json_response=False,
            stateless_http=False,
        )


def test_main_serve_tools_dir_without_infra(tmp_path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    tool_manifest = tools_dir / "sample.tool.yaml"
    tool_manifest.write_text("apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: sample-tool\n", encoding="utf-8")
    factory = MagicMock()
    factory._tools = [{"name": "sample-tool"}]
    with patch("library_ioa.plugins.mcp.server.MCPToolServerFactory.from_manifest", return_value=factory) as build:
        assert main(["serve", "--tools-dir", str(tools_dir), "--transport", "streamable-http"]) == 0
    build.assert_called_once_with(tool_manifest.resolve(), tool_name=None, server_name="sample-tool")
    factory.run.assert_called_with(
        transport="streamable-http",
        host="127.0.0.1",
        port=9001,
        json_response=True,
        stateless_http=True,
    )


def test_main_serve_uses_listen_infra_and_tool_claim(tmp_path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    tool_manifest = tools_dir / "sample.tool.yaml"
    tool_manifest.write_text("apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: sample-tool\n", encoding="utf-8")
    infra = tmp_path / "mcp-deploy.yaml"
    infra.write_text(
        "apiVersion: infra/v1\n"
        "kind: ToolServerRegistry\n"
        "metadata:\n  name: mcp-deploy\n"
        "spec:\n  tool_servers:\n"
        "    - id: local-mcp\n"
        "      name: Public Tools\n"
        "      protocol: MCP\n"
        "      usage: listen\n"
        "      transport: streamable-http\n"
        "      host: 0.0.0.0\n"
        "      port: 9123\n"
        "      tools_dir: tools\n"
        "      tools: [sample-tool]\n",
        encoding="utf-8",
    )
    factory = MagicMock()
    factory._tools = [{"name": "sample-tool"}]
    with patch("library_ioa.plugins.mcp.server.MCPToolServerFactory.from_manifest", return_value=factory) as build:
        assert main(["serve", "--infra-ref", str(infra)]) == 0
    build.assert_called_once_with(tool_manifest.resolve(), tool_name=None, server_name="Public Tools")
    factory.run.assert_called_with(
        transport="streamable-http",
        host="0.0.0.0",
        port=9123,
        json_response=True,
        stateless_http=True,
    )


@pytest.mark.parametrize("usage", ["listen", "deploy", "[connect, listen]", "use-and-deploy"])
def test_main_serve_accepts_new_and_deprecated_listen_usage(tmp_path, usage: str) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "sample.tool.yaml").write_text(
        "apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: sample-tool\n", encoding="utf-8"
    )
    infra = tmp_path / "mcp.yaml"
    infra.write_text(
        "apiVersion: infra/v1\nkind: ToolServerRegistry\nmetadata:\n  name: mcp\n"
        f"spec:\n  tool_servers:\n    - id: local-mcp\n      protocol: mcp\n      usage: {usage}\n"
        "      tools_dir: tools\n",
        encoding="utf-8",
    )
    factory = MagicMock()
    factory._tools = [{"name": "sample-tool"}]
    with patch("library_ioa.plugins.mcp.server.MCPToolServerFactory.from_manifest", return_value=factory):
        assert main(["serve", "--infra-ref", str(infra)]) == 0


@pytest.mark.parametrize("usage", ["connect", "use"])
def test_main_serve_rejects_a_connect_only_registry(tmp_path, usage: str) -> None:
    infra = tmp_path / "mcp.yaml"
    infra.write_text(
        "apiVersion: infra/v1\nkind: ToolServerRegistry\nmetadata:\n  name: mcp\n"
        f"spec:\n  tool_servers:\n    - id: remote\n      protocol: mcp\n      usage: {usage}\n"
        "      url: http://127.0.0.1:9001/mcp\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        main(["serve", "--infra-ref", str(infra)])


def test_main_module_entrypoint_imports() -> None:
    from library_ioa.plugins.mcp.server import __main__ as server_main

    assert callable(server_main.main)

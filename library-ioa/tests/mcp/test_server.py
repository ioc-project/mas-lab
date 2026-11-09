#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fixtures.mcp_conformance_server import build_server
from library_ioa.plugins.mcp.server import MCPToolServerFactory, _python_type_for_schema
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, InputRequiredResult


def _write_tool(tmp_path: Path, *, extra_spec: str = "", class_body: str | None = None) -> Path:
    tool_module = tmp_path / "sample_tool.py"
    tool_module.write_text(
        class_body
        or (
            "class SampleTool:\n"
            "    def execute(self, **kwargs):\n"
            "        return {'answer': kwargs['a'] + kwargs['b']}\n"
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "sample.tool.yaml"
    manifest_path.write_text(
        "apiVersion: mas/v1\n"
        "kind: Tool\n"
        "metadata:\n"
        "  name: sample-tool\n"
        "  description: Catalogue label\n"
        "spec:\n"
        "  description: Adds two integers\n"
        "  parameters:\n"
        "    - name: a\n"
        "      type: integer\n"
        "      required: true\n"
        "    - name: b\n"
        "      type: integer\n"
        "      required: true\n"
        "  returns:\n"
        "    type: integer\n"
        "  idempotent: true\n"
        f"{extra_spec}"
        "  impl:\n"
        "    kind: python\n"
        f"    module_path: {tool_module.name}\n"
        "    class_name: SampleTool\n",
        encoding="utf-8",
    )
    return manifest_path


def test_factory_wraps_manifest_tool(tmp_path: Path) -> None:
    factory = MCPToolServerFactory.from_manifest(_write_tool(tmp_path))
    assert len(factory._tools) == 1
    assert factory._tools[0]["name"] == "sample-tool"
    assert factory._tools[0]["description"] == "Adds two integers"
    assert factory._tools[0]["mcp"]["title"] == "Catalogue label"
    assert factory._tools[0]["mcp"]["annotations"]["idempotentHint"] is True
    assert factory._tools[0]["mcp"]["outputSchema"]["type"] == "integer"
    assert factory._tools[0]["fn"](a=2, b=3) == {"answer": 5}


def test_factory_prefers_call_tool_over_execute(tmp_path: Path) -> None:
    factory = MCPToolServerFactory.from_manifest(
        _write_tool(
            tmp_path,
            class_body=(
                "class SampleTool:\n"
                "    def execute(self, **kwargs):\n"
                "        raise AssertionError('execute must not be used when call_tool exists')\n"
                "    def call_tool(self, tool_name, arguments):\n"
                "        return {'tool': tool_name, 'sum': arguments['a'] + arguments['b']}\n"
            ),
        )
    )
    assert factory._tools[0]["fn"](a=2, b=3) == {"tool": "sample-tool", "sum": 5}


def test_factory_converts_existing_tool_result_envelope(tmp_path: Path) -> None:
    factory = MCPToolServerFactory.from_manifest(
        _write_tool(
            tmp_path,
            class_body=(
                "from mas.runtime.contracts.tool_contract import ToolResultEnvelope\n"
                "class SampleTool:\n"
                "    def execute(self, **kwargs):\n"
                "        return ToolResultEnvelope(content=[{'type': 'text', 'text': 'converted'}])\n"
            ),
        )
    )

    result = factory._tools[0]["fn"](a=2, b=3)

    assert isinstance(result, CallToolResult)
    assert result.content[0].text == "converted"


def test_factory_converts_inline_tool_result_envelope(tmp_path: Path) -> None:
    factory = MCPToolServerFactory.from_manifest(
        _write_tool(
            tmp_path,
            class_body=(
                "from mas.runtime.contracts.tool_contract import ToolResultEnvelope\n"
                "class SampleTool:\n"
                "    def execute(self, **kwargs):\n"
                "        return ToolResultEnvelope.inline({'answer': kwargs['a'] + kwargs['b']})\n"
            ),
        ),
        server_name="public-tools",
    )

    result = factory._tools[0]["fn"](a=2, b=3)

    assert factory.server_name == "public-tools"
    assert isinstance(result, CallToolResult)
    assert result.content[0].text == '{\n  "answer": 5\n}'


def test_factory_injects_sdk_context_only_when_declared(tmp_path: Path) -> None:
    factory = MCPToolServerFactory.from_manifest(
        _write_tool(
            tmp_path,
            class_body=(
                "class SampleTool:\n"
                "    def call_tool(self, tool_name, arguments, *, ctx=None):\n"
                "        return {'has_context': ctx is not None}\n"
            ),
        )
    )
    wrapped = factory._tools[0]["fn"]
    context = MagicMock(spec=Context)

    assert "ctx" in wrapped.__signature__.parameters
    assert factory._server._tool_manager.get_tool("sample-tool").context_kwarg == "ctx"
    assert wrapped(a=2, b=3, ctx=context) == {"has_context": True}


def test_factory_optional_parameter_and_unknown_type() -> None:
    assert _python_type_for_schema("integer") is int
    assert _python_type_for_schema("unknown") is not int


def test_factory_preserves_explicit_mcp_input_schema() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "$defs": {"label": {"type": "string"}},
        "properties": {"label": {"$ref": "#/$defs/label", "x-mcp-header": "Label"}},
    }
    factory = MCPToolServerFactory()
    factory.add_tool(
        {
            "name": "schema-tool",
            "description": "Tests explicit schema preservation",
            "fn": lambda label=None: label,
            "mcp": {"inputSchema": schema},
        }
    )

    registered = factory._server._tool_manager.get_tool("schema-tool")
    assert registered.parameters == schema


def test_registered_wrapper_keeps_ctx_annotation() -> None:
    def tool_fn(label: str, ctx: Context) -> str:
        return label

    factory = MCPToolServerFactory()
    factory.add_tool({"name": "ctx-tool", "description": "Needs the SDK context", "fn": tool_fn})
    registered = factory._server._tool_manager.get_tool("ctx-tool")
    assert registered.context_kwarg == "ctx"


def test_factory_registers_non_tool_capabilities() -> None:
    factory = MCPToolServerFactory(server_name="capability-server")

    def resource_handler() -> str:
        return "resource content"

    def prompt_handler(topic: str = "default") -> str:
        return f"prompt: {topic}"

    async def completion_handler(ref, argument, context):
        return {"values": [argument.get("value", "")]}

    factory.add_resource(
        "test://resource",
        resource_handler,
        name="resource",
        description="A production resource",
        mime_type="text/plain",
    )
    factory.add_resource(
        "test://template/{item}",
        lambda item: item,
        name="template",
        description="A production resource template",
        mime_type="text/plain",
    )
    factory.add_prompt(prompt_handler, name="prompt", description="A production prompt")
    factory.add_completion(completion_handler)

    assert [resource.name for resource in factory._server._resource_manager.list_resources()] == ["resource"]
    assert [template.name for template in factory._server._resource_manager.list_templates()] == ["template"]
    assert [prompt.name for prompt in factory._server._prompt_manager.list_prompts()] == ["prompt"]


def test_from_manifest_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        MCPToolServerFactory.from_manifest(bad)

    missing_name = tmp_path / "noname.tool.yaml"
    missing_name.write_text(
        "apiVersion: mas/v1\nkind: Tool\nmetadata: {}\nspec:\n  impl:\n    module_path: x.py\n    class_name: X\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="metadata.name"):
        MCPToolServerFactory.from_manifest(missing_name)

    no_impl = tmp_path / "noimpl.tool.yaml"
    no_impl.write_text(
        "apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: t\nspec: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="module_path"):
        MCPToolServerFactory.from_manifest(no_impl)

    no_module = tmp_path / "nomod.tool.yaml"
    no_module.write_text(
        "apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: t\n"
        "spec:\n  impl:\n    module_path: missing.py\n    class_name: X\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        MCPToolServerFactory.from_manifest(no_module)


def test_from_manifest_missing_class_name(tmp_path: Path) -> None:
    module = tmp_path / "tool.py"
    module.write_text("class SampleTool:\n    def execute(self, **kwargs):\n        return kwargs\n")
    manifest = tmp_path / "t.tool.yaml"
    manifest.write_text(
        "apiVersion: mas/v1\nkind: Tool\nmetadata:\n  name: t\nspec:\n  impl:\n    module_path: tool.py\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="class_name"):
        MCPToolServerFactory.from_manifest(manifest)


def test_run_stdio_and_http_and_rejects_unknown() -> None:
    factory = MCPToolServerFactory(server_name="demo")
    factory._server = MagicMock()
    factory.run(transport="stdio")
    factory._server.run.assert_called_with(transport="stdio")
    factory.run(transport="streamable-http", host="127.0.0.1", port=9001)
    factory._server.run.assert_called_with(transport="streamable-http", host="127.0.0.1", port=9001)
    factory.run(transport="sse", host="127.0.0.1", port=9001)
    factory._server.run.assert_called_with(transport="sse", host="127.0.0.1", port=9001)
    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        factory.run(transport="grpc")


def test_conformance_fixture_registers_2025_legacy_methods() -> None:
    server = build_server()._server._lowlevel_server

    assert server.get_request_handler("logging/setLevel") is not None
    assert server.get_request_handler("resources/subscribe") is not None
    assert server.get_request_handler("resources/unsubscribe") is not None


def test_conformance_fixture_preserves_extended_tool_schemas() -> None:
    server = build_server()._server
    manager = server._tool_manager

    json_schema = manager.get_tool("json_schema_2020_12_tool").parameters
    assert json_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert json_schema["$defs"]["address"]["$anchor"] == "addressDef"
    assert json_schema["additionalProperties"] is False
    assert json_schema["allOf"][0]["anyOf"]
    assert all(keyword in json_schema for keyword in ("if", "then", "else"))

    header_schema = manager.get_tool("custom_header_tool").parameters
    assert header_schema["properties"]["header_value"]["x-mcp-header"] == "Test-Value"

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert tools["slow_compute"].execution.task_support == "optional"
    assert tools["test_tool_with_task"].execution.task_support == "required"

    for method in ("tasks/get", "tasks/update", "tasks/cancel"):
        assert server._lowlevel_server.get_request_handler(method) is not None


def test_2026_interactive_fixtures_use_input_required_result() -> None:
    factory = build_server()
    fixtures = {tool["name"]: tool["fn"] for tool in factory._tools}
    context = MagicMock(spec=Context)
    context.request_context.session.protocol_version = "2026-07-28"
    context.input_responses = {}

    for name, arguments in (
        ("test_sampling", {"prompt": "Continue?", "ctx": context}),
        ("test_elicitation", {"message": "Continue?", "ctx": context}),
        ("test_elicitation_sep1034_defaults", {"ctx": context}),
        ("test_elicitation_sep1330_enums", {"ctx": context}),
    ):
        result = asyncio.run(fixtures[name](**arguments))
        assert isinstance(result, InputRequiredResult), name

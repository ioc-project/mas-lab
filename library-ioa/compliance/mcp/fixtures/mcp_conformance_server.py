"""Full-protocol integration fixture backed by production MCP adapters.

Tool manifests use ``MCPToolServerFactory.add_tool``. Prompts, resources,
completion, legacy request handlers, and Tasks use the corresponding factory
registration methods and ``MCPTasksExtension``. The fixture supplies only
deterministic handlers; it is not a second server implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any

import anyio
from library_ioa.plugins.mcp.contract import as_mcp_tool_result
from library_ioa.plugins.mcp.server import MCPToolServerFactory
from library_ioa.plugins.mcp.tasks import MCPTasksExtension
from mas.runtime.contracts.tool_contract import ToolResultEnvelope
from mcp.server.mcpserver import Context
from mcp.shared import inbound as mcp_inbound
from mcp.shared.exceptions import MCPError
from mcp.types import (
    Completion,
    CreateMessageRequest,
    ElicitRequest,
    EmptyResult,
    InputRequiredResult,
    ListRootsRequest,
    SamplingMessage,
    SetLevelRequestParams,
    SubscribeRequestParams,
    TextContent,
    ToolExecution,
    UnsubscribeRequestParams,
)
from pydantic import BaseModel, Field

TEST_IMAGE_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
TEST_AUDIO_BASE64 = "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAAB9AAACABAAZGF0YQIAAAA="
_decode_header_value = mcp_inbound.decode_header_value


def _decode_trimmed_header_value(value: str | None) -> str | None:
    return _decode_header_value(value.strip(" \t") if value is not None else None)


mcp_inbound.decode_header_value = _decode_trimmed_header_value
mcp_inbound.NAME_BEARING_METHODS = MappingProxyType(
    {
        **mcp_inbound.NAME_BEARING_METHODS,
        "tasks/get": "taskId",
        "tasks/update": "taskId",
        "tasks/cancel": "taskId",
    }
)


def _result(*content: dict[str, Any], is_error: bool = False) -> Any:
    return as_mcp_tool_result(ToolResultEnvelope(content=list(content), is_error=is_error))


def _text(text: str) -> Any:
    return _result({"type": "text", "text": text})


def _input_required(input_requests: dict[str, dict[str, Any]], request_state: dict[str, Any] | None = None) -> Any:
    payload: dict[str, Any] = {"resultType": "input_required", "inputRequests": input_requests}
    if request_state is not None:
        payload["requestState"] = json.dumps(request_state, separators=(",", ":"))
    return InputRequiredResult.model_validate(payload)


def _response_data(ctx: Context, key: str) -> dict[str, Any] | None:
    responses = ctx.input_responses or {}
    response = responses.get(key)
    if response is None:
        return None
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", by_alias=True)
    return response if isinstance(response, dict) else None


def _response_content(ctx: Context, key: str) -> dict[str, Any]:
    response = _response_data(ctx, key) or {}
    content = response.get("content")
    return content if isinstance(content, dict) else {}


def _uses_input_required_result(ctx: Context) -> bool:
    return ctx.request_context.session.protocol_version == "2026-07-28"


def _input_required_elicitation(ctx: Context, key: str, message: str, model: type[BaseModel]) -> Any:
    response = _response_data(ctx, key)
    if response is not None:
        content = response.get("content") or {}
        action = response.get("action", "accept")
        return _text(f"Elicitation completed: action={action}, content={json.dumps(content, separators=(',', ':'))}")

    schema = model.model_json_schema()
    return _input_required({key: _elicit_request(message, schema["properties"], schema.get("required", []))})


def _elicit_request(message: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return ElicitRequest.model_validate(
        {
            "method": "elicitation/create",
            "params": {
                "message": message,
                "requestedSchema": {"type": "object", "properties": properties, "required": required},
            },
        }
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


def _sampling_request(prompt: str, max_tokens: int = 100) -> dict[str, Any]:
    return CreateMessageRequest.model_validate(
        {
            "method": "sampling/createMessage",
            "params": {
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt}}],
                "maxTokens": max_tokens,
            },
        }
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


def _roots_request() -> dict[str, Any]:
    return ListRootsRequest().model_dump(mode="json", by_alias=True, exclude_none=True)


async def _finish_task(record: Any, delay: float) -> None:
    if record.name == "protocol_error_job":
        record.status = "failed"
        record.error = {"code": -32603, "message": "Protocol task failed"}
    elif record.name == "failing_job":
        record.status = "completed"
        record.result = {
            "resultType": "complete",
            "content": [{"type": "text", "text": "Tool execution failed"}],
            "isError": True,
        }


def _task_input_requests(params: Any, arguments: dict[str, Any]) -> Any:
    if params.name == "test_tool_with_task":
        if not params.input_responses:
            return _input_required(
                {"user_name": _elicit_request("What is your name?", {"name": {"type": "string"}}, ["name"])},
                {"kind": "task-after-input"},
            )
        response = (params.input_responses or {}).get("user_name")
        content = getattr(response, "content", None)
        if isinstance(content, dict) and "name" in content:
            arguments["user_name"] = content["name"]
    return None


def build_server() -> MCPToolServerFactory:
    tasks = MCPTasksExtension(
        {
            "slow_compute": "optional",
            "failing_job": "required",
            "protocol_error_job": "optional",
            "confirm_delete": "optional",
            "multi_input": "optional",
            "test_tool_with_task": "required",
        },
        finish=_finish_task,
        input_requests=_task_input_requests,
    )
    factory = MCPToolServerFactory(server_name="mas-mcp-conformance", extensions=[tasks])
    legacy_log_levels: dict[int, str] = {}
    legacy_resource_subscriptions: dict[str, dict[int, Any]] = {}

    def add(name: str, fn: Any, description: str, *, input_schema: dict[str, Any] | None = None) -> None:
        mcp = {"inputSchema": input_schema} if input_schema is not None else {}
        execution = ToolExecution(taskSupport=tasks.task_tools[name]) if name in tasks.task_tools else None
        factory.add_tool(
            {
                "name": name,
                "description": description,
                "fn": fn,
                "parameters": [],
                "mcp": mcp,
                "execution": execution,
            }
        )

    def simple_text() -> Any:
        return _text("This is a simple text response for testing.")

    def image_content() -> Any:
        return _result({"type": "image", "data": TEST_IMAGE_BASE64, "mimeType": "image/png"})

    def audio_content() -> Any:
        return _result({"type": "audio", "data": TEST_AUDIO_BASE64, "mimeType": "audio/wav"})

    def embedded_resource() -> Any:
        return _result(
            {
                "type": "resource",
                "resource": {
                    "uri": "test://embedded-resource",
                    "mimeType": "text/plain",
                    "text": "This is an embedded resource content.",
                },
            }
        )

    def multiple_content_types() -> Any:
        return _result(
            {"type": "text", "text": "Multiple content types test:"},
            {"type": "image", "data": TEST_IMAGE_BASE64, "mimeType": "image/png"},
            {
                "type": "resource",
                "resource": {
                    "uri": "test://mixed-content-resource",
                    "mimeType": "application/json",
                    "text": json.dumps({"test": "data", "value": 123}, separators=(",", ":")),
                },
            },
        )

    async def tool_with_logging(ctx: Context) -> Any:
        levels = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")
        threshold = legacy_log_levels.get(id(ctx.request_context.session), "info")
        if levels.index("info") >= levels.index(threshold):
            await ctx.info("Tool execution started")
        await anyio.sleep(0.05)
        if levels.index("info") >= levels.index(threshold):
            await ctx.info("Tool processing data")
        await anyio.sleep(0.05)
        if levels.index("info") >= levels.index(threshold):
            await ctx.info("Tool execution completed")
        return _text("Tool with logging executed successfully")

    async def tool_with_progress(ctx: Context) -> Any:
        for progress in (0, 50, 100):
            await ctx.report_progress(progress, 100, f"Completed step {progress} of 100")
            if progress != 100:
                await anyio.sleep(0.05)
        return _text("Progress completed")

    def error_handling() -> Any:
        return _result(
            {"type": "text", "text": "This tool intentionally returns an error for testing"},
            is_error=True,
        )

    def missing_capability(ctx: Context) -> Any:
        raise MCPError(
            code=-32021,
            message="Missing required client capability",
            data={"requiredCapabilities": {"sampling": {}}},
        )

    def json_schema_2020_12_tool(**arguments: Any) -> Any:
        return _text(json.dumps(arguments, separators=(",", ":")))

    def custom_header_tool(header_value: str) -> Any:
        return _text(header_value)

    def test_reconnection() -> Any:
        return _text("Reconnection test completed")

    def greet(name: str) -> Any:
        return _text(f"Hello, {name}!")

    async def slow_compute(seconds: float, label: str = "result") -> Any:
        await asyncio.sleep(min(seconds, 0.2))
        return _text(f"Computed {label}")

    async def failing_job() -> Any:
        await asyncio.sleep(0.05)
        return _result({"type": "text", "text": "Tool execution failed"}, is_error=True)

    async def protocol_error_job() -> Any:
        await asyncio.sleep(0.05)
        raise RuntimeError("Protocol task failed")

    def confirm_delete(filename: str) -> Any:
        return _text(f"Deleted {filename}")

    def multi_input() -> Any:
        return _text("Inputs accepted")

    def test_tool_with_task() -> Any:
        return _text("Task-only tool completed")

    async def sampling(prompt: str, ctx: Context) -> Any:
        if _uses_input_required_result(ctx):
            return await input_sampling(ctx)
        response = await ctx.session.create_message(
            messages=[SamplingMessage(role="user", content=TextContent(text=prompt))],
            max_tokens=100,
            related_request_id=ctx.request_id,
        )
        content = getattr(response, "content", None)
        text = getattr(content, "text", None) or "No response"
        return _text(f"LLM response: {text}")

    class ElicitationInput(BaseModel):
        response: str = Field(description="User's response")

    async def elicitation(message: str, ctx: Context) -> Any:
        if _uses_input_required_result(ctx):
            return _input_required_elicitation(ctx, "user_response", message, ElicitationInput)
        response = await ctx.elicit(message, ElicitationInput)
        content = response.data.model_dump(mode="json") if response.data is not None else {}
        return _text(f"User response: action={response.action}, content={json.dumps(content, separators=(',', ':'))}")

    class DefaultsInput(BaseModel):
        name: str = "John Doe"
        age: int = 30
        score: float = 95.5
        status: str = Field(default="active", json_schema_extra={"enum": ["active", "inactive", "pending"]})
        verified: bool = True

    async def elicitation_defaults(ctx: Context) -> Any:
        if _uses_input_required_result(ctx):
            return _input_required_elicitation(
                ctx,
                "defaults",
                "Please review and update the form fields with defaults",
                DefaultsInput,
            )
        response = await ctx.elicit("Please review and update the form fields with defaults", DefaultsInput)
        content = response.data.model_dump(mode="json") if response.data is not None else {}
        serialized = json.dumps(content, separators=(",", ":"))
        return _text(f"Elicitation completed: action={response.action}, content={serialized}")

    class EnumsInput(BaseModel):
        untitledSingle: str = Field(json_schema_extra={"enum": ["option1", "option2", "option3"]})
        titledSingle: str = Field(
            json_schema_extra={
                "oneOf": [
                    {"const": "value1", "title": "First Option"},
                    {"const": "value2", "title": "Second Option"},
                    {"const": "value3", "title": "Third Option"},
                ]
            }
        )
        legacyEnum: str = Field(
            json_schema_extra={
                "enum": ["opt1", "opt2", "opt3"],
                "enumNames": ["Option One", "Option Two", "Option Three"],
            }
        )
        untitledMulti: list[str] = Field(
            min_length=1,
            max_length=3,
            json_schema_extra={"items": {"type": "string", "enum": ["option1", "option2", "option3"]}},
        )
        titledMulti: list[str] = Field(
            min_length=1,
            max_length=3,
            json_schema_extra={
                "items": {
                    "anyOf": [
                        {"const": "value1", "title": "First Choice"},
                        {"const": "value2", "title": "Second Choice"},
                        {"const": "value3", "title": "Third Choice"},
                    ]
                }
            },
        )

    async def elicitation_enums(ctx: Context) -> Any:
        if _uses_input_required_result(ctx):
            return _input_required_elicitation(
                ctx,
                "enum_options",
                "Please select options from the enum fields",
                EnumsInput,
            )
        response = await ctx.elicit("Please select options from the enum fields", EnumsInput)
        content = response.data.model_dump(mode="json") if response.data is not None else {}
        serialized = json.dumps(content, separators=(",", ":"))
        return _text(f"Elicitation completed: action={response.action}, content={serialized}")

    async def input_elicitation(ctx: Context) -> Any:
        content = _response_content(ctx, "user_name")
        if "name" in content:
            return _text(f"Hello, {content['name']}!")
        return _input_required(
            {"user_name": _elicit_request("What is your name?", {"name": {"type": "string"}}, ["name"])}
        )

    async def input_sampling(ctx: Context) -> Any:
        response = _response_data(ctx, "sample_request")
        if response is not None:
            content = response.get("content") or {}
            text = content.get("text", "no response") if isinstance(content, dict) else "no response"
            return _text(f"Sampling result: {text}")
        return _input_required({"sample_request": _sampling_request("What is the capital of France?")})

    async def input_roots(ctx: Context) -> Any:
        response = _response_data(ctx, "roots_request")
        if response is not None:
            roots = response.get("roots") or []
            return _text(f"Found {len(roots)} root(s)")
        return _input_required({"roots_request": _roots_request()})

    async def input_request_state(ctx: Context) -> Any:
        content = _response_content(ctx, "confirm")
        if ctx.request_state and content.get("ok") is True:
            state = json.loads(ctx.request_state)
            if state.get("kind") == "request-state":
                return _text("state-ok: requestState validated")
        return _input_required(
            {"confirm": _elicit_request("Please confirm", {"ok": {"type": "boolean"}}, ["ok"])},
            {"kind": "request-state"},
        )

    async def input_multiple(ctx: Context) -> Any:
        if ctx.request_state and all(_response_data(ctx, key) for key in ("user_name", "greeting", "client_roots")):
            return _text("Multiple inputs completed")
        return _input_required(
            {
                "user_name": _elicit_request("What is your name?", {"name": {"type": "string"}}, ["name"]),
                "greeting": _sampling_request("Generate a greeting", 50),
                "client_roots": _roots_request(),
            },
            {"kind": "multiple-inputs"},
        )

    async def input_multi_round(ctx: Context) -> Any:
        state = json.loads(ctx.request_state) if ctx.request_state else {}
        if state.get("round") == 2 and _response_data(ctx, "step2"):
            return _text("Multi-round complete")
        if state.get("round") == 1 and _response_data(ctx, "step1"):
            return _input_required(
                {
                    "step2": _elicit_request(
                        "Step 2: What is your favorite color?", {"color": {"type": "string"}}, ["color"]
                    )
                },
                {"round": 2},
            )
        return _input_required(
            {"step1": _elicit_request("Step 1: What is your name?", {"name": {"type": "string"}}, ["name"])},
            {"round": 1},
        )

    async def input_tampered_state(ctx: Context) -> Any:
        if ctx.request_state and _response_data(ctx, "confirm"):
            return _text("integrity-ok: state verified")
        return _input_required(
            {"confirm": _elicit_request("Please confirm", {"ok": {"type": "boolean"}}, ["ok"])},
            {"kind": "tamper-test"},
        )

    async def input_capabilities(ctx: Context) -> Any:
        if ctx.input_responses:
            return _text(f"capabilities-ok: received {','.join(ctx.input_responses)}")
        requests: dict[str, dict[str, Any]] = {}
        capabilities = ctx.client_capabilities
        if capabilities and capabilities.elicitation is not None:
            requests["elicit_input"] = _elicit_request("Elicitation input", {"value": {"type": "string"}}, ["value"])
        if capabilities and capabilities.sampling is not None:
            requests["sample_input"] = _sampling_request("Sample request", 50)
        if not requests:
            return _text("No supported capabilities declared")
        return _input_required(requests, {"kind": "capabilities-test"})

    fixtures = {
        "test_simple_text": (simple_text, "Tests simple text content response"),
        "test_image_content": (image_content, "Tests image content response"),
        "test_audio_content": (audio_content, "Tests audio content response"),
        "test_embedded_resource": (embedded_resource, "Tests embedded resource content response"),
        "test_multiple_content_types": (multiple_content_types, "Tests multiple content types"),
        "test_tool_with_logging": (tool_with_logging, "Tests logging notifications"),
        "test_tool_with_progress": (tool_with_progress, "Tests progress notifications"),
        "test_error_handling": (error_handling, "Tests tool error responses"),
        "test_missing_capability": (missing_capability, "Tests rejection of undeclared client capabilities"),
        "test_reconnection": (test_reconnection, "Tests SSE polling reconnection"),
        "greet": (greet, "Returns a greeting synchronously"),
        "slow_compute": (slow_compute, "Computes a labeled result as a task"),
        "failing_job": (failing_job, "Completes a task with a tool error"),
        "protocol_error_job": (protocol_error_job, "Fails a task with a protocol error"),
        "confirm_delete": (confirm_delete, "Requests confirmation before deleting a file"),
        "multi_input": (multi_input, "Requests multiple independent inputs"),
        "test_tool_with_task": (test_tool_with_task, "Requires task execution support"),
        "test_sampling": (sampling, "Tests server-initiated sampling"),
        "test_elicitation": (elicitation, "Tests server-initiated elicitation"),
        "test_elicitation_sep1034_defaults": (elicitation_defaults, "Tests elicitation defaults"),
        "test_elicitation_sep1330_enums": (elicitation_enums, "Tests elicitation enum schemas"),
        "test_input_required_result_elicitation": (input_elicitation, "Tests MRTR elicitation"),
        "test_input_required_result_sampling": (input_sampling, "Tests MRTR sampling"),
        "test_input_required_result_list_roots": (input_roots, "Tests MRTR roots"),
        "test_input_required_result_request_state": (input_request_state, "Tests MRTR request state"),
        "test_input_required_result_multiple_inputs": (input_multiple, "Tests multiple MRTR inputs"),
        "test_input_required_result_multi_round": (input_multi_round, "Tests multiple MRTR rounds"),
        "test_input_required_result_tampered_state": (input_tampered_state, "Tests MRTR state integrity"),
        "test_input_required_result_capabilities": (input_capabilities, "Tests MRTR capability filtering"),
    }
    for name, (fn, description) in fixtures.items():
        add(name, fn, description)

    add(
        "json_schema_2020_12_tool",
        json_schema_2020_12_tool,
        "Tool with JSON Schema 2020-12 features",
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "$defs": {
                "address": {
                    "$anchor": "addressDef",
                    "type": "object",
                    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
                }
            },
            "properties": {
                "name": {"type": "string"},
                "address": {"$ref": "#/$defs/address"},
                "contactMethod": {"type": "string", "enum": ["phone", "email"]},
                "phone": {"type": "string"},
                "email": {"type": "string"},
            },
            "allOf": [{"anyOf": [{"required": ["phone"]}, {"required": ["email"]}]}],
            "if": {"properties": {"contactMethod": {"const": "phone"}}, "required": ["contactMethod"]},
            "then": {"required": ["phone"]},
            "else": {"required": ["email"]},
            "additionalProperties": False,
        },
    )
    add(
        "custom_header_tool",
        custom_header_tool,
        "Tests custom MCP parameter headers",
        input_schema={
            "type": "object",
            "properties": {"header_value": {"type": "string", "x-mcp-header": "Test-Value"}},
            "required": ["header_value"],
        },
    )

    def require_legacy_protocol(request_context: Any) -> None:
        if request_context.session.protocol_version == "2026-07-28":
            raise MCPError(code=-32601, message="Method not found")

    async def set_logging_level(request_context: Any, params: SetLevelRequestParams) -> EmptyResult:
        require_legacy_protocol(request_context)
        legacy_log_levels[id(request_context.session)] = params.level
        return EmptyResult()

    async def subscribe_resource(request_context: Any, params: SubscribeRequestParams) -> EmptyResult:
        require_legacy_protocol(request_context)
        legacy_resource_subscriptions.setdefault(params.uri, {})[id(request_context.session)] = request_context.session
        return EmptyResult()

    async def unsubscribe_resource(request_context: Any, params: UnsubscribeRequestParams) -> EmptyResult:
        require_legacy_protocol(request_context)
        subscribers = legacy_resource_subscriptions.get(params.uri)
        if subscribers is not None:
            subscribers.pop(id(request_context.session), None)
            if not subscribers:
                legacy_resource_subscriptions.pop(params.uri, None)
        return EmptyResult()

    factory.add_request_handler("logging/setLevel", SetLevelRequestParams, set_logging_level)
    factory.add_request_handler("resources/subscribe", SubscribeRequestParams, subscribe_resource)
    factory.add_request_handler("resources/unsubscribe", UnsubscribeRequestParams, unsubscribe_resource)

    async def trigger_resource_update(ctx: Context) -> Any:
        uri = "test://watched-resource"
        for session in legacy_resource_subscriptions.get(uri, {}).values():
            await session.send_resource_updated(uri)
        await ctx.notify_resource_updated(uri)
        return _text("Resource update triggered")

    add("test_trigger_resource_update", trigger_resource_update, "Triggers a resource update notification")

    def static_text() -> str:
        return "This is the content of the static text resource."

    factory.add_resource(
        "test://static-text",
        static_text,
        name="static-text",
        title="Static Text Resource",
        description="A static text resource for testing",
        mime_type="text/plain",
    )

    def static_binary() -> bytes:
        import base64

        return base64.b64decode(TEST_IMAGE_BASE64)

    factory.add_resource(
        "test://static-binary",
        static_binary,
        name="static-binary",
        title="Static Binary Resource",
        description="A static binary resource for testing",
        mime_type="image/png",
    )

    def template(id: str) -> str:
        return json.dumps({"id": id, "templateTest": True, "data": f"Data for ID: {id}"}, separators=(",", ":"))

    factory.add_resource(
        "test://template/{id}/data",
        template,
        name="template",
        title="Resource Template",
        description="A resource template with parameter substitution",
        mime_type="application/json",
    )

    def watched_resource() -> str:
        return "Watched resource content"

    factory.add_resource(
        "test://watched-resource",
        watched_resource,
        name="watched-resource",
        title="Watched Resource",
        description="A resource that can be subscribed to",
        mime_type="text/plain",
    )

    def simple_prompt() -> str:
        return "This is a simple prompt for testing."

    factory.add_prompt(
        simple_prompt,
        title="Simple Test Prompt",
        description="A simple prompt without arguments",
        name="test_simple_prompt",
    )

    def prompt_with_arguments(arg1: str, arg2: str) -> str:
        return f"Prompt with arguments: arg1='{arg1}', arg2='{arg2}'"

    factory.add_prompt(
        prompt_with_arguments,
        title="Prompt With Arguments",
        description="A prompt with required arguments",
        name="test_prompt_with_arguments",
    )

    def prompt_with_embedded_resource(resourceUri: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": {
                    "type": "resource",
                    "resource": {
                        "uri": resourceUri,
                        "mimeType": "text/plain",
                        "text": "Embedded resource content for testing.",
                    },
                },
            },
            {"role": "user", "content": {"type": "text", "text": "Please process the embedded resource above."}},
        ]

    factory.add_prompt(
        prompt_with_embedded_resource,
        title="Prompt With Embedded Resource",
        description="A prompt that includes an embedded resource",
        name="test_prompt_with_embedded_resource",
    )

    def prompt_with_image() -> list[dict[str, Any]]:
        return [
            {"role": "user", "content": {"type": "image", "data": TEST_IMAGE_BASE64, "mimeType": "image/png"}},
            {"role": "user", "content": {"type": "text", "text": "Please analyze the image above."}},
        ]

    factory.add_prompt(
        prompt_with_image,
        title="Prompt With Image",
        description="A prompt that includes image content",
        name="test_prompt_with_image",
    )

    def input_required_prompt(ctx: Context) -> Any:
        content = _response_content(ctx, "user_context")
        if "context" in content:
            return f"Prompt with context: {content['context']}"
        return _input_required(
            {
                "user_context": _elicit_request(
                    "What context should the prompt use?", {"context": {"type": "string"}}, ["context"]
                )
            }
        )

    factory.add_prompt(
        input_required_prompt,
        title="Input Required Prompt",
        description="A prompt that requires elicitation input",
        name="test_input_required_result_prompt",
    )

    async def complete(ref: Any, argument: Any, context: Any) -> Completion:
        return Completion(values=[], total=0, hasMore=False)

    factory.add_completion(complete)

    return factory


def serve() -> None:
    build_server().run(
        transport="streamable-http",
        host=os.environ.get("MCP_CONFORMANCE_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_CONFORMANCE_PORT", "9002")),
        json_response=False,
        stateless_http=False,
    )


def run_conformance() -> None:
    host = os.environ.get("MCP_CONFORMANCE_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_CONFORMANCE_PORT", "9002"))
    version = os.environ.get("MCP_CONFORMANCE_VERSION", "0.2.0-alpha.11")
    results = Path(os.environ.get("MCP_CONFORMANCE_RESULTS", "/tmp/mas-mcp-conformance"))
    url = f"http://{host}:{port}/mcp"
    server = subprocess.Popen([sys.executable, __file__], env=os.environ.copy())
    try:
        deadline = time.monotonic() + 10
        while True:
            if server.poll() is not None:
                raise RuntimeError(f"MCP conformance fixture exited with code {server.returncode}")
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"MCP conformance fixture did not listen on {host}:{port}") from None
                time.sleep(0.05)

        for revision in ("2025-11-25", "2026-07-28"):
            output_dir = results / revision
            output_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "npx",
                    "--yes",
                    f"@modelcontextprotocol/conformance@{version}",
                    "server",
                    "--url",
                    url,
                    "--requirements",
                    revision,
                    "--output-dir",
                    str(output_dir),
                ],
                check=True,
            )
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main() -> None:
    if "--run-conformance" in sys.argv[1:]:
        run_conformance()
    else:
        serve()


if __name__ == "__main__":
    main()

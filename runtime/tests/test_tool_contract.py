#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.runtime.contracts.tool_contract import (
    ToolContract,
    ToolResultEnvelope,
    invoke_call_tool,
    overlay_tool_advertise,
)
from mas.runtime.manifest.schema.tool import ToolDocument


class _DailyTool(ToolContract):
    def get_name(self) -> str:
        return "calc"

    def get_description(self) -> str:
        return "add"

    def get_parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"a": {"type": "integer"}}}

    def execute(self, **kwargs):
        return {"sum": kwargs["a"]}


class _LegacyCallTool(ToolContract):
    def get_name(self) -> str:
        return "legacy"

    def call_tool(self, tool_name: str, arguments: dict) -> dict:  # no **kwargs
        return {"tool": tool_name, "arguments": arguments, "ok": True}

    def list_tools(self) -> list[dict]:
        return [{"name": "legacy", "description": "", "parameters": {}}]


class _RichTool(_DailyTool):
    def get_title(self) -> str:
        return "Calculator"

    def get_output_schema(self) -> dict:
        return {"type": "object", "properties": {"sum": {"type": "integer"}}}

    def is_idempotent(self) -> bool:
        return True

    def is_read_only(self) -> bool:
        return True

    def get_task_support(self) -> str:
        return "optional"


class _SemanticTool(_DailyTool):
    def get_semantics(self) -> dict:
        return {"concept": "calc", "op": "evaluate", "subject_arg": "a"}


def test_daily_list_tools_stays_three_keys() -> None:
    spec = _DailyTool().list_tools()[0]
    assert spec == {
        "name": "calc",
        "description": "add",
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}},
    }


def test_optional_advertise_fields_emitted_only_when_set() -> None:
    spec = _RichTool().list_tools()[0]
    assert spec["title"] == "Calculator"
    assert spec["output_schema"]["properties"]["sum"]["type"] == "integer"
    assert spec["idempotent"] is True
    assert spec["read_only"] is True
    assert spec["task_support"] == "optional"
    assert spec["annotations"]["idempotentHint"] is True
    assert spec["annotations"]["readOnlyHint"] is True
    assert "destructive" not in spec
    assert "icons" not in spec


def test_semantics_advertise_from_getter() -> None:
    spec = _SemanticTool().list_tools()[0]
    assert spec["semantics"] == {"concept": "calc", "op": "evaluate", "subject_arg": "a"}


def test_call_tool_name_and_arguments_still_work() -> None:
    assert _DailyTool().call_tool("calc", {"a": 2}) == {"sum": 2}


def test_invoke_call_tool_drops_options_legacy_signature() -> None:
    tool = _LegacyCallTool()
    out = invoke_call_tool(
        tool.call_tool,
        "legacy",
        {"x": 1},
        timeout_seconds=5,
        progress_callback=lambda *_: None,
        ctx=object(),
        user="u",
    )
    assert out == {"tool": "legacy", "arguments": {"x": 1}, "ok": True}


def test_invoke_call_tool_forwards_declared_kwargs() -> None:
    seen = {}

    def _fn(tool_name, arguments, *, timeout_seconds=None, **kwargs):
        seen["timeout_seconds"] = timeout_seconds
        seen["kwargs"] = kwargs
        return {"ok": True}

    invoke_call_tool(_fn, "t", {}, timeout_seconds=3, extra="keep")
    assert seen["timeout_seconds"] == 3
    assert seen["kwargs"]["extra"] == "keep"


def test_envelope_optional_result_fields_omitted_when_unset() -> None:
    data = ToolResultEnvelope.inline({"v": 1}).to_dict()
    assert data["result"] == {"v": 1}
    assert data["is_error"] is False
    assert "content" not in data
    assert "structured_content" not in data
    assert "result_type" not in data
    assert "meta" not in data


def test_envelope_optional_result_fields_when_set() -> None:
    data = ToolResultEnvelope(
        result="ok",
        content=[{"type": "image", "data": "x", "mime_type": "image/png"}],
        structured_content={"k": 1},
        result_type="input_required",
        meta={"elicit": True},
    ).to_dict()
    assert data["content"][0]["type"] == "image"
    assert data["structured_content"] == {"k": 1}
    assert data["result_type"] == "input_required"
    assert data["meta"] == {"elicit": True}


def test_overlay_copies_optional_yaml_attributes() -> None:
    merged = overlay_tool_advertise(
        {"name": "calc", "description": "runtime", "parameters": {}},
        {
            "name": "calc",
            "description": "yaml",
            "parameters": {"type": "object"},
            "title": "Calculator",
            "read_only": True,
            "timeout_seconds": 10,
            "semantics": {"concept": "memory", "op": "read", "subject_arg": "query"},
        },
    )
    assert merged["description"] == "yaml"
    assert merged["parameters"] == {"type": "object"}
    assert merged["title"] == "Calculator"
    assert merged["read_only"] is True
    assert merged["timeout_seconds"] == 10
    assert merged["semantics"] == {"concept": "memory", "op": "read", "subject_arg": "query"}


def test_tool_document_optional_spec_fields_round_trip() -> None:
    doc = ToolDocument.from_dict(
        {
            "apiVersion": "mas/v1",
            "kind": "Tool",
            "metadata": {"name": "web-search", "description": "label"},
            "spec": {
                "description": "Search the web",
                "title": "Web Search",
                "read_only": True,
                "open_world": True,
                "destructive": False,
                "task_support": "optional",
                "icons": [{"src": "https://example.invalid/i.png"}],
                "output_schema": {"type": "object"},
                "meta": {"vendor": "ioa"},
                "semantics": {"concept": "search", "op": "query", "subject_arg": "query"},
                "idempotent": True,
                "impl": {"module_path": "./x.py", "class_name": "X"},
            },
        }
    )
    contract = doc.to_contract_dict("web-search")
    assert contract["title"] == "Web Search"
    assert contract["read_only"] is True
    assert contract["open_world"] is True
    assert contract["destructive"] is False
    assert contract["task_support"] == "optional"
    assert contract["output_schema"] == {"type": "object"}
    assert contract["meta"] == {"vendor": "ioa"}
    assert contract["semantics"] == {"concept": "search", "op": "query", "subject_arg": "query"}
    advertised = doc.spec.to_tool_server_spec("web-search")
    assert advertised["title"] == "Web Search"
    assert advertised["annotations"]["readOnlyHint"] is True
    assert advertised["annotations"]["openWorldHint"] is True
    assert advertised["annotations"]["destructiveHint"] is False
    assert advertised["execution"]["taskSupport"] == "optional"
    assert advertised["_meta"] == {"vendor": "ioa"}


def test_tool_fixture_reads_by_tool_then_wildcard():
    from types import SimpleNamespace

    import pytest
    from mas.runtime.contracts import tool_fixture

    ctx = SimpleNamespace(tool_fixtures={"by_tool": {"get_logs": {"a": 1}, "*": {"b": 2}}})
    assert tool_fixture(ctx, "get_logs") == {"a": 1}
    assert tool_fixture(ctx, "get_metrics") == {"b": 2}
    assert tool_fixture(SimpleNamespace(tool_fixtures={"by_tool": {}}), "x") is None
    assert tool_fixture(SimpleNamespace(), "x") is None
    assert tool_fixture(None, "x") is None
    with pytest.raises(TypeError):
        tool_fixture(SimpleNamespace(tool_fixtures={"c": 3}), "x")

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Opt-in structured return: wrap layer plus protocol-side validation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mas.library.standard.plugins.layers.structured import StructuredLayer
from mas.library.standard.plugins.llm.structured import apply_structured_result, json_schema_format, schema_from_format
from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL, _provider_result_return
from mas.runtime.reliability.classes import ClassifiedFailure
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

_SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


class _Inner:
    def __init__(self, content: str = '{"n": 1}') -> None:
        self.content = content
        self.seen: dict | None = None

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        payload = _PROVIDER_CALL.get()
        self.seen = dict(payload) if isinstance(payload, dict) else None
        result = {"role": "assistant", "content": self.content}
        return _provider_result_return(io, payload, result)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)


def _io() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="LLM_CALL")


def test_schema_from_format_shapes() -> None:
    assert schema_from_format(None) is None
    assert schema_from_format({}) is None
    assert schema_from_format({"foo": 1}) is None
    assert schema_from_format({"json_schema": {"name": "r"}}) is None
    assert schema_from_format({"type": "json_object"}) == {"type": "object"}
    assert schema_from_format({"schema": _SCHEMA}) == _SCHEMA
    assert schema_from_format({"type": "json_schema", "json_schema": {"name": "r", "schema": _SCHEMA}}) == _SCHEMA
    assert schema_from_format({"type": "object", "properties": {"n": {"type": "integer"}}})["type"] == "object"
    assert json_schema_format(_SCHEMA)["json_schema"]["schema"] == _SCHEMA


def test_apply_structured_result_validates() -> None:
    out = apply_structured_result({"content": '{"n": 1}'}, _SCHEMA)
    assert out["parsed"] == {"n": 1}
    with pytest.raises(ClassifiedFailure, match="not JSON"):
        apply_structured_result({"content": "nope"}, _SCHEMA)
    with pytest.raises(ClassifiedFailure, match="does not match"):
        apply_structured_result({"content": '{"n": "x"}'}, _SCHEMA)
    with pytest.raises(ClassifiedFailure, match="no JSON"):
        apply_structured_result({"content": ""}, _SCHEMA)
    parsed = apply_structured_result({"content": {"n": 2}}, _SCHEMA)
    assert parsed["parsed"] == {"n": 2}
    assert '"n": 2' in parsed["content"]
    with pytest.raises(ClassifiedFailure, match="invalid schema"):
        apply_structured_result({"content": "{}"}, {"type": "nope"})
    fenced = apply_structured_result({"content": '```json\n{"n": 1}\n```'}, _SCHEMA)
    assert fenced["parsed"] == {"n": 1}
    parts = apply_structured_result({"content": [{"type": "text", "text": '{"n": 1}'}]}, _SCHEMA)
    assert parts["parsed"] == {"n": 1}
    array_schema = {"type": "array", "items": {"type": "object"}}
    already = apply_structured_result(
        {"content": [{"type": "text", "text": "hello"}]},
        array_schema,
    )
    assert already["parsed"] == [{"type": "text", "text": "hello"}]


def test_apply_skips_a_tool_call_turn() -> None:
    message = {
        "content": None,
        "finish_reason": "tool_calls",
        "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}],
    }
    out = apply_structured_result(message, _SCHEMA)
    assert "parsed" not in out
    assert out["tool_calls"][0]["id"] == "t1"


def test_wrap_injects_response_format_and_returns_parsed() -> None:
    inner = _Inner()
    layer = StructuredLayer.wrap(inner, {"schema": _SCHEMA, "name": "score"})
    payload = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    token = _PROVIDER_CALL.set(payload)
    try:
        ret = asyncio.run(layer.ainvoke(_io()))
        assert payload["response_format"]["json_schema"]["name"] == "score"
        assert payload["_result"]["parsed"] == {"n": 1}
        assert '"parsed"' in ret.text
        payload.pop("_result")
        ret = asyncio.run(layer.ainvoke(_io()))
        assert payload["_result"]["parsed"] == {"n": 1}
    finally:
        _PROVIDER_CALL.reset(token)


def test_wrap_is_noop_without_schema() -> None:
    inner = _Inner("plain")
    layer = StructuredLayer.wrap(inner, {})
    payload = {"model": "m", "messages": []}
    token = _PROVIDER_CALL.set(payload)
    try:
        ret = asyncio.run(layer.ainvoke(_io()))
        assert payload["_result"]["content"] == "plain"
        assert "parsed" not in payload["_result"]
        assert ret.finish_reason == ""
    finally:
        _PROVIDER_CALL.reset(token)


def test_wrap_leaves_a_tool_call_turn() -> None:
    class _Tools(_Inner):
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            payload = _PROVIDER_CALL.get()
            result = {
                "role": "assistant",
                "content": None,
                "finish_reason": "tool_calls",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}],
            }
            return _provider_result_return(io, payload, result)

    layer = StructuredLayer.wrap(_Tools(), {"schema": _SCHEMA})
    payload = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    token = _PROVIDER_CALL.set(payload)
    try:
        ret = asyncio.run(layer.ainvoke(_io()))
        assert "parsed" not in payload["_result"]
        assert payload["_result"]["tool_calls"][0]["id"] == "t1"
        assert ret.finish_reason == "tool_calls"
    finally:
        _PROVIDER_CALL.reset(token)


def test_wrap_skips_non_llm_ops() -> None:
    async def _ainvoke(io: InvokeEngineIo) -> EngineIoReturn:
        del io
        return EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP", text="ok")

    inner = SimpleNamespace(
        invoke=lambda io: EngineIoReturn(
            correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="ok"
        ),
        ainvoke=_ainvoke,
    )
    layer = StructuredLayer.wrap(
        inner, {"schema": _SCHEMA, "response_format": {"type": "json_object"}}, call_kinds=("llm",)
    )
    out = asyncio.run(layer.ainvoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL")))
    assert out.text == "ok"
    assert asyncio.run(layer.ainvoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL"))).text == "ok"


def test_parse_json_content_treats_part_list_as_wrapped_json() -> None:
    from mas.library.standard.plugins.llm.structured import parse_json_content

    assert parse_json_content([{"type": "text", "text": '{"n": 1}'}]) == {"n": 1}
    image = {
        "type": "image",
        "media_type": "image/png",
        "sha256": "abc",
        "name": "chart.png",
        "bytes": 4,
    }
    with pytest.raises(ClassifiedFailure):
        parse_json_content([{"type": "text", "text": "see"}, image])
    assert parse_json_content([1, 2, 3]) == [1, 2, 3]
    assert parse_json_content([{"type": "tool_use"}]) == [{"type": "tool_use"}]


def test_inject_and_validate_without_a_payload() -> None:
    from mas.library.standard.plugins.layers.structured import _inject, _validate

    assert _inject(_SCHEMA, "r") is None
    ret = EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP", text="x")
    assert _validate(_io(), None, _SCHEMA, ret) is ret

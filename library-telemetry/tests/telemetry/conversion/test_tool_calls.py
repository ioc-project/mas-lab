#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""tool_calls_text: completion text for a tool-use turn."""

from __future__ import annotations

from mas.library.telemetry.conversion.mappings.execution import h_llm_call_end
from mas.library.telemetry.conversion.tool_calls import MAX_ARGS_CHARS, tool_calls_text


def test_runtime_openai_and_flat_shapes() -> None:
    calls = [
        {"toolName": "lookup", "arguments": {"id": "1"}},
        {"function": {"name": "search", "arguments": '{"q": "x"}'}},
        {"name": "flat"},
    ]
    assert tool_calls_text(calls) == '→ lookup({"id": "1"})\n→ search({"q": "x"})\n→ flat({})'


def test_non_lists_and_non_dicts_render_nothing() -> None:
    assert tool_calls_text(None) == ""
    assert tool_calls_text("x") == ""
    assert tool_calls_text(["x", 3]) == ""


def test_arguments_are_cut_and_unserializable_ones_survive() -> None:
    long = tool_calls_text([{"name": "t", "arguments": "a" * 1000}])
    assert long == f"→ t({'a' * MAX_ARGS_CHARS})"
    assert tool_calls_text([{"name": "t", "arguments": {"s": {1, 2}}}]).startswith("→ t(")


class _Conv:
    """Just enough of SpanEmitter for h_llm_call_end."""

    def __init__(self) -> None:
        self.closed: dict = {}

    def ts_ns(self, ev):
        return 0

    def is_closed(self, call_id):
        return False

    def is_open(self, call_id):
        return True

    def close_span(self, call_id, extra, **_):
        self.closed = extra


def test_llm_call_end_keeps_output_and_appends_tool_calls() -> None:
    conv = _Conv()
    h_llm_call_end(conv, {"call_id": "c", "output": "ok", "tool_calls": [{"toolName": "t", "arguments": {}}]})
    assert conv.closed["mas.llm.response"] == "ok\n→ t({})"
    h_llm_call_end(conv, {"call_id": "c", "output": "", "tool_calls": [{"toolName": "t", "arguments": {}}]})
    assert conv.closed["mas.llm.response"] == "→ t({})"
    h_llm_call_end(conv, {"call_id": "c", "output": "plain"})
    assert conv.closed["mas.llm.response"] == "plain"

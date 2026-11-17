#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What ``llm_call_start`` reports: the request, as actually sent.

Covers the pieces that build it: engine unwrapping, ``llm_request_for``,
``tool_def_names``, ``LlmCallRequest.engine_io_fields`` and the tool-call list
``llm_call_end`` carries.
"""

from __future__ import annotations

from types import SimpleNamespace

from mas.runtime.driver.driver import ExchangeRecord, engine_manifest, engine_model_id, llm_request_for
from mas.runtime.engine.exchange_preview import tool_def_names
from mas.runtime.engine.llm_output_limits import OutputLimits
from mas.runtime.kernel.envelope import LlmCallRequest
from mas.runtime.machines.obs_envelope import _llm_return_tool_calls
from mas.runtime.schema.ingress import ToolCallSpec

USER = [{"role": "user", "content": "x"}]
TOOL_TURN = [
    *USER,
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "r"},
]


class Leaf:
    model = "m"
    max_tokens = 100
    temperature = 0.7


class Middleware:
    def __init__(self, inner):
        self.inner = inner


def _record(messages=None, tools=None, model=None) -> ExchangeRecord:
    rec = ExchangeRecord.__new__(ExchangeRecord)
    rec.model = model
    rec.messages = messages
    rec.tools = tools
    return rec


# ── engine unwrapping ───────────────────────────────────────────────────────


def test_model_and_manifest_walk_inner_links() -> None:
    leaf = SimpleNamespace(model=" gpt ", manifest={"a": 1})
    assert engine_model_id(Middleware(Middleware(leaf))) == "gpt"
    assert engine_manifest(Middleware(leaf)) == {"a": 1}


def test_walk_stops_on_cycles_and_missing_values() -> None:
    loop = Middleware(None)
    loop.inner = loop
    assert engine_model_id(loop) == ""
    assert engine_manifest(loop) is None
    assert engine_model_id(None) == ""
    assert engine_model_id(SimpleNamespace(model="  ")) == ""


# ── llm_request_for ─────────────────────────────────────────────────────────


def test_request_values_survive_middleware_wrapping() -> None:
    got = llm_request_for(Middleware(Middleware(Leaf())), _record(USER), "LLM_CALL")
    assert (got.model, got.max_tokens, got.temperature) == ("m", 100, 0.7)
    assert got.messages == USER


def test_non_llm_ops_have_no_request() -> None:
    assert llm_request_for(Leaf(), _record(), "TOOL_CALL") is None


def test_max_tokens_is_the_budget_actually_sent() -> None:
    class Reasoning(Leaf):
        output_limits = OutputLimits(max_tokens=100, max_completion_tokens=900)

    assert llm_request_for(Reasoning(), _record(USER), "LLM_CALL").max_tokens == 900


def test_temperature_is_zero_when_answering_from_tool_results() -> None:
    assert llm_request_for(Leaf(), _record(TOOL_TURN), "LLM_CALL").temperature == 0.0
    assert llm_request_for(Leaf(), _record(USER), "LLM_CALL").temperature == 0.7


def test_unknown_values_stay_unset_and_bools_are_not_numbers() -> None:
    bare = SimpleNamespace(max_tokens=True, temperature=False)
    got = llm_request_for(bare, _record(), "LLM_CALL")
    assert (got.model, got.messages, got.offered_tools, got.max_tokens, got.temperature) == ("", None, None, None, None)


def test_record_model_wins_over_engine_model() -> None:
    assert llm_request_for(Leaf(), _record(model="from-record"), "LLM_CALL").model == "from-record"


# ── tool_def_names ──────────────────────────────────────────────────────────


def test_tool_def_names_accepts_every_shape() -> None:
    tools = [
        {"type": "function", "function": {"name": "a"}},
        {"name": "b"},
        "c",
        "",
        {"function": {}},
        42,
    ]
    assert tool_def_names(tools) == ["a", "b", "c"]
    assert tool_def_names(None) == []


# ── LlmCallRequest.engine_io_fields ─────────────────────────────────────────────


def test_engine_io_fields_leave_out_unset_values() -> None:
    assert LlmCallRequest().engine_io_fields() == {}
    full = LlmCallRequest(model="m", messages=USER, offered_tools=["t"], max_tokens=1, temperature=0.0)
    assert full.engine_io_fields() == {
        "model": "m",
        "messages": USER,
        "tools": ["t"],
        "max_tokens": 1,
        "temperature": 0.0,
    }


# ── llm_call_end tool calls ─────────────────────────────────────────────────


def test_return_tool_calls_single_parallel_and_none() -> None:
    single = SimpleNamespace(next_step="TOOL_CALL", tool_name="a", tool_arguments={"q": 1}, parallel_tools=())
    assert _llm_return_tool_calls(single) == [{"toolName": "a", "arguments": {"q": 1}}]
    parallel = SimpleNamespace(
        next_step="PARALLEL_TOOL_CALLS",
        tool_name="",
        parallel_tools=(
            ToolCallSpec(tool_name="a", tool_arguments={}),
            ToolCallSpec(tool_name="b", tool_arguments={"x": 2}),
        ),
    )
    assert [c["toolName"] for c in _llm_return_tool_calls(parallel)] == ["a", "b"]
    assert _llm_return_tool_calls(SimpleNamespace(next_step="STOP", tool_name="", parallel_tools=())) is None


def test_parallel_turn_with_no_specs_is_not_reported_as_a_single_call() -> None:
    ev = SimpleNamespace(next_step="PARALLEL_TOOL_CALLS", tool_name="stale", parallel_tools=())
    assert _llm_return_tool_calls(ev) is None


def test_temperature_helper_is_the_one_rule_the_engine_and_the_request_share() -> None:
    from mas.runtime.engine.llm_request import sampling_temperature

    assert sampling_temperature(0.7, answering_from_tools=True) == 0.0
    assert sampling_temperature(0.7, answering_from_tools=False) == 0.7


async def test_snapshot_is_read_through_a_middleware_wrapper() -> None:
    from mas.runtime.driver.driver import _engine_snapshot
    from mas.runtime.engine.exchange_preview import ExchangeSnapshot

    class Snapshotting(Leaf):
        async def aexchange_snapshot(self, op, *, correlation_id=0):
            return ExchangeSnapshot(messages=USER, tools=[{"function": {"name": "t"}}])

    snap = await _engine_snapshot(Middleware(Snapshotting()), "LLM_CALL")
    assert snap is not None and snap.messages == USER
    assert await _engine_snapshot(Middleware(Leaf()), "LLM_CALL") is None

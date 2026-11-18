#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``provider_state`` on engine returns and ``is_error`` on tool messages."""

from __future__ import annotations

import json

from mas.runtime.boundary.context.working_memory import WorkingMemoryStore
from mas.runtime.boundary.gov.transition import build_gov_transition
from mas.runtime.driver.driver import _engine_payload_json
from mas.runtime.engine.infra_pipeline import _completion_from_cached_text, _provider_result_return
from mas.runtime.engine.llm_cache import middleware_cache_deserialize, middleware_cache_serialize
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.kernel.run_event import from_engine_return
from mas.runtime.kernel.state import RunEvent, RunLedger
from mas.runtime.kernel.state_serialize import run_from_dict, run_to_dict
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec

STATE = {"blocks": [{"type": "thinking", "thinking": "hm", "signature": "sig=="}]}


def _call_message(n: int = 1) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": f"t{i}", "arguments": "{}"}} for i in range(n)
        ],
        "provider_state": STATE,
    }


def _convert(message: dict) -> EngineIoReturn:
    engine = LiveLlmEngine(use_tool_loop=True, use_cache=False)
    io = InvokeEngineIo(correlation_id=3, op="LLM_CALL")
    return engine._message_to_engine_return(io, message, [], [], False, {}, "tool_calls")


def test_engine_return_defaults_and_round_trip() -> None:
    assert EngineIoReturn(correlation_id=1).provider_state == {}
    ret = EngineIoReturn(correlation_id=1, provider_state=STATE)
    assert EngineIoReturn.model_validate(ret.model_dump(mode="json")).provider_state == STATE


def test_run_row_carries_provider_state_through_checkpoint() -> None:
    run = RunLedger()
    run.append(
        from_engine_return(EngineIoReturn(correlation_id=1, next_step="TOOL_CALL", tool_name="t", provider_state=STATE))
    )
    run.append(RunEvent(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL"))
    dumped = run_to_dict(run)
    assert "provider_state" not in dumped["events"][1]  # old snapshots keep their shape
    again = run_from_dict(json.loads(json.dumps(dumped)))
    assert again.events[0].provider_state == STATE
    assert again.events[1].provider_state == {}


def test_hitl_pending_return_keeps_provider_state() -> None:
    ret = EngineIoReturn(correlation_id=1, next_step="TOOL_CALL", tool_name="t", provider_state=STATE)
    assert EngineIoReturn.model_validate(ret.model_dump(mode="json")).provider_state == STATE


def test_llm_live_propagates_provider_state_single_and_parallel() -> None:
    single = _convert(_call_message(1))
    assert single.next_step == "TOOL_CALL" and single.provider_state == STATE
    parallel = _convert(_call_message(2))
    assert parallel.next_step == "PARALLEL_TOOL_CALLS" and parallel.provider_state == STATE
    assert _convert({"role": "assistant", "content": "hi", "provider_state": "bad"}).provider_state == {}


def test_pipeline_text_round_trip_keeps_provider_state() -> None:
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    ret = _provider_result_return(io, {}, _call_message(1))
    assert _completion_from_cached_text(ret.text)["provider_state"] == STATE


def test_cache_hit_replays_provider_state() -> None:
    """The llm_cache layer stores the leaf return; its text holds the whole completion."""
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    leaf = _provider_result_return(io, {}, _call_message(2))
    stored = middleware_cache_serialize(leaf)
    hit = middleware_cache_deserialize(stored, 5)
    replayed = _completion_from_cached_text(hit.text)
    assert replayed["provider_state"] == STATE
    assert _convert(replayed).provider_state == STATE


def test_middleware_cache_round_trip_keeps_provider_state() -> None:
    ret = EngineIoReturn(
        correlation_id=1,
        next_step="PARALLEL_TOOL_CALLS",
        parallel_tools=(ToolCallSpec(tool_name="a"), ToolCallSpec(tool_name="b")),
        provider_state=STATE,
    )
    entry = middleware_cache_serialize(ret)
    assert middleware_cache_deserialize(entry, 9).provider_state == STATE
    plain = middleware_cache_serialize(EngineIoReturn(correlation_id=1, next_step="TOOL_CALL", tool_name="t"))
    assert "provider_state" not in plain


def _row(**kwargs) -> RunEvent:
    return from_engine_return(EngineIoReturn(correlation_id=4, **kwargs))


def test_working_memory_attaches_provider_state_to_tool_call_messages() -> None:
    wm = WorkingMemoryStore()
    wm.project_event(_row(next_step="TOOL_CALL", tool_name="t", provider_state=STATE))
    assert wm.messages[-1]["provider_state"] == STATE
    wm2 = WorkingMemoryStore()
    wm2.project_event(
        _row(
            next_step="PARALLEL_TOOL_CALLS",
            parallel_tools=(ToolCallSpec(tool_name="a"), ToolCallSpec(tool_name="b")),
            provider_state=STATE,
        )
    )
    assert wm2.messages[-1]["provider_state"] == STATE and len(wm2.messages[-1]["tool_calls"]) == 2


def test_working_memory_omits_empty_provider_state_and_text_messages() -> None:
    wm = WorkingMemoryStore()
    wm.project_event(_row(next_step="TOOL_CALL", tool_name="t"))
    assert "provider_state" not in wm.messages[-1]
    wm.project_event(from_engine_return(EngineIoReturn(correlation_id=9, text="done", provider_state=STATE)))
    assert wm.messages[-1] == {"role": "assistant", "content": "done"}


def test_tool_error_flag_only_on_error_rows() -> None:
    wm = WorkingMemoryStore()
    wm.project_event(_row(next_step="TOOL_CALL", tool_name="t"))
    wm.project_event(EngineIoReturn(correlation_id=4, response_kind="ERROR", text="boom"))
    assert wm.messages[-1] == {"role": "tool", "tool_call_id": "call_4", "content": "boom", "is_error": True}
    ok = WorkingMemoryStore()
    ok.project_event(_row(next_step="TOOL_CALL", tool_name="t"))
    ok.project_event(EngineIoReturn(correlation_id=4, response_kind="TOOL_RESULT", text="fine"))
    assert "is_error" not in ok.messages[-1]


def test_observability_payloads_exclude_provider_state() -> None:
    ret = EngineIoReturn(correlation_id=1, next_step="TOOL_CALL", tool_name="t", provider_state=STATE)
    assert "provider_state" not in json.loads(_engine_payload_json(ret))
    transition = build_gov_transition("ingress", ret)
    assert "provider_state" not in transition.attributes


def test_event_log_drops_provider_state_from_any_payload_with_messages() -> None:
    from mas.runtime.boundary.obs.operator import ObservabilityOperator

    operator = ObservabilityOperator()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [], "provider_state": STATE},
    ]
    operator.record_context_assembled(correlation_id=1, turn_index=1, agent_id="a", messages=messages)
    logged = operator.events[-1].payload["messages"]
    assert all("provider_state" not in m for m in logged)
    assert messages[1]["provider_state"] == STATE  # the live transcript keeps it
    assert logged[1]["role"] == "assistant"

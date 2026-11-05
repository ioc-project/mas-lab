#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""boundary/gov/transition.py — GovTransition construction for governance plugins."""

from __future__ import annotations

from mas.runtime.boundary.gov.transition import GovTransition, build_gov_transition
from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import EngineIoReturn, HitlResolve, UserInputReceived


def test_ingress_user_input_received_shape() -> None:
    symbol = UserInputReceived(user_turn_id="u1", text="hello", session_id="s1", task_id="t1")
    t = build_gov_transition("ingress", symbol, q=QProduct(), agent_id="agent-1", session_id="s1", task_id="t1")
    assert isinstance(t, GovTransition)
    assert t.hook == "ingress"
    assert t.kind == "USER_INPUT_RECEIVED"
    assert t.op == ""
    assert t.response_kind == ""
    assert t.machine == "execution_engine"
    assert t.destructive is False
    assert t.agent_id == "agent-1"
    assert t.session_id == "s1"
    assert t.task_id == "t1"
    assert t.symbol is symbol
    assert t.attributes["text"] == "hello"
    assert t.attributes["session_id"] == "s1"
    assert t.attributes["task_id"] == "t1"


def test_egress_tool_call_resolves_machine_and_tool_name_from_shared_pending_fields() -> None:
    q = QProduct()
    q.pending_tool_name = "lookup_schedule"
    q.pending_tool_args = {"origin": "A", "destination": "B"}
    symbol = InvokeEngineIo(correlation_id=1, op="TOOL_CALL")

    t = build_gov_transition("egress", symbol, q=q, agent_id="agent-1")

    assert t.hook == "egress"
    assert t.op == "TOOL_CALL"
    assert t.machine == "M_tool"
    assert t.correlation_id == 1
    assert t.attributes["tool_name"] == "lookup_schedule"
    assert t.attributes["tool_arguments"] == {"origin": "A", "destination": "B"}


def test_egress_tool_call_prefers_pending_tools_by_cid_over_shared_fields() -> None:
    """Parallel tool calls populate pending_tools_by_cid per-correlation-id;
    the shared pending_tool_name/args only ever hold the LAST spec set. A
    plugin observing an earlier sibling's transition must see ITS OWN tool,
    not whichever one happened to be scheduled last — same resolution the
    driver itself uses at dispatch time (see driver.py's _dispatch_engine_batch)."""
    q = QProduct()
    q.pending_tool_name = "get_attractions"  # the (wrong, last-scheduled) shared field
    q.pending_tool_args = {"city": "Verdantia"}
    q.pending_tools_by_cid[1] = ("lookup_schedule", {"origin": "A", "destination": "B"})

    symbol = InvokeEngineIo(correlation_id=1, op="TOOL_CALL")
    t = build_gov_transition("egress", symbol, q=q, agent_id="agent-1")

    assert t.attributes["tool_name"] == "lookup_schedule"
    assert t.attributes["tool_arguments"] == {"origin": "A", "destination": "B"}


def test_egress_tool_call_with_no_pending_tool_has_no_tool_attributes() -> None:
    symbol = InvokeEngineIo(correlation_id=9, op="TOOL_CALL")
    t = build_gov_transition("egress", symbol, q=QProduct(), agent_id="agent-1")
    assert "tool_name" not in t.attributes
    assert "tool_arguments" not in t.attributes


def test_egress_llm_call_machine_is_model() -> None:
    symbol = InvokeEngineIo(correlation_id=2, op="LLM_CALL")
    t = build_gov_transition("egress", symbol, q=QProduct(), agent_id="agent-1")
    assert t.op == "LLM_CALL"
    assert t.machine == "M_model"
    # Not a tool call — no tool resolution attempted even if pending fields
    # happen to be set from a previous step.
    assert "tool_name" not in t.attributes


def test_egress_memory_op_machine_is_memory() -> None:
    symbol = InvokeEngineIo(correlation_id=3, op="MEMORY_OP")
    t = build_gov_transition("egress", symbol, q=QProduct(), agent_id="agent-1")
    assert t.machine == "M_memory"


def test_ingress_engine_io_return_tool_result_machine_is_tool() -> None:
    symbol = EngineIoReturn(correlation_id=1, response_kind="TOOL_RESULT", next_step="STOP")
    t = build_gov_transition("ingress", symbol, q=QProduct(), agent_id="agent-1")
    assert t.kind == "ENGINE_IO_RETURN"
    assert t.response_kind == "TOOL_RESULT"
    assert t.machine == "M_tool"


def test_ingress_engine_io_return_model_text_machine_is_model() -> None:
    symbol = EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP")
    t = build_gov_transition("ingress", symbol, q=QProduct(), agent_id="agent-1")
    assert t.machine == "M_model"


def test_ingress_hitl_resolve_machine_is_gov() -> None:
    symbol = HitlResolve(
        request_id=1,
        resolution=HitlResolveChoice.ALLOW,
        operator_context={},
    )
    t = build_gov_transition("ingress", symbol, q=QProduct(), agent_id="agent-1")
    assert t.kind == "HITL_RESOLVE"
    assert t.machine == "M_gov"


def test_q_state_snapshot_reflects_live_product_state() -> None:
    q = QProduct()
    q.scheduled_egress = "TOOL_CALL"
    symbol = UserInputReceived(user_turn_id="u1", text="hi")
    t = build_gov_transition("ingress", symbol, q=q, agent_id="agent-1")
    assert t.q_state == {
        "dp": q.dp.value,
        "ctx": q.ctx.value,
        "model": q.model.value,
        "tool": q.tool.value,
        "gov": q.gov_state,
        "scheduled_egress": "TOOL_CALL",
    }


def test_q_state_empty_when_q_not_provided() -> None:
    symbol = UserInputReceived(user_turn_id="u1", text="hi")
    t = build_gov_transition("ingress", symbol, agent_id="agent-1")
    assert t.q_state == {}


def test_agent_spec_and_destructive_pass_through() -> None:
    spec = {"tools": ["lookup_schedule"]}
    symbol = InvokeEngineIo(correlation_id=5, op="TOOL_CALL", destructive=True)
    t = build_gov_transition("egress", symbol, q=QProduct(), agent_spec=spec)
    assert t.agent_spec == spec
    assert t.destructive is True

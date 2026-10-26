#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""hitl_resolve events record who decided and any steering text."""

from __future__ import annotations

from unittest.mock import MagicMock

from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.hitl_step import (
    _modified_ingress_return,
    _operator_tool_text,
    apply_hitl_resolve,
)
from mas.runtime.kernel.state import DpState, QProduct, RunLedger
from mas.runtime.machines.gov import gov_enter_hitl_pending
from mas.runtime.schema.egress import EmitClientResponse, NoOp
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import EngineIoReturn, HitlResolve
from mas.runtime.schema.observability import ObsEventKind


def test_hitl_resolve_payload_includes_operator_and_steering() -> None:
    obs = ObservabilityOperator()
    obs.record_ingress(
        HitlResolve(
            request_id=1,
            resolution=HitlResolveChoice.SKIP,
            operator_context={"operator_id": "cli-operator", "steering": "stop"},
        ),
        QProduct(),
    )
    events = [e for e in obs.events if e.kind == ObsEventKind.HITL_RESOLVE]
    assert len(events) == 1
    assert events[0].actor_id == "cli-operator"
    assert events[0].payload["operator_id"] == "cli-operator"
    assert events[0].payload["steering"] == "stop"
    assert events[0].payload["resolution"] == "SKIP"


def test_block_appends_operator_steering_to_the_denial() -> None:
    canned = _operator_tool_text(blocked=True, tool_ran=False, tool_name="calc", steering="")
    assert canned == "Operator blocked tool 'calc' via HITL."
    with_steer = _operator_tool_text(
        blocked=True,
        tool_ran=False,
        tool_name="calc",
        steering="Budget work is out of policy.",
    )
    assert with_steer.startswith(canned)
    assert "Budget work is out of policy." in with_steer
    skipped = _operator_tool_text(
        blocked=False,
        tool_ran=False,
        tool_name="calc",
        steering="Ask the user instead.",
    )
    assert skipped == "Ask the user instead."
    blocked_result = _operator_tool_text(
        blocked=True,
        tool_ran=True,
        tool_name="web-search",
        steering="Drop the result.",
    )
    assert "tool result for 'web-search'" in blocked_result
    assert "Drop the result." in blocked_result
    skipped_result = _operator_tool_text(
        blocked=False,
        tool_ran=True,
        tool_name="web-search",
        steering="",
    )
    assert skipped_result == "Operator skipped tool result for 'web-search' via HITL."


def test_modified_ingress_return_stops_on_block_or_non_tool_result() -> None:
    tool = EngineIoReturn(
        correlation_id=3,
        response_kind="TOOL_RESULT",
        next_step="LLM_CALL",
        text="secret",
    )
    blocked = _modified_ingress_return(tool, "denied", blocked=True)
    assert blocked.text == "denied"
    assert blocked.next_step == "STOP"
    assert blocked.response_kind == "TOOL_RESULT"
    skipped = _modified_ingress_return(tool, "steer instead", blocked=False)
    assert skipped.next_step == "LLM_CALL"
    model = EngineIoReturn(correlation_id=4, response_kind="MODEL_TEXT", next_step="LLM_CALL", text="hi")
    folded = _modified_ingress_return(model, "no", blocked=False)
    assert folded.response_kind == "MODEL_TEXT"
    assert folded.next_step == "STOP"


def _pending_tool_gate(q: QProduct, *, request_id: int, tool_name: str) -> None:
    q.pending_tool_name = tool_name
    gov_enter_hitl_pending(q, request_id=request_id, pending_schedule="TOOL_CALL")


def _pending_tool_result(q: QProduct, event: EngineIoReturn) -> None:
    q.pending_tool_name = event.tool_name or "tool"
    q.hitl_pending_ingress = event.response_kind
    q.pending_ingress_return = event.model_dump(mode="json")
    gov_enter_hitl_pending(q, request_id=event.correlation_id, pending_schedule="NONE")


def test_schedule_block_ends_the_turn_with_steering() -> None:
    run = RunLedger()
    q = QProduct()
    _pending_tool_gate(q, request_id=11, tool_name="calc")
    out = apply_hitl_resolve(
        q,
        run,
        HitlResolve(
            request_id=11,
            resolution=HitlResolveChoice.BLOCK,
            operator_context={"steering": "Budget work is out of policy."},
        ),
        config=KernelConfig(),
    )
    assert q.hitl_block_committed is True
    assert q.dp is DpState.IDLE
    assert len(out) == 1
    assert isinstance(out[0], EmitClientResponse)
    assert "blocked tool 'calc'" in out[0].content
    assert "Budget work is out of policy." in out[0].content


def test_ingress_block_does_not_call_the_follow_up_llm() -> None:
    pending = EngineIoReturn(
        correlation_id=7,
        response_kind="TOOL_RESULT",
        next_step="LLM_CALL",
        tool_name="web-search",
        text="secret-result",
    )
    run = RunLedger()
    q = QProduct()
    _pending_tool_result(q, pending)
    evaluate = MagicMock(return_value=[NoOp()])
    out = apply_hitl_resolve(
        q,
        run,
        HitlResolve(
            request_id=7,
            resolution=HitlResolveChoice.BLOCK,
            operator_context={"steering": "Do not use that result."},
        ),
        config=KernelConfig(),
        evaluate=evaluate,
    )
    evaluate.assert_not_called()
    assert q.hitl_pending_ingress == ""
    assert q.dp is DpState.IDLE
    assert isinstance(out[0], EmitClientResponse)
    assert "blocked tool result" in out[0].content
    assert "Do not use that result." in out[0].content
    latest = run.latest()
    assert latest is not None
    assert latest.next_step == "STOP"


def test_ingress_skip_uses_steering_as_the_tool_result() -> None:
    pending = EngineIoReturn(
        correlation_id=8,
        response_kind="TOOL_RESULT",
        next_step="LLM_CALL",
        tool_name="web-search",
        text="secret-result",
    )
    run = RunLedger()
    q = QProduct()
    _pending_tool_result(q, pending)
    evaluate = MagicMock(return_value=[NoOp()])
    apply_hitl_resolve(
        q,
        run,
        HitlResolve(
            request_id=8,
            resolution=HitlResolveChoice.SKIP,
            operator_context={"steering": "Ask the user instead."},
        ),
        config=KernelConfig(),
        evaluate=evaluate,
    )
    evaluate.assert_called_once()
    latest = run.latest()
    assert latest is not None
    assert latest.text == "Ask the user instead."
    assert latest.next_step == "LLM_CALL"


def test_ingress_unknown_resolution_is_a_noop() -> None:
    pending = EngineIoReturn(
        correlation_id=9,
        response_kind="TOOL_RESULT",
        next_step="LLM_CALL",
        text="x",
    )
    run = RunLedger()
    q = QProduct()
    _pending_tool_result(q, pending)
    out = apply_hitl_resolve(
        q,
        run,
        HitlResolve(request_id=9, resolution=HitlResolveChoice.TERMINATE),
        config=KernelConfig(),
        evaluate=MagicMock(return_value=[NoOp()]),
    )
    assert out == [NoOp()]
    assert q.hitl_pending_ingress == "TOOL_RESULT"


def test_hitl_resolve_reads_message_as_steering_and_defaults_operator() -> None:
    event = HitlResolve(
        request_id=1,
        resolution=HitlResolveChoice.SKIP,
        operator_context={"message": "ask the user instead"},
    )
    assert event.operator_id == "operator"
    assert event.steering_text == "ask the user instead"

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""boundary/gov/filter.py — GovTransitionFilter.matches() against GovTransition."""

from __future__ import annotations

from mas.runtime.boundary.gov.filter import GovTransitionFilter
from mas.runtime.boundary.gov.transition import build_gov_transition
from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import UserInputReceived


def _user_input_transition():
    return build_gov_transition("ingress", UserInputReceived(user_turn_id="u1", text="hi"), q=QProduct())


def _tool_call_transition(*, destructive: bool = False):
    q = QProduct()
    q.pending_tool_name = "lookup_schedule"
    symbol = InvokeEngineIo(correlation_id=1, op="TOOL_CALL", destructive=destructive)
    return build_gov_transition("egress", symbol, q=q)


def test_empty_filter_matches_everything_on_its_hook() -> None:
    assert GovTransitionFilter(hook="ingress").matches(_user_input_transition())
    assert GovTransitionFilter(hook="egress").matches(_tool_call_transition())


def test_hook_mismatch_never_matches() -> None:
    assert not GovTransitionFilter(hook="egress").matches(_user_input_transition())
    assert not GovTransitionFilter(hook="ingress").matches(_tool_call_transition())


def test_kind_filter() -> None:
    f = GovTransitionFilter(hook="ingress", kind=("USER_INPUT_RECEIVED",))
    assert f.matches(_user_input_transition())
    assert not GovTransitionFilter(hook="ingress", kind=("HITL_RESOLVE",)).matches(_user_input_transition())


def test_op_filter() -> None:
    f = GovTransitionFilter(hook="egress", op=("TOOL_CALL",))
    assert f.matches(_tool_call_transition())
    assert not GovTransitionFilter(hook="egress", op=("LLM_CALL",)).matches(_tool_call_transition())


def test_response_kind_filter() -> None:
    from mas.runtime.schema.ingress import EngineIoReturn

    t = build_gov_transition(
        "ingress",
        EngineIoReturn(correlation_id=1, response_kind="TOOL_RESULT", next_step="STOP"),
        q=QProduct(),
    )
    assert GovTransitionFilter(hook="ingress", response_kind=("TOOL_RESULT",)).matches(t)
    assert not GovTransitionFilter(hook="ingress", response_kind=("MODEL_TEXT",)).matches(t)


def test_destructive_filter() -> None:
    destructive_t = _tool_call_transition(destructive=True)
    safe_t = _tool_call_transition(destructive=False)
    f = GovTransitionFilter(hook="egress", destructive=True)
    assert f.matches(destructive_t)
    assert not f.matches(safe_t)
    # destructive=None (default) means "don't care" — matches either.
    f_any = GovTransitionFilter(hook="egress")
    assert f_any.matches(destructive_t)
    assert f_any.matches(safe_t)


def test_machine_filter() -> None:
    f = GovTransitionFilter(hook="egress", machine=("M_tool",))
    assert f.matches(_tool_call_transition())
    llm_t = build_gov_transition("egress", InvokeEngineIo(correlation_id=2, op="LLM_CALL"), q=QProduct())
    assert not f.matches(llm_t)


def test_combined_filter_requires_all_declared_fields_to_match() -> None:
    f = GovTransitionFilter(hook="egress", op=("TOOL_CALL",), destructive=False)
    assert f.matches(_tool_call_transition(destructive=False))
    assert not f.matches(_tool_call_transition(destructive=True))


def test_from_dict_parses_kind_alongside_existing_fields() -> None:
    f = GovTransitionFilter.from_dict(
        {"hook": "ingress", "kind": ["USER_INPUT_RECEIVED", "HITL_RESOLVE"], "destructive": True}
    )
    assert f.hook == "ingress"
    assert f.kind == ("USER_INPUT_RECEIVED", "HITL_RESOLVE")
    assert f.destructive is True


def test_from_dict_kind_accepts_bare_string() -> None:
    f = GovTransitionFilter.from_dict({"kind": "USER_INPUT_RECEIVED"})
    assert f.kind == ("USER_INPUT_RECEIVED",)

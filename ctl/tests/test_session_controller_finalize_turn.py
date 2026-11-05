#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SessionController._finalize_turn — folds working memory even mid-HITL-pause."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.ctl.session.controller import ConversationConfig, SessionController, TurnResult
from mas.runtime.driver.driver import ExchangeRecord
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.session import BacktrackCapReached
from mas.runtime.spec.checkpoint import CheckpointPolicy


def _controller_with_ctx(ctx: AutoCtxAssembler, **config_kwargs) -> SessionController:
    instance = SimpleNamespace(driver=SimpleNamespace(ctx=ctx))
    return SessionController(
        instance=instance,
        display=None,
        config=ConversationConfig(**config_kwargs),
    )


def test_finalize_turn_folds_working_memory_even_when_awaiting_hitl():
    """Regression: a delegated (or any) turn paused mid-flight for HITL still
    has its already-executed tool calls folded into committed history. Before
    this fix, they sat only in ctx.working_memory and were silently wiped by
    the next turn's note_user_input() -- a real data-loss gap, not something
    a resumed HITL flow could recover from."""
    ctx = AutoCtxAssembler()
    ctx.note_user_input("please book a flight")
    ctx.record_assistant_tool_call(call_id="c1", tool_name="book_flight", arguments={"dest": "CDG"})
    ctx.record_tool_result(call_id="c1", content="awaiting approval")

    controller = _controller_with_ctx(ctx)
    result = TurnResult(trace=None, responses=[], awaiting_hitl=True)
    controller._finalize_turn(result)

    assert ctx.working_memory.messages == []  # folded in, not left dangling
    roles = [m["role"] for m in ctx.committed_messages]
    assert roles == ["user", "assistant", "tool"]
    assert ctx.committed_messages[0]["content"] == "please book a flight"


def test_finalize_turn_does_not_duplicate_the_user_turn_on_hitl_resume():
    """Regression: a turn folded once at HITL-pause time and again when
    submit_hitl() resolves it (both call _finalize_turn) used to re-append
    the SAME user message and a bogus turn_history tuple the second time,
    because note_agent_response never cleared ctx.last_user_text after
    using it -- harmless before this branch (finalize only ever ran once
    per turn), a real duplication bug once a paused turn can be folded
    twice."""
    from mas.runtime.schema.egress import EmitClientResponse

    ctx = AutoCtxAssembler()
    ctx.note_user_input("please book a flight")
    ctx.record_assistant_tool_call(call_id="c1", tool_name="book_flight", arguments={})
    ctx.record_tool_result(call_id="c1", content="awaiting approval")

    controller = _controller_with_ctx(ctx)
    controller._finalize_turn(TurnResult(trace=None, responses=[], awaiting_hitl=True))

    # Continuation after HITL approval: more tool activity, then a final answer.
    ctx.record_assistant_tool_call(call_id="c2", tool_name="book_flight", arguments={})
    ctx.record_tool_result(call_id="c2", content="booked!")
    resp = EmitClientResponse(content="Your flight is booked!", finish_reason="stop")
    controller._finalize_turn(TurnResult(trace=None, responses=[resp], awaiting_hitl=False))

    user_messages = [m for m in ctx.committed_messages if m.get("content") == "please book a flight"]
    assert len(user_messages) == 1
    assert ctx.committed_messages[-1] == {"role": "assistant", "content": "Your flight is booked!"}


def test_finalize_turn_skips_checkpoint_while_awaiting_hitl():
    ctx = AutoCtxAssembler()
    ctx.note_user_input("please book a flight")
    ctx.record_assistant_tool_call(call_id="c1", tool_name="book_flight", arguments={})
    ctx.record_tool_result(call_id="c1", content="awaiting approval")

    saved: list[dict] = []
    controller = _controller_with_ctx(ctx, save_checkpoint_each_turn=True)
    controller.instance.record_checkpoint = lambda label="": {"label": label}
    controller.checkpoint_store = SimpleNamespace(save=lambda snap: saved.append(snap))

    controller._finalize_turn(TurnResult(trace=None, responses=[], awaiting_hitl=True))

    assert saved == []  # turn isn't actually done -- no checkpoint yet


def test_finalize_turn_does_not_fold_when_nothing_happened_yet():
    """No response text and no working memory recorded -- nothing to commit,
    same as before this change (avoids inserting spurious empty turns)."""
    ctx = AutoCtxAssembler()
    controller = _controller_with_ctx(ctx)
    controller._finalize_turn(TurnResult(trace=None, responses=[], awaiting_hitl=True))
    assert ctx.committed_messages == []
    assert ctx.turn_history == []


def test_finalize_turn_completed_turn_still_commits_and_checkpoints():
    """Normal (non-HITL) path is unchanged: response text folds in and a
    completed turn still checkpoints."""
    from mas.runtime.schema.egress import EmitClientResponse

    ctx = AutoCtxAssembler()
    ctx.note_user_input("hello")
    saved: list[dict] = []
    controller = _controller_with_ctx(ctx, save_checkpoint_each_turn=True)
    controller.instance.record_checkpoint = lambda label="": {"label": label}
    controller.checkpoint_store = SimpleNamespace(save=lambda snap: saved.append(snap))

    result = TurnResult(
        trace=None,
        responses=[EmitClientResponse(content="hi there", finish_reason="stop")],
        awaiting_hitl=False,
    )
    controller._finalize_turn(result)

    assert ctx.committed_messages[-1] == {"role": "assistant", "content": "hi there"}
    assert len(saved) == 1


def test_backtrack_command_delegates_steps_and_note_to_managed_session():
    controller = _controller_with_ctx(AutoCtxAssembler())
    calls = []
    controller.managed_session = SimpleNamespace(
        backtrack=lambda store, *, steps, steering_text: (
            calls.append((store, steps, steering_text)) or "turn-2.checkpoint.json"
        )
    )
    controller.checkpoint_store = object()
    controller.display = SimpleNamespace(on_system=lambda _message: None)

    result = controller.run_turn("/backtrack 2 explain the correction")

    assert result.trace is None
    assert calls == [(controller.checkpoint_store, 2, "explain the correction")]


def test_on_event_policy_checkpoints_only_matching_exchange_events():
    controller = _controller_with_ctx(AutoCtxAssembler())
    checkpoints = []
    controller.managed_session = SimpleNamespace(
        checkpoint_policy=CheckpointPolicy(mode="on_event", triggers=("after_llm_call",)),
        checkpoint=lambda store, *, label: checkpoints.append(label),
    )
    controller.checkpoint_store = object()

    controller._checkpoint_after_exchange(ExchangeRecord(kind="llm_request"))
    controller._checkpoint_after_exchange(ExchangeRecord(kind="llm_response"))

    assert len(checkpoints) == 1
    assert checkpoints[0].startswith("event-")


def test_every_turn_policy_checkpoints_completed_turns():
    controller = _controller_with_ctx(AutoCtxAssembler())
    checkpoints = []
    controller.managed_session = SimpleNamespace(
        checkpoint_policy=CheckpointPolicy(mode="every_turn"),
        checkpoint=lambda store, *, label: checkpoints.append(label),
    )
    controller.checkpoint_store = object()
    controller._turn = 2

    controller._finalize_turn(TurnResult(trace=None, responses=[], awaiting_hitl=False))

    assert checkpoints == ["turn-2"]


def test_restore_turn_sets_and_rejects_negative():
    controller = _controller_with_ctx(AutoCtxAssembler())
    controller.restore_turn(4)
    assert controller.turn == 4
    with pytest.raises(ValueError, match="turn must be"):
        controller.restore_turn(-1)


def test_automatic_backtrack_uses_policy_steps_and_error_as_steering():
    controller = _controller_with_ctx(AutoCtxAssembler())
    calls = []
    controller.managed_session = SimpleNamespace(
        backtrack_policy={"steps": 2},
        backtrack=lambda store, **kwargs: calls.append((store, kwargs)),
    )
    controller.checkpoint_store = object()
    trace = SimpleNamespace(boundary_errors=[SimpleNamespace(code="INGRESS_BACKTRACK", message="tool failed")])

    handled = controller._handle_automatic_backtrack(trace)

    assert handled is True
    assert calls[0][0] is controller.checkpoint_store
    assert calls[0][1]["steps"] == 2
    assert calls[0][1]["automatic"] is True
    assert "tool failed" in calls[0][1]["steering_text"]


def test_automatic_backtrack_cap_pauses_for_operator_policy():
    controller = _controller_with_ctx(AutoCtxAssembler())
    controller.managed_session = SimpleNamespace(
        backtrack_policy={"steps": 1},
        status=None,
        backtrack=lambda *_args, **_kwargs: (_ for _ in ()).throw(BacktrackCapReached("hitl")),
    )
    controller.checkpoint_store = object()
    controller.instance.pause = lambda **_kwargs: None
    messages = []
    controller.display = SimpleNamespace(on_system=messages.append)
    trace = SimpleNamespace(boundary_errors=[SimpleNamespace(code="INGRESS_BACKTRACK", message="repeat")])

    assert controller._handle_automatic_backtrack(trace) is True
    assert messages == ["automatic backtrack limit reached (hitl)"]

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Operator console — state-driven stdin (no concurrent You:/HITL prompts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from mas.ctl.session.operator_console import OperatorConsole, OperatorMode
from mas.runtime.schema.hitl import HitlResolveChoice


@dataclass
class _FakeHitlRequest:
    request_id: str = "r1"
    offered_actions: list = field(default_factory=lambda: list(HitlResolveChoice))


@dataclass
class _FakeTrace:
    hitl_requests: list = field(default_factory=list)
    awaiting_hitl: bool = False
    client_responses: list = field(default_factory=list)
    boundary_errors: list = field(default_factory=list)


class TestOperatorConsole:
    def test_control_commands_do_not_run_turn(self, monkeypatch):
        err = __import__("io").StringIO()
        console = OperatorConsole(err=err)
        controller = MagicMock()
        controller.config.single_turn = False
        controller.instance = MagicMock()
        calls: list[str] = []

        def run_turn(text, **kw):
            calls.append(text)
            return MagicMock(awaiting_hitl=False, trace=_FakeTrace())

        controller.run_turn = run_turn
        inputs = iter(["/pause", "/resume", "/abort", "/quit"])
        monkeypatch.setattr(
            "mas.ctl.session.operator_console.sys.stdin",
            MagicMock(readline=lambda: next(inputs) + "\n"),
        )
        console.run(controller)
        assert calls == []
        controller.instance.pause.assert_called_once()
        controller.instance.resume.assert_called_once()
        controller.instance.abort.assert_called_once()
        controller.display.on_system.assert_any_call("paused (LifecyclePause)")

    def test_hitl_mode_after_turn(self, monkeypatch):
        err = __import__("io").StringIO()
        console = OperatorConsole(err=err)
        from mas.runtime.schema.egress import EmitHitlRequest, HitlQuestionType

        req = EmitHitlRequest(
            request_id=1,
            question_type=HitlQuestionType.CONFIRM,
            question="allow web_search?",
            offered_actions=list(HitlResolveChoice),
        )
        turn_trace = _FakeTrace(hitl_requests=[req], awaiting_hitl=True)
        after_hitl = _FakeTrace(awaiting_hitl=False)
        controller = MagicMock()
        controller.config.single_turn = True
        controller.run_turn = MagicMock(return_value=MagicMock(awaiting_hitl=True, trace=turn_trace))
        controller.submit_hitl = MagicMock(return_value=MagicMock(awaiting_hitl=False, trace=after_hitl))
        inputs = iter(["hello", "ALLOW"])
        monkeypatch.setattr(
            "mas.ctl.session.operator_console.sys.stdin",
            MagicMock(readline=lambda: next(inputs) + "\n"),
        )
        console.run(controller)
        controller.run_turn.assert_called_once_with("hello", auto_hitl=False)
        controller.submit_hitl.assert_called_once()
        assert "HITL decision required" in err.getvalue()

    def test_reset_calls_controller_reset_session(self, monkeypatch):
        err = __import__("io").StringIO()
        console = OperatorConsole(err=err)
        controller = MagicMock()
        controller.config.single_turn = True
        controller.run_turn = MagicMock(return_value=MagicMock(awaiting_hitl=False, trace=_FakeTrace()))
        inputs = iter(["/reset", "/quit"])
        monkeypatch.setattr(
            "mas.ctl.session.operator_console.sys.stdin",
            MagicMock(readline=lambda: next(inputs) + "\n"),
        )
        console.run(controller)
        controller.reset_session.assert_called_once()
        controller.run_turn.assert_not_called()

    def test_reset_refused_in_hitl_mode(self):
        err = __import__("io").StringIO()
        console = OperatorConsole(err=err)
        controller = MagicMock()
        controller.reset_session = MagicMock()

        handled = console._handle_control(
            controller,
            "/reset",
            OperatorMode.HITL,
            _FakeHitlRequest(),
        )
        assert handled is True
        controller.reset_session.assert_not_called()
        controller.display.on_system.assert_called_with("reset refused: resolve HITL first")

    def test_steer_uses_run_turn(self, monkeypatch):
        console = OperatorConsole(err=__import__("io").StringIO())
        controller = MagicMock()
        controller.config.single_turn = True
        controller.run_turn = MagicMock(return_value=MagicMock(awaiting_hitl=False, trace=_FakeTrace()))
        inputs = iter(["/steer focus on tests", "/quit"])
        monkeypatch.setattr(
            "mas.ctl.session.operator_console.sys.stdin",
            MagicMock(readline=lambda: next(inputs) + "\n"),
        )
        console.run(controller)
        controller.run_turn.assert_any_call("/steer focus on tests", auto_hitl=False)

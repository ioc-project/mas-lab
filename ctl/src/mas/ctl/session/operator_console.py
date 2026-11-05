#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Operator console — single stdin loop driven by kernel/session state.

Ctl terminates the surface protocol here: one prompt at a time (user turn, HITL,
or control). Concurrency is excluded by construction — the runtime product
(M_ctrl, M_gov HITL_PENDING, M_dp) decides which ingress symbols are legal;
the console never offers ``You:`` while ``gov_is_hitl_pending`` or while a turn
is in flight.

Steering (``OperatorSteerReceived``) preempts a working decode: streamed
tokens are kept and the operator text continues the same generation.
It is not a finished client response and not interrupt-and-replace.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TextIO

from mas.ctl.session.hitl_prompt import build_hitl_resolve, print_hitl_brief


class OperatorMode(str, Enum):
    USER = "user"
    HITL = "hitl"


@dataclass
class OperatorConsole:
    """Stdout/stderr interactive loop — same state discipline as curses TUI."""

    err: TextIO = field(default_factory=lambda: sys.stderr)
    user_prompt: str = "You: "
    hitl_prompt: str = "HITL> "
    exit_commands: frozenset[str] = frozenset({"/quit", "/exit", "/q", "exit", "quit"})

    def run(
        self,
        controller: Any,
        *,
        initial: str | None = None,
    ) -> None:
        mode = OperatorMode.USER
        hitl_request = None
        pending_first = initial

        while True:
            if mode == OperatorMode.HITL and hitl_request is not None:
                line = self._read_line(self.hitl_prompt)
                if line is None:
                    break
                resolve = build_hitl_resolve(
                    hitl_request,
                    line,
                    read_line=self._read_line,
                )
                result = controller.submit_hitl(resolve, auto_hitl=False)
                if result.awaiting_hitl and result.trace.hitl_requests:
                    hitl_request = result.trace.hitl_requests[-1]
                    print_hitl_brief(hitl_request, err=self.err)
                else:
                    mode = OperatorMode.USER
                    hitl_request = None
                if controller.config.single_turn and mode == OperatorMode.USER:
                    break
                continue

            if pending_first is not None:
                text = pending_first
                pending_first = None
            else:
                text = self._read_line(self.user_prompt)
            if text is None:
                break
            if not text.strip():
                continue
            stripped = text.strip()
            if stripped.lower() in self.exit_commands:
                break
            if self._handle_control(controller, stripped, mode, hitl_request):
                mode, hitl_request = self._control_state(mode, hitl_request, stripped, controller)
                continue

            result = controller.run_turn(stripped, auto_hitl=False)
            if result.awaiting_hitl and result.trace.hitl_requests:
                hitl_request = result.trace.hitl_requests[-1]
                mode = OperatorMode.HITL
                print_hitl_brief(hitl_request, err=self.err)
            if controller.config.single_turn and mode == OperatorMode.USER:
                break

    def _read_line(self, prompt: str) -> str | None:
        # The prompt is for a person at a terminal. With piped input nobody reads it, and it
        # would run into the echo of the line on the same output ("You: You: ...").
        if _is_terminal(sys.stdin):
            self.err.write(prompt)
            self.err.flush()
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            return None
        if line == "":
            return None
        return line.rstrip("\n")

    def _control_state(
        self,
        mode: OperatorMode,
        hitl_request: Any,
        text: str,
        controller: Any,
    ) -> tuple[OperatorMode, Any]:
        low = text.strip().lower()
        if low == "/reset":
            return OperatorMode.USER, None
        return mode, hitl_request

    def _handle_control(
        self,
        controller: Any,
        text: str,
        mode: OperatorMode,
        hitl_request: Any,
    ) -> bool:
        """Control-plane commands → Lifecycle* / OperatorSteer ingress."""
        low = text.strip().lower()
        if low.startswith("/steer "):
            controller.run_turn(text, auto_hitl=False)
            return True
        if low in ("/pause", "/stop"):
            controller.instance.pause()
            controller.display.on_system("paused (LifecyclePause)")
            return True
        if low == "/resume":
            controller.instance.resume()
            controller.display.on_system("resumed")
            return True
        if low in ("/abort", "/kill"):
            controller.instance.abort()
            controller.display.on_system("aborted (LifecycleAbort)")
            return True
        if low == "/reset":
            if mode == OperatorMode.HITL:
                controller.display.on_system("reset refused: resolve HITL first")
                return True
            controller.reset_session()
            return True
        if low in ("/help", "/?"):
            self._print_help()
            return True
        return False

    def _print_help(self) -> None:
        self.err.write(
            "commands: /reset /pause /resume /abort /steer <text> /skills /skill <name> /quit\n"
        )
        self.err.flush()


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (OSError, ValueError):  # a closed or detached stream is not a terminal
        return False

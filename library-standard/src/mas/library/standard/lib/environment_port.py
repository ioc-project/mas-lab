#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Per-run environment port, passed through a context variable.

Mirrors ``mas.runtime.boundary.obs.event_stream``: the lab binds a port
around an in-process run; ``environment@v1`` reads it at the governance
synchronous point. No environment variables. No lab import.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Mapping, Protocol

from mas.library.standard.lib.triggers import Trigger

_current: ContextVar["EnvironmentPort | None"] = ContextVar("mas_environment_port", default=None)


class Anchor(Protocol):
    hook: str
    op: str
    tool: str
    agent: str
    occurrence: int


class EnvironmentPort(Protocol):
    """Synchronous world around one run. Missing methods mean that action is refused."""

    def observe(
        self,
        on: str,
        *,
        tool: str = "",
        agent: str = "",
        hook: str = "",
        op: str = "",
    ) -> None: ...

    def fire(self, trigger_id: str, anchor: Anchor) -> None: ...

    def triggers(self) -> tuple[Trigger, ...]: ...

    def note_error(self, message: str) -> None: ...

    def bind_control(
        self,
        control: object | None,
        *,
        session_id: str = "",
        entry_agent: str = "",
    ) -> None: ...

    def run_named_action(self, name: str) -> None: ...

    def emit_named(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def deliver_event(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> bool: ...

    def fixture_url(self, name: str) -> str: ...


def get_environment_port() -> EnvironmentPort | None:
    return _current.get()


def set_environment_port(port: EnvironmentPort | None) -> Token:
    return _current.set(port)


def reset_environment_port(token: Token) -> None:
    _current.reset(token)

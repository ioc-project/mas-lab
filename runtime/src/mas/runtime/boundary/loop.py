#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent loop: the seam between what reaches an agent and what runs the task.

A host (an A2A listener, a lab run) hands a task to a loop and gets a reply. The kernel main loop is
one loop and the default. Another loop may drive an external harness. Transports, identity,
governance at the boundary and observability stay with the host, outside this interface.

This module is the interface only. It names no harness and adds nothing to the kernel.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol


@dataclass(frozen=True)
class LoopTask:
    """One task for a loop.

    ``session_id`` is the conversation (the A2A ``contextId``); empty starts a new one. ``task_id`` names
    this turn. ``caller`` is an opaque reference to the caller's identity, never a credential.
    ``headers`` is the string metadata that came with the call (trace headers, ``mas.*`` keys).
    """

    text: str
    session_id: str = ""
    task_id: str = ""
    caller: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    parent_call_id: str = ""
    upstream_correlation_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True)
class LoopReply:
    """What a loop returns.

    ``stream`` holds text chunks when the loop streamed; a host that cannot stream joins them.
    ``events`` are the loop's own events when its protocol reports any (a harness's tool calls, say);
    each is a plain dict. A kernel-backed loop leaves it empty: the kernel's events go through its
    own observability.
    """

    text: str = ""
    context_id: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    task_state: str = ""
    stream: tuple[Any, ...] = ()
    events: tuple[dict[str, Any], ...] = ()


class LoopError(RuntimeError):
    """The loop could not complete the task (the harness failed, timed out or broke its protocol)."""


class LoopDenied(LoopError):
    """Governance at the boundary refused the task or the reply. ``policy`` names who, ``reason`` why."""

    def __init__(self, decision: str, policy: str, reason: str) -> None:
        super().__init__(f"{decision} by {policy}: {reason}")
        self.decision = decision
        self.policy = policy
        self.reason = reason


class AgentLoop(Protocol):
    """Take a task, return a reply.

    ``loop_id`` is the plugin id (hyphenated). ``run`` blocks; a host calls it from a worker thread.
    ``close`` releases what the loop holds (a child process, a connection) and is safe to call twice.
    """

    loop_id: str

    def run(self, task: LoopTask) -> LoopReply: ...

    def close(self) -> None: ...

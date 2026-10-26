#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ControlContract — pause, steer, list, and walk snapshots.

Plugin code, an LLM tool advertisement, and an admin CLI all call the
same methods. The tool is never a second implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

Surface = Literal["plugin", "llm", "admin"]
QueueAt = int | Literal["head", "tail"]


class SessionPaused(RuntimeError):
    """The session is paused; user turns are refused until ``resume``."""

    def __init__(self, session_id: str, *, reason: str = "") -> None:
        self.session_id = session_id
        self.reason = reason
        extra = f": {reason}" if reason else ""
        super().__init__(f"session {session_id!r} is paused{extra}")


class ControlDenied(RuntimeError):
    """A control verb was refused; the refusal is still traced."""

    def __init__(self, method: str, session_id: str, *, reason: str) -> None:
        self.method = method
        self.session_id = session_id
        self.reason = reason
        super().__init__(f"{method} denied for session {session_id!r}: {reason}")


class SessionBusy(RuntimeError):
    """A second exclusive ``run_turn`` while a turn is already in flight.

    A2A Send Message (``message/send``) is :meth:`ControlContract.send_message`:
    additional input on a non-terminal task, queued at the tail. This error
    is only for starting another exclusive user turn on the same session.
    """

    def __init__(self, session_id: str, *, method: str = "run_turn") -> None:
        self.session_id = session_id
        self.method = method
        super().__init__(
            f"{method} refused for session {session_id!r}: a turn is in flight"
        )


class SessionNotStopped(RuntimeError):
    """Snapshot/persist require a paused session, or ``auto_stop=True``."""

    def __init__(self, session_id: str, *, method: str = "persist") -> None:
        self.session_id = session_id
        self.method = method
        super().__init__(
            f"{method} refused for session {session_id!r}: session is not stopped "
            "(pause first, or pass --auto-stop)"
        )


class QueueConflict(RuntimeError):
    """A queue mutation used a stale ``revision`` from an earlier peek."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"queue revision {expected} is stale (now {actual}); peek and retry"
        )


class QueueItemGone(KeyError):
    """The queued id was already popped, cancelled, or never existed."""

    def __init__(self, input_id: str) -> None:
        self.input_id = input_id
        super().__init__(f"unknown queued input {input_id!r}")


@dataclass(frozen=True)
class ControlCapability:
    """Least-privilege token for one caller of ``ControlContract``."""

    actor: str
    surface: Surface
    session_ids: frozenset[str] | None = None
    methods: frozenset[str] | None = None

    def allows(self, method: str, session_id: str) -> bool:
        if self.session_ids is not None and session_id not in self.session_ids:
            return False
        if self.methods is not None and method not in self.methods:
            return False
        return True


@dataclass(frozen=True)
class ControlEvent:
    """One governed control action, reconstructable without a private index."""

    kind: str
    session_id: str
    actor: str
    surface: Surface
    method: str
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    denied: bool = False
    taken_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class QueuedInputView:
    input_id: str
    text: str
    source: str
    priority: int = 0
    action: Literal["turn", "steer"] = "turn"
    actor: str = ""
    revision: int = 0


@dataclass(frozen=True)
class QueueView:
    """Atomic snapshot of one session queue. ``revision`` is the CAS token."""

    session_id: str
    revision: int
    items: tuple[QueuedInputView, ...] = ()


@dataclass(frozen=True)
class SessionSnapshotView:
    session_id: str
    status: str
    turn: int
    live_snapshot_id: str | None
    cursor_snapshot_id: str | None
    spec_revision: int | None = None


@dataclass(frozen=True)
class ContextUsageView:
    """Privacy-safe token and cache snapshot from the latest assembled request."""

    session_id: str
    available: bool = False
    captured_at: str = ""
    model: str = ""
    context_window: int | None = None
    estimated_prompt_tokens: int = 0
    completion_reserve: int = 0
    estimated_remaining_tokens: int | None = None
    fill_ratio: float | None = None
    token_breakdown: dict[str, int] = field(default_factory=dict)
    context_parts: tuple[dict[str, Any], ...] = ()
    latest_provider_usage: dict[str, Any] = field(default_factory=dict)
    provider_usage_source: str = "unavailable"
    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float | None = None
    cache_hits_by_layer: dict[str, int] = field(default_factory=dict)
    cache_misses_by_layer: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    cost_status: str = "pricing_not_configured"
    archive_view_id: str = ""
    archive_covering_ids: tuple[str, ...] = ()
    system_prefix_hash: str = ""
    system_prefix_reused: bool = False


class ControlContract(Protocol):
    """Capability-scoped control plane. Callers pass ``session_id``, not objects.

    Inbound user turns are a subset of this contract: A2A ``message/send``
    is :meth:`send_message`. :meth:`steer` is control-protocol only — A2A
    has no steer RPC. Default ``steer`` preempts a working decode (keep
    the prefix, replace the rest). ``mode="replace"`` discards the
    prefix and starts a new turn. ``mode="after"`` waits for the current
    generation to finish, then runs next (queue to front).
    """

    def pause(self, session_id: str, *, reason: str) -> None: ...
    def resume(self, session_id: str) -> None: ...
    def send_message(
        self,
        session_id: str,
        *,
        text: str,
        source: str = "user",
    ) -> str: ...
    def steer(
        self,
        session_id: str,
        *,
        text: str,
        mode: Literal[
            "preempt", "replace", "after", "amend", "enqueue", "inject_now"
        ] = "preempt",
        at: QueueAt = "head",
    ) -> None: ...
    def discard_last(self, session_id: str) -> str: ...
    def inspect(self, session_id: str) -> SessionSnapshotView: ...
    def inspect_context(self, session_id: str) -> ContextUsageView: ...
    def inspect_archive(self, session_id: str) -> dict[str, Any]: ...
    def snapshot(self, session_id: str, *, label: str = "", auto_stop: bool = False) -> Any: ...
    def persist(
        self,
        session_id: str,
        *,
        snapshot_id: str | None = None,
        label: str = "",
        auto_stop: bool = False,
    ) -> Any: ...
    def run_script(
        self,
        session_id: str,
        *,
        text: str = "",
        script_file: str = "",
        auto_stop: bool = False,
    ) -> Any: ...
    def list_checkpoints(self, session_id: str) -> list[Any]: ...
    def navigate(self, session_id: str, *, to: str, reason: str) -> Any: ...
    def enqueue_input(
        self,
        session_id: str,
        *,
        text: str,
        source: str,
        priority: int = 0,
        action: Literal["turn", "steer"] = "turn",
        at: QueueAt = "tail",
    ) -> str: ...
    def peek_queue(self, session_id: str) -> list[QueuedInputView]: ...
    def inspect_queue(self, session_id: str) -> QueueView: ...
    def reorder_queue(
        self,
        session_id: str,
        order: list[str],
        *,
        revision: int | None = None,
    ) -> None: ...
    def cancel_queued(
        self,
        session_id: str,
        input_id: str,
        *,
        revision: int | None = None,
    ) -> None: ...
    def set_queued_action(
        self,
        session_id: str,
        input_id: str,
        *,
        action: Literal["turn", "steer"],
        revision: int | None = None,
    ) -> None: ...
    def cancel_inflight(self, session_id: str) -> bool: ...
    def fork_investigation(self, session_id: str) -> str: ...
    def disable_tool(self, session_id: str, *, name: str, reason: str) -> int: ...

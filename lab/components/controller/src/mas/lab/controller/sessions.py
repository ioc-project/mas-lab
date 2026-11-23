#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Client session registry — idle auto-shutdown when no users and no workers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from mas.lab.controller.worker_model import WorkerStatus
from mas.runtime import hostenv

if TYPE_CHECKING:
    from mas.lab.controller.api import ControllerAPI

DEFAULT_IDLE_SHUTDOWN_SEC = 30.0


def idle_shutdown_sec() -> float:
    """``MAS_CONTROLLER_IDLE_SEC``, read when asked: the daemon installs the host environment first."""
    raw = hostenv.get("MAS_CONTROLLER_IDLE_SEC", "")
    return float(raw) if str(raw).strip() else DEFAULT_IDLE_SHUTDOWN_SEC


class SessionRegistry:
    """Track CLI / test client sessions; signal idle shutdown when empty."""

    def __init__(self) -> None:
        self._sessions: dict[str, float] = {}
        self._idle_since: Optional[float] = None

    def acquire(self, session_id: str) -> None:
        self._sessions[session_id] = time.time()
        self._idle_since = None

    def release(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def touch(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._sessions[session_id] = time.time()

    def count(self) -> int:
        return len(self._sessions)

    def active_worker_count(self, api: ControllerAPI) -> int:
        return sum(1 for w in api.workers.list_workers() if w.status in (WorkerStatus.PENDING, WorkerStatus.RUNNING))

    def should_shutdown(self, api: ControllerAPI, *, idle_sec: float | None = None) -> bool:
        """True when no sessions, no active workers, and idle for *idle_sec*."""
        threshold = idle_sec if idle_sec is not None else idle_shutdown_sec()
        if self._sessions or self.active_worker_count(api) > 0:
            self._idle_since = None
            return False
        now = time.time()
        if self._idle_since is None:
            self._idle_since = now
            return threshold <= 0
        return (now - self._idle_since) >= threshold

    def status(self) -> dict:
        return {
            "sessions": self.count(),
            "idle_since": self._idle_since,
            "idle_shutdown_sec": idle_shutdown_sec(),
        }

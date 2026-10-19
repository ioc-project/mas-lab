#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Worker model types."""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class WorkerKind(str, enum.Enum):
    APPLICATION = "application"
    BENCHMARK = "benchmark"
    EXPERIMENT = "experiment"
    PIPELINE = "pipeline"


class WorkerStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkerRecord:
    """In-memory worker state owned by the controller daemon."""

    id: str
    kind: WorkerKind
    status: WorkerStatus = WorkerStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    command: str = ""
    endpoint: str = ""
    spec: Dict[str, Any] = field(default_factory=dict)
    stdout: List[str] = field(default_factory=list)
    stderr: List[str] = field(default_factory=list)
    stdout_text: str = ""
    stderr_text: str = ""
    result: Any = None
    parent_id: Optional[str] = None
    pid: Optional[int] = None

    def append_stdout(self, line: str) -> None:
        self.stdout.append(line)

    def append_stderr(self, line: str) -> None:
        self.stderr.append(line)

    def append_stdout_chunk(self, text: str) -> None:
        self.stdout_text += text

    def append_stderr_chunk(self, text: str) -> None:
        self.stderr_text += text

    def combined_stdout(self) -> str:
        parts = []
        if self.stdout:
            parts.append("\n".join(self.stdout))
        if self.stdout_text:
            parts.append(self.stdout_text)
        return "\n".join(parts)

    def combined_stderr(self) -> str:
        parts = []
        if self.stderr:
            parts.append("\n".join(self.stderr))
        if self.stderr_text:
            parts.append(self.stderr_text)
        return "\n".join(parts)

    def to_job_summary(self) -> Dict[str, Any]:
        origin = _ui_origin(self.spec)
        summary = {
            "id": self.id,
            "endpoint": self.endpoint,
            "command": self.command,
            "status": self._job_status(),
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "pid": self.pid,
            "exit_code": self.exit_code,
            "origin": origin,
        }
        if self.error:
            summary["error"] = self.error
        return summary

    def to_job_detail(self) -> Dict[str, Any]:
        detail = self.to_job_summary()
        detail.update(
            {
                "stdout": self.combined_stdout(),
                "stderr": self.combined_stderr(),
                "error": self.error,
                "request_body": self.spec,
            }
        )
        return detail

    def _job_status(self) -> str:
        mapping = {
            WorkerStatus.PENDING: "pending",
            WorkerStatus.RUNNING: "running",
            WorkerStatus.COMPLETED: "completed",
            WorkerStatus.FAILED: "failed",
            WorkerStatus.CANCELLED: "cancelled",
        }
        return mapping.get(self.status, "running")


def _ui_origin(spec: Dict[str, Any] | None) -> str | None:
    ref = (spec or {}).get("demo_ui_ref") if isinstance(spec, dict) else None
    if not isinstance(ref, dict) or not ref:
        return None
    return str(ref.get("origin") or "demo-ui")


def new_worker_id(prefix: str = "w") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

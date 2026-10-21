#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Persisted runs (job store rebuilt on the run/artifact model)."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE = """\
CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    scope            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    item             TEXT NOT NULL,
    status           TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    created_at       TEXT NOT NULL,
    exit_code        INTEGER,
    request_summary  TEXT NOT NULL DEFAULT '{}',
    job_id           TEXT,
    result_dir       TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Run:
    id: str
    scope: str
    kind: str
    item: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    request_summary: dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    result_dir: Optional[str] = None
    log_lines: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "kind": self.kind,
            "item": self.item,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "request_summary": self.request_summary,
            "job_id": self.job_id,
            "log": "\n".join(self.log_lines),
        }


class RunStore:
    def __init__(self, db_path: Path | None = None):
        self._memory: dict[str, Run] = {}
        self._conn: sqlite3.Connection | None = None
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(_CREATE)
            self._conn.commit()
            self._load()

    def _load(self) -> None:
        if self._conn is None:
            return
        rows = self._conn.execute(
            "SELECT id, scope, kind, item, status, started_at, finished_at, "
            "created_at, exit_code, request_summary, job_id, result_dir FROM runs"
        ).fetchall()
        for row in rows:
            summary = row[9]
            try:
                summary_obj = json.loads(summary) if summary else {}
            except json.JSONDecodeError:
                summary_obj = {}
            run = Run(
                id=row[0],
                scope=row[1],
                kind=row[2],
                item=row[3],
                status=row[4],
                started_at=row[5],
                finished_at=row[6],
                created_at=row[7],
                exit_code=row[8],
                request_summary=summary_obj,
                job_id=row[10],
                result_dir=row[11],
            )
            self._memory[run.id] = run

    def save(self, run: Run) -> None:
        self._memory[run.id] = run
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(id, scope, kind, item, status, started_at, finished_at, created_at, "
            "exit_code, request_summary, job_id, result_dir) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.scope,
                run.kind,
                run.item,
                run.status,
                run.started_at,
                run.finished_at,
                run.created_at,
                run.exit_code,
                json.dumps(run.request_summary),
                run.job_id,
                run.result_dir,
            ),
        )
        self._conn.commit()

    def get(self, run_id: str) -> Run | None:
        return self._memory.get(run_id)

    def list(self) -> list[Run]:
        return sorted(self._memory.values(), key=lambda r: r.created_at, reverse=True)

    def delete(self, run_id: str) -> None:
        self._memory.pop(run_id, None)
        if self._conn is not None:
            self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def new_run(*, scope: str, kind: str, item: str, request_summary: dict | None = None) -> Run:
    return Run(
        id=str(uuid.uuid4()),
        scope=scope,
        kind=kind,
        item=item,
        status="pending",
        created_at=_now(),
        request_summary=request_summary or {},
    )


_store: RunStore | None = None


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store


def init_run_store(db_path: Path | None) -> RunStore:
    global _store
    if _store is not None:
        _store.close()
    _store = RunStore(db_path)
    return _store


def set_run_store(store: RunStore | None) -> None:
    global _store
    _store = store

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Durable job store — SQLite (default) or ephemeral (no-op).

The store persists job metadata so that ``GET /api/jobs`` survives a
server restart. Stdout/stderr are *not* stored in the DB (they can be
multi-MB); instead they are teed to a log file whose path is kept in
the job record.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    endpoint    TEXT NOT NULL,
    command     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    pid         INTEGER,
    exit_code   INTEGER,
    error       TEXT,
    request_body TEXT NOT NULL DEFAULT '{}',
    log_path    TEXT
);
"""


class JobStore(ABC):
    """Minimal interface for job persistence."""

    @abstractmethod
    def save(self, job_dict: dict[str, Any]) -> None:
        """Upsert a job record (called on every state transition)."""

    @abstractmethod
    def load_all(self) -> list[dict[str, Any]]:
        """Return every persisted job as a dict."""

    @abstractmethod
    def get(self, job_id: str) -> dict[str, Any] | None:
        """Return a single job by id, or ``None``."""

    @abstractmethod
    def delete(self, job_id: str) -> None:
        """Remove a job record."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""


class EphemeralJobStore(JobStore):
    """No-op store — nothing is persisted. Used with ``--ephemeral``."""

    def save(self, job_dict: dict[str, Any]) -> None:
        pass

    def load_all(self) -> list[dict[str, Any]]:
        return []

    def get(self, job_id: str) -> dict[str, Any] | None:
        return None

    def delete(self, job_id: str) -> None:
        pass

    def close(self) -> None:
        pass


_COLUMNS = (
    "id", "endpoint", "command", "status",
    "created_at", "started_at", "finished_at",
    "pid", "exit_code", "error", "request_body", "log_path",
)


class SqliteJobStore(JobStore):
    """SQLite-backed durable store.

    Parameters
    ----------
    db_path:
        Filesystem path for the SQLite database file.  Parent directories
        are created automatically.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        logger.info("Job store opened: %s", db_path)

    def save(self, job_dict: dict[str, Any]) -> None:
        request_body = job_dict.get("request_body")
        if isinstance(request_body, dict):
            request_body = json.dumps(request_body)
        elif request_body is None:
            request_body = "{}"

        values = (
            job_dict.get("id"),
            job_dict.get("endpoint", ""),
            job_dict.get("command", ""),
            job_dict.get("status", "pending"),
            job_dict.get("created_at", ""),
            job_dict.get("started_at"),
            job_dict.get("finished_at"),
            job_dict.get("pid"),
            job_dict.get("exit_code"),
            job_dict.get("error"),
            request_body,
            job_dict.get("log_path"),
        )

        self._conn.execute(
            "INSERT OR REPLACE INTO jobs "
            "(id, endpoint, command, status, created_at, started_at, "
            "finished_at, pid, exit_code, error, request_body, log_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        self._conn.commit()

    def load_all(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM jobs ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get(self, job_id: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(row: tuple) -> dict[str, Any]:
        d: dict[str, Any] = dict(zip(_COLUMNS, row, strict=True))
        rb = d.get("request_body")
        if isinstance(rb, str):
            try:
                d["request_body"] = json.loads(rb)
            except json.JSONDecodeError:
                d["request_body"] = {}
        return d


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: JobStore | None = None


def get_job_store() -> JobStore:
    """Return the active store.  Raises if ``init_job_store`` hasn't been called."""
    if _store is None:
        return EphemeralJobStore()
    return _store


def init_job_store(*, db_path: Path | None = None) -> JobStore:
    """Initialise the module-level singleton.

    Parameters
    ----------
    db_path:
        Path to the SQLite file.  ``None`` → ephemeral (no-op).
    """
    global _store
    if _store is not None:
        _store.close()
    if db_path is None:
        _store = EphemeralJobStore()
    else:
        _store = SqliteJobStore(db_path)
    return _store


def set_job_store(store: JobStore | None) -> None:
    """Override for tests."""
    global _store
    _store = store

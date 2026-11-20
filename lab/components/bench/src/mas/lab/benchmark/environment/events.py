#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Environment event log: value-free records in environment.jsonl."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from mas.lab.benchmark.environment.clock import RunClock
from mas.lab.benchmark.environment.model import Limits, RunCoordinates

logger = logging.getLogger(__name__)

CLOSED_SET = frozenset(
    {
        "fixture.starting",
        "fixture.ready",
        "fixture.failed",
        "fixture.stopped",
        "clock.started",
        "clock.advanced",
        "timer.armed",
        "timer.fired",
        "trigger.matched",
        "trigger.skipped",
        "action.started",
        "action.done",
        "action.failed",
        "action.timeout",
        "event.emitted",
        "event.delivered",
        "control.applied",
        "environment.closed",
    }
)
NAME_RE = re.compile(r"^[a-z0-9._:-]{1,128}$")
ID_RE = re.compile(r"^[a-z0-9._:-]{1,128}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "body",
        "json",
        "header",
        "headers",
        "authorization",
        "token",
        "secret",
        "password",
        "credential",
        "query",
        "url",
        "text",
        "content",
        "prompt",
        "argument",
        "arguments",
        "payload",
    }
)
FORBIDDEN_PAYLOAD_SUBSTR = ("token", "secret", "password", "credential", "authorization")


def kind_for(name: str) -> str:
    prefix = name.split(".", 1)[0]
    if prefix in {"fixture", "clock", "timer", "trigger", "action", "event", "environment", "control", "user"}:
        return prefix if prefix != "user" else "event"
    return "event"


def _payload_ok(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        lowered = str(key).lower()
        if lowered in FORBIDDEN_PAYLOAD_KEYS or any(part in lowered for part in FORBIDDEN_PAYLOAD_SUBSTR):
            raise ValueError(f"environment payload key {key!r} is not value-free")
        if isinstance(value, str):
            if value and not (ID_RE.fullmatch(value) or SHA_RE.fullmatch(value) or _numeric_string(value)):
                raise ValueError(f"environment payload {key!r} is not value-free")
        elif isinstance(value, (int, float, bool)) or value is None:
            continue
        else:
            raise ValueError(f"environment payload {key!r} must be a number, bool, id or digest")


def _numeric_string(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


class EventLog:
    """Append-only, schema-shaped records. seq strictly increases."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        clock: RunClock,
        run: RunCoordinates,
        limits: Limits,
        on_record: Callable[[dict[str, Any]], None] | None = None,
        reset: bool = True,
    ) -> None:
        self.path = path
        self.clock = clock
        self.run = run
        self.limits = limits
        self._on_record = on_record
        self._lock = threading.Lock()
        self._seq = 0
        self._records: list[dict[str, Any]] = []
        self._closed = False
        self._truncated = False
        self._disk_failed = False
        self._handle: Any = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if reset or not path.is_file():
                path.write_text("", encoding="utf-8")
            elif path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        self._records.append(rec)
                        self._seq = max(self._seq, int(rec.get("seq") or 0))

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._records)

    def read(self, since_seq: int = 0) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in self._records if int(item.get("seq") or 0) > since_seq)

    def emit(
        self,
        name: str,
        *,
        source: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        anchor: Mapping[str, Any] | None = None,
        outcome: str = "ok",
        event_id: str = "",
    ) -> dict[str, Any]:
        folded = str(name or "").strip().lower()
        if folded.startswith("user."):
            if not NAME_RE.fullmatch(folded):
                raise ValueError(f"event name {name!r} is not valid")
        elif folded not in CLOSED_SET:
            raise ValueError(f"unknown environment event {name!r}")
        body = dict(payload or {})
        _payload_ok(body)
        with self._lock:
            if self._closed:
                raise RuntimeError("environment event log is closed")
            # The last slot belongs to environment.closed. Past the cap the log stops
            # growing instead of failing the run that the environment only observes.
            if self._seq >= self.limits.max_events_per_run - 1 and folded != "environment.closed":
                if not self._truncated:
                    self._truncated = True
                    logger.warning("environment event log truncated at %d records", self._seq)
                return {"v": 1, "seq": 0, "id": "truncated", "name": folded, "outcome": "dropped", "payload": {}}
            self._seq += 1
            key = event_id or f"{folded}:{self._seq}"
            record = {
                "v": 1,
                "seq": self._seq,
                "id": key,
                "name": folded,
                "kind": kind_for(folded),
                "source": dict(source or {"type": "environment", "id": "environment"}),
                "time": {
                    "clock": self.clock.kind,
                    "wall": self.clock.wall_iso(),
                    "elapsed_ms": round(self.clock.elapsed_ms(), 1),
                    **({"virtual_s": self.clock.now()} if self.clock.kind == "virtual" else {}),
                },
                "run": {
                    "experiment": self.run.experiment,
                    "scenario": self.run.scenario,
                    "item": self.run.item,
                    "run": self.run.run,
                    "attempt": self.run.attempt,
                },
                "outcome": outcome,
                "payload": body,
            }
            if anchor:
                record["anchor"] = dict(anchor)
            self._records.append(record)
            if self.path is not None and not self._disk_failed:
                try:
                    if self._handle is None:
                        self._handle = self.path.open("a", encoding="utf-8")
                    self._handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
                    self._handle.flush()  # a reader (the live view, a crash) sees every record at once
                except OSError:
                    # A full or vanished disk must not fail the run this log only observes.
                    self._disk_failed = True
                    logger.warning("environment log %s is no longer writable; keeping records in memory", self.path)
            callback = self._on_record
        if callback is not None:
            callback(record)
        return record

    def close(self) -> dict[str, Any]:
        record = self.emit(
            "environment.closed",
            source={"type": "environment", "id": "environment"},
            payload={"truncated": 1} if self._truncated else {},
        )
        with self._lock:
            self._closed = True
            handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                logger.debug("environment log close failed", exc_info=True)
        return record

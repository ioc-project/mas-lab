#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event emission — terminating protocol (stdout, file)."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable


def envelope_only_kind(kind: str) -> bool:
    """True for envelope kinds that must not land in ``events.jsonl``."""
    text = str(kind or "")
    return text.startswith(("run.", "artifact.", "control.", "scope.")) or text == "metric"


@runtime_checkable
class EventEmitter(Protocol):
    """Ctl termination: persist or stream serialized event records."""

    def emit(self, record: dict) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class JsonlFileEmitter:
    """Append native records to ``events.jsonl``. Truncates on open."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(b"")
        self._lock = threading.Lock()

    def emit(self, record: dict) -> None:
        if envelope_only_kind(str(record.get("kind") or "")):
            return
        line = json.dumps(record, default=str) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    @property
    def path(self):
        return self._path


class StdoutJsonlEmitter:
    """Emit JSONL lines on stdout or stderr (terminating protocol — ctl-owned)."""

    def __init__(self, *, stream=None) -> None:
        self._stream = stream or sys.stderr

    def emit(self, record: dict) -> None:
        self._stream.write(json.dumps(record, default=str) + "\n")
        self._stream.flush()

    def flush(self) -> None:
        self._stream.flush()

    def close(self) -> None:
        pass


class NullEmitter:
    def emit(self, record: dict) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class FanOutEmitter:
    def __init__(self, *emitters: EventEmitter) -> None:
        self._emitters = emitters

    def emit(self, record: dict) -> None:
        for e in self._emitters:
            e.emit(record)

    def flush(self) -> None:
        for e in self._emitters:
            e.flush()

    def close(self) -> None:
        for e in self._emitters:
            e.close()

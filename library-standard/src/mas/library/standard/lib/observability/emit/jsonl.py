#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event emission — terminating protocol (stdout, file)."""

from __future__ import annotations

import json
import os
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
    """Append native records to ``events.jsonl``. Truncates once, when the emitter is created.

    The file is opened once in append mode and kept open for the life of the
    emitter. Each ``emit`` is one unbuffered write of one whole line, so lines
    of two emitters (or two threads) on one path never interleave. ``close``
    releases the handle; an ``emit`` after ``close`` raises.
    """

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fd: int | None = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_TRUNC, 0o644)

    def emit(self, record: dict) -> None:
        skip = envelope_only_kind(str(record.get("kind") or ""))
        data = b"" if skip else (json.dumps(record, default=str) + "\n").encode("utf-8")
        with self._lock:
            if self._fd is None:
                raise RuntimeError("JsonlFileEmitter.emit after close")
            if skip:
                return
            view = memoryview(data)
            while view:
                view = view[os.write(self._fd, view) :]

    def flush(self) -> None:
        pass

    def close(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
            if fd is not None:
                os.close(fd)

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

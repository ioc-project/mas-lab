#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event emission — terminating protocol (stdout, file, OTLP push)."""

from __future__ import annotations

import json
import sys
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
    """Append JSONL records to a file (native observability parity)."""

    def __init__(self, path) -> None:
        from pathlib import Path

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(b"")

    def emit(self, record: dict) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

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

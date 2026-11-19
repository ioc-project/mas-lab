#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One log line per kind of problem, then a counter. Never a traceback per call."""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

_logger = logging.getLogger("mas.library.telemetry.transport")

_lock = Lock()
_seen: dict[tuple[str, str], int] = {}


def warn_once(kind: str, detail: str, message: str, *args: Any) -> None:
    """Log ``message`` at WARNING the first time ``(kind, detail)`` is seen."""
    key = (kind, detail)
    with _lock:
        count = _seen.get(key, 0)
        _seen[key] = count + 1
        first = count == 0
    if first:
        _logger.warning(message, *args)


def count(kind: str, detail: str = "") -> int:
    with _lock:
        return _seen.get((kind, detail), 0)


def counts() -> dict[tuple[str, str], int]:
    with _lock:
        return dict(_seen)


def reset() -> None:
    with _lock:
        _seen.clear()


__all__ = ["count", "counts", "reset", "warn_once"]

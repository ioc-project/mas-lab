#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Wall and virtual run clocks."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from mas.lab.benchmark.environment.model import ClockSpec


class RunClock:
    """One clock per run. Virtual time only moves on ``advance`` / ``tick``."""

    def __init__(self, spec: ClockSpec | None = None) -> None:
        self.spec = spec or ClockSpec()
        self.kind = self.spec.kind
        self._origin_mono = time.monotonic()
        self._origin_wall = time.time()
        self._virtual_s = float(self.spec.start_s)
        self._epoch = _parse_epoch(self.spec.epoch)
        self._on_advance: list[Callable[[float], None]] = []

    def on_advance(self, callback: Callable[[float], None]) -> None:
        self._on_advance.append(callback)

    def now(self) -> float:
        if self.kind == "virtual":
            return self._virtual_s
        return time.monotonic() - self._origin_mono

    def wall_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._origin_mono) * 1000.0

    def epoch_seconds(self) -> float:
        if self.kind == "virtual":
            return self._epoch + self._virtual_s
        return time.time()

    def advance(self, seconds: float) -> float:
        if self.kind != "virtual":
            raise RuntimeError("clock_advance is only valid on a virtual clock")
        if seconds < 0:
            raise ValueError("clock_advance by must be >= 0")
        self._virtual_s += float(seconds)
        for callback in list(self._on_advance):
            callback(self._virtual_s)
        return self._virtual_s

    def tick(self, event: str) -> float:
        delta = float(self.spec.tick.get(event) or 0.0)
        if delta <= 0:
            return self.now()
        if self.kind != "virtual":
            return self.now()
        return self.advance(delta)

    def check_sleep(self, seconds: float, *, bound_s: float) -> None:
        if seconds < 0:
            raise ValueError("sleep by must be >= 0")
        if seconds > bound_s:
            raise ValueError(f"sleep {seconds}s exceeds limits.max_sleep_s {bound_s}")
        if self.kind == "virtual":
            raise RuntimeError("sleep is only valid on a wall clock; use clock_advance")

    def sleep(self, seconds: float, *, bound_s: float) -> float:
        self.check_sleep(seconds, bound_s=bound_s)
        time.sleep(seconds)
        return self.now()


def _parse_epoch(text: str) -> float:
    raw = str(text or "").strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()

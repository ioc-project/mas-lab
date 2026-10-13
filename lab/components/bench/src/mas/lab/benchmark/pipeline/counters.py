#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Test-hook counters for pipeline v1/v2 parity and the efficiency budget."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PipelineCounters:
    judge_calls: int = 0
    llm_calls: int = 0
    events_bytes_read: int = 0
    store_hits: int = 0
    store_misses: int = 0
    lifecycle: dict[str, int] = field(default_factory=dict)

    def bump(self, name: str, n: int = 1) -> None:
        self.lifecycle[name] = int(self.lifecycle.get(name) or 0) + n

    def snapshot(self) -> dict[str, int]:
        out = {
            "judge_calls": self.judge_calls,
            "llm_calls": self.llm_calls,
            "events_bytes_read": self.events_bytes_read,
            "store_hits": self.store_hits,
            "store_misses": self.store_misses,
        }
        out.update(self.lifecycle)
        return out


_ACTIVE = PipelineCounters()


def counters() -> PipelineCounters:
    return _ACTIVE


def reset_counters() -> PipelineCounters:
    global _ACTIVE
    _ACTIVE = PipelineCounters()
    return _ACTIVE

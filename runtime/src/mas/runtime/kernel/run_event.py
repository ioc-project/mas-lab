#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Accepted facts on the run log.

The next model prompt is composed of three functions:

1. Append an accepted fact (the engine return, or a placeholder when a
   tool did not run). Pause, retry, and hard halt skip this step.
2. After the kernel step, take ``run.since(snapshot)`` — rows whose
   ``seq`` is new. Parallel returns re-sort the list by id, so a new
   row can land in the middle; ``seq`` does not move with the index.
3. Copy each of those rows into the chat transcript.

A new governance action needs an explicit arm and a test that checks
whether a log row was appended. Do not put vendor prompt JSON in the
kernel.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mas.runtime.kernel.state import RunEvent
from mas.runtime.schema.ingress import EngineIoReturn


def from_engine_return(event: EngineIoReturn) -> RunEvent:
    parallel = tuple((str(spec.tool_name), dict(spec.tool_arguments or {})) for spec in (event.parallel_tools or ()))
    return RunEvent(
        correlation_id=event.correlation_id,
        response_kind=event.response_kind,
        next_step=event.next_step,
        text=event.text or "",
        tool_name=event.tool_name or "",
        tool_arguments=dict(event.tool_arguments or {}),
        parallel_tools=parallel,
        finish_reason=event.finish_reason or "",
        provider_state=dict(event.provider_state or {}),
    )


def synthetic_tool_result(
    *,
    cid: int,
    text: str = "",
    next_step: str = "LLM_CALL",
) -> RunEvent:
    """Placeholder tool result (HITL skip/block, egress skip, blacklist)."""
    return RunEvent(
        correlation_id=cid,
        response_kind="TOOL_RESULT",
        next_step=next_step,
        text=text,
    )


def max_event_seq(events: Sequence[Any] | None) -> int:
    """Highest ``seq`` already on the log (0 if empty)."""
    if not isinstance(events, (list, tuple)) or not events:
        return 0
    return max(int(getattr(row, "seq", 0) or 0) for row in events)


def new_run_events(events: Sequence[Any] | None, seen_seq: int) -> list[Any]:
    """Rows stamped after ``seen_seq``. Prefer ``RunLedger.since`` in production."""
    if not isinstance(events, (list, tuple)):
        return []
    return [row for row in events if int(getattr(row, "seq", 0) or 0) > seen_seq]

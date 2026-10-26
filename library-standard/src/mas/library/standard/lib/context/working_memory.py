#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-turn working-memory slice for the assembler plugin.

``working_memory_messages`` is deprecated and ignored: the request is bounded
by the model window (plus completion reserve), not a message count.
``pin_policy: never`` tool groups stay in the slice if a caller still passes
a positive limit.
"""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.context.payload import start_of_tool_group
from mas.library.standard.lib.context.pinned_history import split_pinned_tool_groups


def working_memory_slice_limit(manifest: dict | None) -> int:
    """Max in-turn working-memory messages kept for assembly (0 = unbounded).

    The YAML key is accepted for old manifests and silently ignored.
    """
    _ = manifest
    return 0


def bounded_working_memory_tail(
    messages: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep at most the last *limit* messages, never splitting a tool-call group.

    ``pin_policy: never`` tool groups are always kept.
    """
    if limit <= 0 or len(messages) <= limit:
        return list(messages)
    start = start_of_tool_group(messages, len(messages) - limit)
    dropped, kept = list(messages[:start]), list(messages[start:])
    never_pin, _ = split_pinned_tool_groups(dropped)
    return never_pin + kept

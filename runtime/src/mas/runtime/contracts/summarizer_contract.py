#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SummarizerContract — sub-plugin of the summarising context manager.

The context manager owns recency (keep_turns) and the hysteresis cache.
A summarizer only turns older turns into summary text, or signals drop.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SummarizerContract(Protocol):
    """Compress older conversation turns. ``None`` / empty means drop them."""

    def summarize(self, messages: list[dict[str, Any]]) -> str | None | Awaitable[str | None]:
        """Return summary prose, or ``None`` to discard *messages*. May be ``async def``."""
        ...

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Start and stop for anything a call pipeline owns. A class opts in by deriving from :class:`Lifecycle`."""

from __future__ import annotations


class Lifecycle:
    """Base of every object a pipeline starts and stops: layers, engines, wrapped providers.

    ``start`` and ``stop`` do nothing here. A subclass overrides them; one that wraps another call
    forwards to it (a wrap layer's base class does).
    """

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def capabilities(self) -> dict[str, bool]:
        """What this object can do, as flags. A wrap layer merges the flags of the call below it."""
        return {}

    def tick(self) -> None:
        """Periodic work, run by the engine before each LLM call (a breaker probing a tool it took away)."""
        return None


def call_start(target: object) -> None:
    """Start *target* when it is a :class:`Lifecycle`. Anything else owns no lifecycle."""
    if isinstance(target, Lifecycle):
        target.start()


def call_tick(target: object) -> None:
    """Run the periodic work of *target* when it is a :class:`Lifecycle`."""
    if isinstance(target, Lifecycle):
        target.tick()


def call_stop(target: object) -> None:
    """Stop *target* when it is a :class:`Lifecycle`."""
    if isinstance(target, Lifecycle):
        target.stop()

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session-scoped spawn budgets and stable subagent identities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpawnLedger:
    """Enforce session-wide spawn count and per-branch recursion depth.

    Depth is tracked per agent, not as one counter per session: two children
    of the same parent are siblings at the same depth, whatever order they
    run in. A single session-wide counter would make the second sibling look
    like a grandchild of the first and refuse it against ``max_depth``.

    **No method here may ever ``await``.** ``allow_spawn()`` followed by
    ``enter()`` is a check-then-commit pair; under ``asyncio`` it is atomic
    only because no suspension point exists between them. Introducing one
    would let a second task pass the same check before the first commits.
    """

    max_depth: int = 3
    max_spawns: int | None = 8
    _agent_depth: dict[tuple[str, str], int] = field(default_factory=dict, init=False, repr=False)
    _spawn_count: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _sequence: dict[tuple[str, str], int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.max_depth, int) or isinstance(self.max_depth, bool) or self.max_depth < 1:
            raise ValueError("max_depth must be an integer >= 1")
        if self.max_spawns is not None and (
            not isinstance(self.max_spawns, int) or isinstance(self.max_spawns, bool) or self.max_spawns < 1
        ):
            raise ValueError("max_spawns must be None or an integer >= 1")

    def allow_spawn(self, session_id: str, parent_agent_id: str = "") -> bool:
        """Check both ceilings for one child of *parent_agent_id*, without mutating."""
        if not self.allows_child_depth(self.agent_depth(session_id, parent_agent_id)):
            return False
        return self.max_spawns is None or self._spawn_count.get(session_id, 0) < self.max_spawns

    def allows_child_depth(self, parent_depth: Any) -> bool:
        """Return whether one child under a typed, non-negative parent depth fits K."""
        return (
            isinstance(parent_depth, int)
            and not isinstance(parent_depth, bool)
            and parent_depth >= 0
            and parent_depth + 1 <= self.max_depth
        )

    def mint_agent_id(self, parent_agent_id: str, template_id: str) -> str:
        """Return a deterministic, unique child ID for this parent/template pair."""
        key = (parent_agent_id, template_id)
        sequence = self._sequence.get(key, 0) + 1
        self._sequence[key] = sequence
        return f"{parent_agent_id}.{template_id}.{sequence}"

    def enter(self, session_id: str, child_agent_id: str = "", parent_agent_id: str = "") -> None:
        """Account for one spawned child immediately before its nested turn."""
        depth = self.agent_depth(session_id, parent_agent_id) + 1
        if child_agent_id:
            self._agent_depth[(session_id, child_agent_id)] = depth
        self._spawn_count[session_id] = self._spawn_count.get(session_id, 0) + 1

    def exit(self, session_id: str, child_agent_id: str = "") -> None:
        """Release one child after its nested turn, including failures."""
        if not child_agent_id:
            return
        if self._agent_depth.pop((session_id, child_agent_id), None) is None:
            raise RuntimeError(f"spawn ledger underflow for session {session_id!r}")

    def agent_depth(self, session_id: str, agent_id: str) -> int:
        """Depth of one agent in its branch; unknown agents are roots."""
        if not agent_id:
            return 0
        return self._agent_depth.get((session_id, agent_id), 0)

    def spawn_count(self, session_id: str) -> int:
        """Total children started in one session."""
        return self._spawn_count.get(session_id, 0)

    def current_depth(self, session_id: str) -> int:
        """Deepest live branch in one session."""
        live = [depth for (sid, _agent), depth in self._agent_depth.items() if sid == session_id]
        return max(live, default=0)

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe copy of budgets, live depths, counts and minted sequences."""
        return {
            "max_depth": self.max_depth,
            "max_spawns": self.max_spawns,
            "agent_depth": [[sid, aid, depth] for (sid, aid), depth in self._agent_depth.items()],
            "spawn_count": dict(self._spawn_count),
            "sequence": [[parent, template, n] for (parent, template), n in self._sequence.items()],
        }

    def restore_spend(self, data: Mapping[str, Any] | None) -> None:
        """Put back what a snapshot says was spent, in place, keeping this ledger's ceilings and identity."""
        saved = SpawnLedger.from_snapshot(data)
        self._agent_depth = saved._agent_depth
        self._spawn_count = saved._spawn_count
        self._sequence = saved._sequence

    @classmethod
    def from_snapshot(cls, data: Mapping[str, Any] | None) -> SpawnLedger:
        """Rebuild a ledger from :meth:`snapshot`. An empty mapping is a fresh ledger."""
        blob = dict(data or {})
        max_spawns = blob.get("max_spawns", 8)
        ledger = cls(
            max_depth=int(blob["max_depth"]) if blob.get("max_depth") is not None else 3,
            max_spawns=None if max_spawns is None else int(max_spawns),
        )
        ledger._agent_depth = {(str(sid), str(aid)): int(depth) for sid, aid, depth in blob.get("agent_depth") or ()}
        ledger._spawn_count = {str(sid): int(count) for sid, count in dict(blob.get("spawn_count") or {}).items()}
        ledger._sequence = {(str(parent), str(template)): int(n) for parent, template, n in blob.get("sequence") or ()}
        return ledger

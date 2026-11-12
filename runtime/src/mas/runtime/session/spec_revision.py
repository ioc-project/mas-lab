#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live spec: the bound manifest can change, and every change is recorded."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mas.runtime.session.state import ManifestRef

_ALLOWED_DELTAS = frozenset(
    {"materialize", "disable_tool", "enable_tool", "set_pattern", "overlay_factor", "swap_layer_state"}
)

# Where call layers keep what they learn at run time (a tripped breaker, say), inside the running spec.
LAYER_STATE_KEY = "layer_state"

_CLAIM_ATTEMPTS = 10_000


@dataclass(frozen=True)
class SpecDelta:
    kind: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.kind not in _ALLOWED_DELTAS:
            raise ValueError(f"unknown spec delta {self.kind!r}")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SpecDelta":
        extra = sorted(set(data) - {"kind", "payload"})
        if extra:
            raise ValueError(f"unknown spec delta keys {extra}")
        return cls(str(data.get("kind") or ""), dict(data.get("payload") or {}))


@dataclass(frozen=True)
class SpecRevision:
    revision: int
    content_hash: str
    parent_revision: int | None
    actor: str
    reason: str
    delta: SpecDelta
    taken_at: str


class SpecRevisionLog:
    """Per-session spec spine. Restore a snapshot by replaying to that revision."""

    def __init__(self) -> None:
        self._revs: dict[str, list[SpecRevision]] = {}
        self._manifests: dict[str, dict[str, Any]] = {}
        self._heads: dict[str, int] = {}
        # one entry per (session, position in the log): whoever puts its token there first owns that position
        self._claims: dict[tuple[str, int], object] = {}

    def materialize(self, session_id: str, manifest: dict[str, Any], *, actor: str = "bootstrap") -> SpecRevision:
        body = copy.deepcopy(manifest)
        ref = ManifestRef.from_content(body)
        rev = SpecRevision(
            revision=0,
            content_hash=ref.content_hash,
            parent_revision=None,
            actor=actor,
            reason="initial materialization",
            delta=SpecDelta("materialize", {}),
            taken_at=datetime.now(UTC).isoformat(),
        )
        self._claims = {key: tok for key, tok in self._claims.items() if key[0] != session_id}
        self._revs[session_id] = [rev]
        self._manifests[session_id] = body
        self._heads[session_id] = 0
        return rev

    def current_manifest(self, session_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._manifests[session_id])

    def current_spec(self, session_id: str) -> dict[str, Any]:
        manifest = self._manifests.get(session_id) or {}
        spec = manifest.get("spec")
        return copy.deepcopy(spec) if isinstance(spec, dict) else {}

    def layer_state(self, session_id: str, layer: str) -> dict[str, Any]:
        """What *layer* has recorded in the running spec, per target. A copy of that part only."""
        spec = (self._manifests.get(session_id) or {}).get("spec")
        states = spec.get(LAYER_STATE_KEY) if isinstance(spec, dict) else None
        mine = states.get(layer) if isinstance(states, dict) else None
        return copy.deepcopy(mine) if isinstance(mine, dict) else {}

    def latest(self, session_id: str) -> SpecRevision | None:
        revs = self._revs.get(session_id) or []
        if not revs:
            return None
        head = self._heads.get(session_id)
        if head is None:
            return revs[-1]
        for rev in reversed(revs):
            if rev.revision == head:
                return rev
        return revs[-1]

    def apply(
        self,
        session_id: str,
        delta: SpecDelta,
        *,
        actor: str,
        reason: str,
        decide: Callable[[dict[str, Any]], None] | None = None,
    ) -> SpecRevision:
        """Append *delta* to the log, decided against the spec it will be applied to.

        *decide* sees a copy of the spec at the position the delta will take and may refuse it by raising. The
        position is then claimed with one atomic ``setdefault``: when another writer got there first the
        delta is decided again against the spec that writer produced. Decision and write are so one step,
        with nothing locked.
        """
        for _ in range(_CLAIM_ATTEMPTS):
            # the position is read first and the manifest after it; a publisher writes the manifest first and
            # the position last, so a manifest is never older than the position it is read with
            position = len(self._revs.get(session_id) or ())
            parent = self.latest(session_id)
            if parent is None:
                raise KeyError(f"session {session_id!r} has no materialized spec")
            manifest = copy.deepcopy(self._manifests[session_id])
            spec = manifest.setdefault("spec", {})
            if not isinstance(spec, dict):
                raise ValueError("manifest spec must be an object")
            if decide is not None:
                decide(copy.deepcopy(spec))
            _apply_delta(spec, delta)
            ref = ManifestRef.from_content(manifest)
            rev = SpecRevision(
                revision=parent.revision + 1,
                content_hash=ref.content_hash,
                parent_revision=parent.revision,
                actor=actor,
                reason=reason,
                delta=delta,
                taken_at=datetime.now(UTC).isoformat(),
            )
            token = object()
            if self._claims.setdefault((session_id, position), token) is not token:
                time.sleep(0)  # the writer that holds this position is publishing; let it finish
                continue
            self._manifests[session_id] = manifest
            self._heads[session_id] = rev.revision
            self._revs[session_id].append(rev)
            return rev
        raise RuntimeError(f"spec log for {session_id!r} kept changing; delta {delta.kind} not applied")

    def has(self, session_id: str) -> bool:
        return session_id in self._manifests

    @contextmanager
    def sealed(self, session_id: str) -> Iterator[tuple[dict[str, Any], int]]:
        """Hold the next position of the log so that nothing is appended; yields ``(manifest, revision)``.

        A writer that already claimed a position finishes first (the seal takes the position after it); a
        writer that comes later loses its claim to the seal and decides again, which is when the session's
        lifecycle refuses it. Whatever is read inside is the spec no write can change until the seal ends.
        """
        token = object()
        while True:
            position = len(self._revs.get(session_id) or ())
            if self._claims.setdefault((session_id, position), token) is token:
                break
            time.sleep(0)
        try:
            head = self.latest(session_id)
            yield self.current_manifest(session_id), (head.revision if head else 0)
        finally:
            self._claims.pop((session_id, position), None)

    def restore_manifest(self, session_id: str, manifest: dict[str, Any], revision: int) -> None:
        self._manifests[session_id] = copy.deepcopy(manifest)
        self._heads[session_id] = revision

    def clear_session(self, session_id: str) -> None:
        self._revs.pop(session_id, None)
        self._manifests.pop(session_id, None)
        self._heads.pop(session_id, None)
        self._claims = {key: tok for key, tok in self._claims.items() if key[0] != session_id}


def _apply_delta(spec: dict[str, Any], delta: SpecDelta) -> None:
    if delta.kind in {"disable_tool", "enable_tool"}:
        name = str(delta.payload.get("name") or "")
        if not name:
            raise ValueError(f"{delta.kind} requires payload.name")
        by = str(delta.payload.get("by") or "")  # who disabled it; the machine decides who may undo that
        for tool in spec.get("tools") or []:
            if not isinstance(tool, dict) or str(tool.get("name") or tool.get("id") or "") != name:
                continue
            if delta.kind == "disable_tool":
                tool["enabled"] = False
                if by:
                    tool["disabled_by"] = by
                return
            tool.pop("enabled", None)
            tool.pop("disabled_by", None)
            return
        raise ValueError(f"tool {name!r} is not in the current spec")
    if delta.kind == "set_pattern":
        spec["design_pattern"] = delta.payload.get("pattern")
        return
    if delta.kind == "overlay_factor":
        spec.setdefault("factors", {})
        if not isinstance(spec["factors"], dict):
            raise ValueError("spec.factors must be an object")
        spec["factors"][str(delta.payload.get("id") or "factor")] = delta.payload.get("value")
        return
    if delta.kind == "swap_layer_state":
        layer = str(delta.payload.get("layer") or "")
        target = str(delta.payload.get("target") or "")
        if not layer or not target:
            raise ValueError("swap_layer_state requires payload.layer and payload.target")
        states = spec.setdefault(LAYER_STATE_KEY, {})
        if not isinstance(states, dict):
            raise ValueError(f"spec.{LAYER_STATE_KEY} must be an object")
        mine = states.setdefault(layer, {})
        state = delta.payload.get("state")
        if state:
            mine[target] = copy.deepcopy(dict(state))
        else:
            mine.pop(target, None)
            if not mine:
                states.pop(layer, None)
            if not states:
                spec.pop(LAYER_STATE_KEY, None)
        return
    if delta.kind == "materialize":
        return
    raise ValueError(f"unknown spec delta {delta.kind!r}")

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-memory snapshot identity and session-scoped snapshot tree.

A Snapshot is a cheap in-memory node. A Checkpoint is that node written
to disk. Walking the tree moves a debug cursor; it does not change the
live run until someone promotes a branch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SnapshotRef:
    """Identity of one snapshot node.

    ``state_digest`` is the cheap CoW token (kernel version ⊕ working-memory
    version ⊕ spec revision). ``snapshot_id`` also includes parent, seq,
    and the governance label so two identical states at two decisions stay
    distinct tree nodes — content equality is ``state_digest``, not the id.
    """

    snapshot_id: str
    session_id: str
    parent_snapshot_id: str | None
    turn: int
    taken_at: str
    spec_revision: int | None = None
    label: str = ""
    kind: str = "explicit"
    hook: str = ""
    decision: str = ""
    op: str = ""
    correlation_id: int = 0
    state_digest: str = ""
    seq: int = 0

    @classmethod
    def from_state(
        cls,
        *,
        session_id: str,
        parent_snapshot_id: str | None,
        turn: int,
        kernel: dict[str, Any] | Any = None,
        working_memory: Any = None,
        spec_revision: int | None = None,
        label: str = "",
        kind: str = "explicit",
        hook: str = "",
        decision: str = "",
        op: str = "",
        correlation_id: int = 0,
        kernel_version: int = 0,
        wm_version: int = 0,
        seq: int = 0,
        related_fingerprints: list[str] | tuple[str, ...] | None = None,
        system_plugins: Any = None,
    ) -> SnapshotRef:
        taken_at = datetime.now(UTC).isoformat()
        from mas.runtime.session.cow import CowKernel

        if isinstance(kernel, CowKernel):
            kernel_version = kernel.version
        digest_fields: dict[str, Any] = {
            "kernel_version": kernel_version,
            "wm_version": wm_version,
            "spec_revision": spec_revision,
            "turn": turn,
            "related": list(related_fingerprints or ()),
        }
        if system_plugins:  # absent without plugins, so existing snapshot ids do not change
            digest_fields["system_plugins"] = _canonical_hash(dict(system_plugins))
        state_digest = _canonical_hash(digest_fields)
        snapshot_id = _canonical_hash(
            {
                "session_id": session_id,
                "parent_snapshot_id": parent_snapshot_id,
                "state_digest": state_digest,
                "seq": seq,
                "kind": kind,
                "hook": hook,
                "decision": decision,
                "op": op,
                "correlation_id": correlation_id,
                "label": label,
            }
        )
        return cls(
            snapshot_id=snapshot_id,
            session_id=session_id,
            parent_snapshot_id=parent_snapshot_id,
            turn=turn,
            taken_at=taken_at,
            spec_revision=spec_revision,
            label=label,
            kind=kind,
            hook=hook,
            decision=decision,
            op=op,
            correlation_id=correlation_id,
            state_digest=state_digest,
            seq=seq,
        )


@dataclass
class Snapshot:
    """In-memory capture. Never expected to cross a process boundary."""

    ref: SnapshotRef
    kernel: Any
    working_memory: list[Any]
    spec: dict[str, Any] | None = None
    spec_revision: int | None = None
    related: list[Any] = field(default_factory=list)
    system_plugins: dict[str, Any] = field(default_factory=dict)

    def kernel_dict(self) -> dict[str, Any]:
        from mas.runtime.session.cow import CowKernel

        if isinstance(self.kernel, CowKernel):
            return self.kernel.materialize()
        if isinstance(self.kernel, dict):
            return self.kernel
        return dict(self.kernel or {})


class SnapshotTree:
    """Per-session tree of snapshot refs. Live trajectory ≠ debug cursor."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, SnapshotRef]] = {}
        self._live: dict[str, str] = {}
        self._cursor: dict[str, str] = {}
        self._bodies: dict[str, Snapshot] = {}
        self._seq: dict[str, int] = {}

    def next_seq(self, session_id: str) -> int:
        self._seq[session_id] = self._seq.get(session_id, 0) + 1
        return self._seq[session_id]

    def record(self, ref: SnapshotRef, *, body: Snapshot | None = None, live: bool = True) -> None:
        bucket = self._nodes.setdefault(ref.session_id, {})
        bucket[ref.snapshot_id] = ref
        if body is not None:
            self._bodies[ref.snapshot_id] = body
        if live:
            self._live[ref.session_id] = ref.snapshot_id
            if ref.session_id not in self._cursor:
                self._cursor[ref.session_id] = ref.snapshot_id

    def get(self, snapshot_id: str) -> SnapshotRef | None:
        for bucket in self._nodes.values():
            if snapshot_id in bucket:
                return bucket[snapshot_id]
        return None

    def body(self, snapshot_id: str) -> Snapshot | None:
        return self._bodies.get(snapshot_id)

    def children(self, snapshot_id: str) -> list[SnapshotRef]:
        parent = self.get(snapshot_id)
        if parent is None:
            return []
        bucket = self._nodes.get(parent.session_id, {})
        return [ref for ref in bucket.values() if ref.parent_snapshot_id == snapshot_id]

    def path_to_root(self, snapshot_id: str) -> list[SnapshotRef]:
        path: list[SnapshotRef] = []
        current = self.get(snapshot_id)
        seen: set[str] = set()
        while current is not None and current.snapshot_id not in seen:
            path.append(current)
            seen.add(current.snapshot_id)
            current = self.get(current.parent_snapshot_id) if current.parent_snapshot_id else None
        path.reverse()
        return path

    def all_branches(self, session_id: str) -> list[list[SnapshotRef]]:
        bucket = self._nodes.get(session_id, {})
        leaves = [
            ref
            for ref in bucket.values()
            if not any(child.parent_snapshot_id == ref.snapshot_id for child in bucket.values())
        ]
        if not leaves and bucket:
            leaves = list(bucket.values())
        return [self.path_to_root(leaf.snapshot_id) for leaf in leaves]

    def list_nodes(self, session_id: str) -> list[SnapshotRef]:
        bucket = self._nodes.get(session_id, {})
        return sorted(bucket.values(), key=lambda r: (r.turn, r.taken_at, r.snapshot_id))

    def live(self, session_id: str) -> SnapshotRef | None:
        sid = self._live.get(session_id)
        return self.get(sid) if sid else None

    def cursor(self, session_id: str) -> SnapshotRef | None:
        sid = self._cursor.get(session_id)
        return self.get(sid) if sid else None

    def set_cursor(self, session_id: str, snapshot_id: str) -> SnapshotRef:
        ref = self._nodes.get(session_id, {}).get(snapshot_id) or self.get(snapshot_id)
        if ref is None:
            raise KeyError(f"unknown snapshot {snapshot_id!r}")
        self._cursor[session_id] = ref.snapshot_id
        return ref

    def set_live(self, session_id: str, snapshot_id: str) -> SnapshotRef:
        ref = self.set_cursor(session_id, snapshot_id)
        self._live[session_id] = ref.snapshot_id
        return ref

    def clear_session(self, session_id: str) -> None:
        bodies = self._nodes.pop(session_id, {})
        self._live.pop(session_id, None)
        self._cursor.pop(session_id, None)
        self._seq.pop(session_id, None)
        for snapshot_id in bodies:
            self._bodies.pop(snapshot_id, None)

    @classmethod
    def from_events(cls, events: list[Any], *, session_id: str | None = None) -> SnapshotTree:
        """Rebuild refs from τ / control events. Bodies are not recovered."""
        tree = cls()
        for event in events:
            payload = _event_payload(event)
            kind = _event_kind(event)
            if kind not in {"snapshot_recorded", "checkpoint_navigated", "branch_opened"}:
                continue
            sid = str(payload.get("session_id") or getattr(event, "session_id", "") or session_id or "")
            if not sid:
                continue
            if kind == "checkpoint_navigated" and payload.get("denied"):
                continue
            snapshot_id = str(payload.get("snapshot_id") or payload.get("to") or payload.get("child") or "")
            if not snapshot_id:
                continue
            ref = SnapshotRef(
                snapshot_id=snapshot_id,
                session_id=sid,
                parent_snapshot_id=payload.get("parent_snapshot_id") or payload.get("from"),
                turn=int(payload.get("turn") or 0),
                taken_at=str(payload.get("taken_at") or getattr(event, "taken_at", "") or ""),
                spec_revision=payload.get("spec_revision"),
                label=str(payload.get("label") or ""),
                kind=str(payload.get("kind") or "explicit"),
                hook=str(payload.get("hook") or ""),
                decision=str(payload.get("decision") or ""),
                op=str(payload.get("op") or ""),
                correlation_id=int(payload.get("correlation_id") or 0),
                state_digest=str(payload.get("state_digest") or ""),
                seq=int(payload.get("seq") or 0),
            )
            live = bool(payload.get("live", True)) and kind != "checkpoint_navigated"
            tree.record(ref, live=live)
            if kind == "checkpoint_navigated" and not payload.get("denied"):
                try:
                    tree.set_cursor(sid, snapshot_id)
                except KeyError:
                    pass
        return tree


def _event_kind(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("kind") or "")
    return str(getattr(event, "kind", "") or "")


def _event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        payload = dict(event.get("payload") or {})
        if "session_id" not in payload and event.get("session_id"):
            payload["session_id"] = event["session_id"]
        return payload
    payload = dict(getattr(event, "payload", None) or {})
    if "session_id" not in payload:
        payload["session_id"] = getattr(event, "session_id", "")
    return payload


def persist(
    snapshot: Snapshot, store: Any, *, manifest: dict[str, Any], lineage: dict[str, Any], backtrack_count: int = 0
) -> Any:
    """Write a Snapshot through CheckpointStore. Disk is optional."""
    from mas.runtime.session.state import ManifestRef

    ref = ManifestRef.from_content(manifest)
    working_memory = []
    for entry in snapshot.working_memory or []:
        item = dict(entry)
        history = []
        for turn in item.get("turn_history") or []:
            if isinstance(turn, (list, tuple)) and len(turn) >= 2:
                history.append([str(turn[0]), str(turn[1])])
            else:
                history.append(turn)
        item["turn_history"] = history
        working_memory.append(item)
    payload = {
        "version": 2,
        "label": snapshot.ref.label,
        "turn": snapshot.ref.turn,
        "backtrack_count": backtrack_count,
        "spec_revision": snapshot.spec_revision,
        "lineage": lineage,
        "kernel": snapshot.kernel_dict(),
        "working_memory": working_memory,
        "related": [r.as_payload() if hasattr(r, "as_payload") else r for r in (snapshot.related or [])],
        "manifest": {"content": ref.content, "content_hash": ref.content_hash},
    }
    if snapshot.system_plugins:
        payload["system_plugins"] = dict(snapshot.system_plugins)
    label = snapshot.ref.label or f"turn-{snapshot.ref.turn:04d}"
    return store.save(payload, label=f"{snapshot.ref.session_id}-{label}")

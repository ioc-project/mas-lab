#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session aggregate state above the kernel snapshot."""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    sync_working_memory_out,
)
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.spec.checkpoint import CheckpointPolicy


class CheckpointStore(Protocol):
    """Persistence boundary used by a session to write a complete snapshot."""

    def save(self, snapshot: dict[str, Any], *, label: str = "") -> Path: ...

    def load_payload(self, path: Path) -> dict[str, Any]: ...

    def retain(self, session_id: str, mode: str, n: int) -> None: ...


@dataclass
class SessionLineage:
    """Identity and ancestry for one independently addressable session."""

    session_id: str
    parent_session_id: str | None = None
    forked_from_checkpoint: str | None = None
    root_session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.root_session_id is None:
            self.root_session_id = self.parent_session_id or self.session_id


class SessionStatus(str, Enum):
    """Lifecycle state for a session independently of kernel automaton state."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    CHECKPOINTING = "checkpointing"
    TERMINATED = "terminated"


class BacktrackCapReached(RuntimeError):
    """Automatic rollback budget is exhausted; caller applies the cap policy."""

    def __init__(self, action: str) -> None:
        self.action = action
        super().__init__(f"automatic backtrack limit reached ({action})")


LOCATION_KEYS = frozenset({"manifest_dir"})


def without_location_keys(value: Any) -> Any:
    """``value`` without the keys that say where a file lives. A hash taken over it does not depend on the checkout."""
    if isinstance(value, dict):
        return {key: without_location_keys(item) for key, item in value.items() if key not in LOCATION_KEYS}
    if isinstance(value, list):
        return [without_location_keys(item) for item in value]
    return value


@dataclass(frozen=True)
class ManifestRef:
    """Self-contained manifest content and its canonical SHA-256 digest."""

    content: dict[str, Any]
    content_hash: str

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ManifestRef:
        """Build an integrity reference without embedding external secrets.

        The hash ignores where a plugin's files live (:func:`without_location_keys`), so it holds across checkouts.
        """
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        located = json.dumps(without_location_keys(content), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(located.encode("utf-8")).hexdigest()
        return cls(content=json.loads(encoded), content_hash=digest)


@dataclass
class Session:
    """Own runtime, controller, working memory, and checkpoint lineage."""

    session_id: str
    instance: RuntimeInstance
    controller: Any
    working_memory: WorkingMemoryRegistry
    lineage: SessionLineage
    status: SessionStatus = SessionStatus.CREATED
    manifest_ref: ManifestRef | None = None
    checkpoint_history: list[str] = field(default_factory=list)
    checkpoint_policy: CheckpointPolicy = field(default_factory=CheckpointPolicy)
    backtrack_policy: dict[str, Any] = field(default_factory=dict)
    backtrack_count: int = 0
    pause_reason: str = ""
    spec_revision: int = 0
    snapshot_tree: Any | None = None
    spec_log: Any | None = None
    snapshot_policy: str = "governance"
    related_state: list[Any] = field(default_factory=list)
    execute_sandbox: Any | None = None
    lineage_events: list[dict[str, Any]] = field(default_factory=list)
    _capturing_snapshot: bool = field(default=False, repr=False, compare=False)

    def _system_plugins(self) -> Any:
        engine = getattr(getattr(self.instance, "driver", None), "engine", None)
        if engine is None:
            return None
        from mas.runtime.engine.leaf import leaf_engine

        leaf = leaf_engine(engine)
        provider = getattr(leaf, "tool_provider", None)
        getter = getattr(provider, "system_plugins", None)
        return getter() if callable(getter) else None

    def _system_plugin_snapshot(self) -> dict[str, Any]:
        plugins = self._system_plugins()
        return dict(plugins.snapshot()) if plugins is not None else {}

    def _restore_system_plugins(self, snapshot: Any) -> None:
        if not isinstance(snapshot, dict):
            return
        bound = self._system_plugins()
        if bound is not None:
            bound.restore(snapshot)

    def pause(self, *, reason: str = "") -> None:
        """Mark the session paused. User turns must refuse until ``resume``."""
        self.status = SessionStatus.PAUSED
        self.pause_reason = reason
        pause = getattr(self.instance, "pause", None)
        if callable(pause):
            try:
                pause(reason=reason)
            except Exception:
                pass

    def resume(self) -> None:
        if self.status is SessionStatus.TERMINATED:
            raise RuntimeError("cannot resume a terminated session")
        self.status = SessionStatus.ACTIVE
        self.pause_reason = ""
        resume = getattr(self.instance, "resume", None)
        if callable(resume):
            try:
                resume()
            except Exception:
                pass

    def checkpoint(self, store: CheckpointStore, *, label: str = "") -> Path:
        """Persist a self-contained snapshot of kernel and conversation state."""
        if self.manifest_ref is None:
            raise ValueError("self-contained checkpoint requires manifest content")

        sync_working_memory_out(
            self.instance,
            memory_key=self.session_id,
            agent_id=str(getattr(self.controller, "agent_id", "agent")),
            registry=self.working_memory,
        )

        previous_status = self.status
        self.status = SessionStatus.CHECKPOINTING
        seal = ExitStack()
        try:
            return self._write_checkpoint(store, label, previous_status, seal)
        finally:
            seal.close()
            if self.status is SessionStatus.CHECKPOINTING:  # building the payload failed
                self.status = (
                    previous_status
                    if previous_status in {SessionStatus.PAUSED, SessionStatus.TERMINATED}
                    else SessionStatus.ACTIVE
                )

    def _write_checkpoint(
        self, store: CheckpointStore, label: str, previous_status: SessionStatus, seal: ExitStack
    ) -> Path:
        # the spec is read with the log sealed: no write lands while this checkpoint is being taken
        self._pin_spec(seal)
        payload: dict[str, Any] = {
            "version": 2,
            "label": label,
            "turn": int(getattr(self.controller, "_turn", 0)),
            "backtrack_count": self.backtrack_count,
            "lineage": {
                "session_id": self.lineage.session_id,
                "parent_session_id": self.lineage.parent_session_id,
                "forked_from_checkpoint": self.lineage.forked_from_checkpoint,
                "root_session_id": self.lineage.root_session_id,
                "created_at": self.lineage.created_at,
            },
            "kernel": self.instance.snapshot(),
            "working_memory": self.working_memory.export_session(self.session_id),
            "manifest": {
                "content": self.manifest_ref.content,
                "content_hash": self.manifest_ref.content_hash,
            },
        }
        plugins = self._system_plugin_snapshot()
        if plugins:
            payload["system_plugins"] = plugins
        try:
            turn_label = label or f"turn-{payload['turn']:04d}"
            store_label = f"{self.session_id}-{turn_label}"
            path = store.save(payload, label=store_label)
        finally:
            seal.close()
            if previous_status in {SessionStatus.PAUSED, SessionStatus.TERMINATED}:
                self.status = previous_status
            else:
                self.status = SessionStatus.ACTIVE
        self.checkpoint_history.append(str(path))
        retain = getattr(store, "retain", None)
        if callable(retain):
            retain(
                self.session_id,
                self.checkpoint_policy.retention_mode,
                self.checkpoint_policy.retention_n,
            )
        if self.checkpoint_policy.retention_mode == "single":
            self.checkpoint_history = self.checkpoint_history[-1:]
        elif self.checkpoint_policy.retention_mode == "last_n":
            self.checkpoint_history = self.checkpoint_history[-self.checkpoint_policy.retention_n :]
        self._record_snapshot(payload, label=label)
        return path

    def _pin_spec(self, seal: ExitStack | None = None) -> None:
        """Bring ``manifest_ref`` and ``spec_revision`` to what the spec log holds, read as one.

        With *seal* the log stays sealed until the stack closes. Without it the pair is read once, which is
        the point in the log the snapshot is taken at.
        """
        log = self.spec_log
        if log is None or not log.has(self.session_id):
            return
        if seal is not None:
            manifest, revision = seal.enter_context(log.sealed(self.session_id))
        else:
            with log.sealed(self.session_id) as (manifest, revision):
                pass
        self.manifest_ref = ManifestRef.from_content(manifest)
        self.spec_revision = revision

    def _record_snapshot(self, payload: dict[str, Any], *, label: str) -> None:
        tree = self.snapshot_tree
        if tree is None:
            return
        from mas.runtime.session.snapshot import Snapshot, SnapshotRef

        parent = tree.live(self.session_id)
        ref = SnapshotRef.from_state(
            session_id=self.session_id,
            parent_snapshot_id=parent.snapshot_id if parent else None,
            turn=int(payload.get("turn", 0)),
            kernel=payload.get("kernel") or {},
            working_memory=payload.get("working_memory") or [],
            spec_revision=self.spec_revision,
            label=label,
        )
        tree.record(
            ref,
            body=Snapshot(
                ref=ref,
                kernel=payload.get("kernel") or {},
                working_memory=list(payload.get("working_memory") or []),
                spec=self.manifest_ref.content if self.manifest_ref else None,
                spec_revision=self.spec_revision,
                related=list(payload.get("related") or []),
                system_plugins=dict(payload.get("system_plugins") or {}),
            ),
            live=True,
        )

    def backtrack(
        self,
        store: CheckpointStore,
        *,
        steps: int = 1,
        steering_text: str = "",
        automatic: bool = False,
    ) -> Path:
        """Restore a retained checkpoint and optionally steer the next turn."""
        if automatic:
            max_backtracks = self.backtrack_policy.get("max_backtracks_per_session", 3)
            if self.backtrack_count >= max_backtracks:
                raise BacktrackCapReached(self.backtrack_policy.get("on_cap_reached", "hitl"))
        if steps < 1:
            raise ValueError("backtrack steps must be at least 1")
        if steps > len(self.checkpoint_history):
            raise ValueError("backtrack exceeds retained checkpoint history")

        target_index = len(self.checkpoint_history) - steps
        path = Path(self.checkpoint_history[target_index])
        payload = store.load_payload(path)
        self.instance.load_checkpoint(payload["kernel"])
        self.working_memory.restore_session(
            self.session_id,
            payload.get("working_memory") or [],
        )
        self._restore_system_plugins(payload.get("system_plugins"))
        self.checkpoint_history = self.checkpoint_history[: target_index + 1]
        self.controller.restore_turn(int(payload.get("turn", 0)))
        if automatic:
            self.backtrack_count += 1
        if self.status not in {SessionStatus.PAUSED, SessionStatus.TERMINATED}:
            self.status = SessionStatus.ACTIVE
        if steering_text.strip():
            self.controller.run_turn(f"/steer {steering_text.strip()}", auto_hitl=False)
        if "spec_revision" in payload:
            self.spec_revision = int(payload["spec_revision"] or 0)
        tree = self.snapshot_tree
        if tree is not None:
            restored_turn = int(payload.get("turn", 0))
            match = None
            for node in reversed(tree.list_nodes(self.session_id)):
                if node.turn == restored_turn:
                    match = node
                    break
            if match is not None:
                tree.set_live(self.session_id, match.snapshot_id)
            self.take_snapshot(label="after-backtrack", kind="backtrack")
        return path

    def take_snapshot(
        self,
        *,
        label: str = "",
        live: bool = True,
        kind: str = "explicit",
        hook: str = "",
        decision: str = "",
        op: str = "",
        correlation_id: int = 0,
    ) -> Any:
        """In-memory CoW node. Does not touch disk.

        Freezes live kernel + working memory (O(1) sharing). The next
        ``transition`` / ``sync_working_memory_out`` copies only if it writes.
        """
        from mas.runtime.session.cow import capture_kernel
        from mas.runtime.session.snapshot import Snapshot, SnapshotRef, SnapshotTree

        self._pin_spec()
        sync_working_memory_out(
            self.instance,
            memory_key=self.session_id,
            agent_id=str(getattr(self.controller, "agent_id", "agent")),
            registry=self.working_memory,
        )
        kernel_obj = getattr(self.instance, "kernel", None)
        if kernel_obj is not None and hasattr(kernel_obj, "q"):
            cow = capture_kernel(kernel_obj)
            kernel_payload: Any = cow
            kernel_version = cow.version
        else:
            kernel_payload = self.instance.snapshot()
            kernel_version = 0
        wm_version = 0
        freeze_session = getattr(self.working_memory, "freeze_session", None)
        if callable(freeze_session):
            wm_version = freeze_session(self.session_id)
        working_memory = self.working_memory.export_session(self.session_id)
        system_plugins = self._system_plugin_snapshot()
        tree = self.snapshot_tree or SnapshotTree()
        parent = tree.live(self.session_id)
        seq = tree.next_seq(self.session_id) if hasattr(tree, "next_seq") else 0
        from mas.runtime.boundary.related_state import capture_all

        related = capture_all(self.related_state, self.session_id)
        ref = SnapshotRef.from_state(
            session_id=self.session_id,
            parent_snapshot_id=parent.snapshot_id if parent else None,
            turn=int(getattr(self.controller, "_turn", 0)),
            kernel=kernel_payload,
            working_memory=working_memory,
            spec_revision=self.spec_revision,
            label=label,
            kind=kind,
            hook=hook,
            decision=decision,
            op=op,
            correlation_id=correlation_id,
            kernel_version=kernel_version,
            wm_version=wm_version,
            seq=seq,
            related_fingerprints=[r.fingerprint for r in related],
            system_plugins=system_plugins,
        )
        snap = Snapshot(
            ref=ref,
            kernel=kernel_payload,
            working_memory=list(working_memory),
            spec=self.manifest_ref.content if self.manifest_ref else None,
            spec_revision=self.spec_revision,
            related=related,
            system_plugins=system_plugins,
        )
        self.lineage_events.append(
            {
                "kind": "snapshot_recorded",
                "session_id": self.session_id,
                "payload": {
                    "snapshot_id": ref.snapshot_id,
                    "parent_snapshot_id": ref.parent_snapshot_id,
                    "turn": ref.turn,
                    "label": ref.label,
                    "kind": ref.kind,
                    "hook": ref.hook,
                    "decision": ref.decision,
                    "op": ref.op,
                    "correlation_id": ref.correlation_id,
                    "spec_revision": ref.spec_revision,
                    "state_digest": ref.state_digest,
                    "seq": ref.seq,
                    "live": live,
                    "related": [r.as_payload() for r in related],
                },
            }
        )
        if self.snapshot_tree is not None:
            self.snapshot_tree.record(ref, body=snap, live=live)
        return snap

    def on_governance_decision(
        self,
        *,
        hook: str,
        decision: str,
        correlation_id: int = 0,
        op: str = "",
        tool_name: str = "",
    ) -> Any | None:
        """Automatic snapshot at the envelope authorize/validate chokepoint."""
        if self.snapshot_policy != "governance" or self._capturing_snapshot:
            return None
        self._capturing_snapshot = True
        try:
            return self.take_snapshot(
                label=f"gov:{hook}:{decision}:{op or tool_name}",
                kind="governance",
                hook=hook,
                decision=decision,
                op=op,
                correlation_id=correlation_id,
            )
        finally:
            self._capturing_snapshot = False

    def restore_snapshot(self, snapshot: Any) -> None:
        """Restore kernel, working memory, turn, and spec revision from a node."""
        from mas.runtime.session.cow import CowKernel, restore_kernel

        kernel_obj = getattr(self.instance, "kernel", None)
        if isinstance(getattr(snapshot, "kernel", None), CowKernel) and kernel_obj is not None:
            restore_kernel(kernel_obj, snapshot.kernel)
        else:
            payload = snapshot.kernel_dict() if hasattr(snapshot, "kernel_dict") else snapshot.kernel
            self.instance.load_checkpoint(payload)
        self.working_memory.restore_session(self.session_id, snapshot.working_memory)
        restore_turn = getattr(self.controller, "restore_turn", None)
        if callable(restore_turn):
            restore_turn(int(snapshot.ref.turn))
        if snapshot.spec_revision is not None:
            self.spec_revision = int(snapshot.spec_revision)
        if snapshot.spec is not None:
            self.manifest_ref = ManifestRef.from_content(snapshot.spec)
            if self.spec_log is not None:
                self.spec_log.restore_manifest(
                    self.session_id,
                    snapshot.spec,
                    int(snapshot.spec_revision or 0),
                )
        if self.snapshot_tree is not None:
            self.snapshot_tree.set_live(self.session_id, snapshot.ref.snapshot_id)
        from mas.runtime.boundary.related_state import restore_all

        restore_all(self.related_state, self.session_id, getattr(snapshot, "related", None))
        self._restore_system_plugins(getattr(snapshot, "system_plugins", None))

    def persist_snapshot(self, snapshot: Any, store: CheckpointStore) -> Path:
        from mas.runtime.session.snapshot import persist

        if self.manifest_ref is None:
            raise ValueError("self-contained checkpoint requires manifest content")
        path = persist(
            snapshot,
            store,
            manifest=self.manifest_ref.content,
            lineage={
                "session_id": self.lineage.session_id,
                "parent_session_id": self.lineage.parent_session_id,
                "forked_from_checkpoint": self.lineage.forked_from_checkpoint,
                "root_session_id": self.lineage.root_session_id,
                "created_at": self.lineage.created_at,
            },
            backtrack_count=self.backtrack_count,
        )
        self.checkpoint_history.append(str(path))
        return path

    def branch(self, *, steering: str | None = None):
        """Sibling timeline. Discarded on exit unless ``promote`` or ``persist``."""
        from contextlib import contextmanager

        @contextmanager
        def _branch():
            origin = self.take_snapshot(label="branch-origin")
            handle = _BranchHandle(self, origin)
            if steering:
                self.controller.run_turn(f"/steer {steering}", auto_hitl=False)
            try:
                yield handle
            finally:
                if not handle.kept:
                    self.restore_snapshot(origin)

        return _branch()


class _BranchHandle:
    def __init__(self, session: Session, origin: Any) -> None:
        self.session = session
        self.origin = origin
        self.kept = False

    def inspect(self) -> Any:
        return self.session.take_snapshot(label="inspect", live=False)

    def persist(self, store: CheckpointStore) -> Path:
        snap = self.session.take_snapshot(label="branch")
        path = self.session.persist_snapshot(snap, store)
        self.kept = True
        return path

    def promote(self) -> Any:
        snap = self.session.take_snapshot(label="promoted")
        self.kept = True
        return snap

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Control-plane lifecycle for independently owned runtime sessions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.ctl.adapters.checkpoint import JsonCheckpointStore
from mas.ctl.session.turn_queue import TurnInputQueue
from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
from mas.runtime.boundary.control.contract import ControlCapability, ControlEvent
from mas.runtime.session import ManifestRef, Session, SessionLineage, SessionStatus
from mas.runtime.session.snapshot import SnapshotTree
from mas.runtime.session.spec_revision import SpecRevisionLog
from mas.runtime.spec.checkpoint import parse_checkpoint_policy
from mas.runtime.spec.gov import parse_gov_spec

SessionFactory = Callable[[dict[str, Any], str], tuple[Any, Any]]


@dataclass
class SessionManager:
    """Create, resume, and fork sessions using a caller-owned runtime factory."""

    checkpoint_store: JsonCheckpointStore | None = None
    session_factory: SessionFactory | None = None
    sessions: dict[str, Session] = field(default_factory=dict)
    turn_queue: TurnInputQueue = field(default_factory=TurnInputQueue)
    snapshot_tree: SnapshotTree = field(default_factory=SnapshotTree)
    spec_log: SpecRevisionLog = field(default_factory=SpecRevisionLog)
    control_events: list[ControlEvent] = field(default_factory=list)

    def create(
        self,
        instance: Any,
        controller: Any,
        manifest_content: dict[str, Any],
        *,
        session_id: str | None = None,
        working_memory_registry: WorkingMemoryRegistry | None = None,
    ) -> Session:
        """Register a fresh session with its own working-memory registry."""
        resolved_id = session_id or str(getattr(controller, "session_id", "") or uuid.uuid4())
        lineage = SessionLineage(session_id=resolved_id)
        session = Session(
            session_id=resolved_id,
            instance=instance,
            controller=controller,
            working_memory=working_memory_registry or WorkingMemoryRegistry(),
            lineage=lineage,
            manifest_ref=ManifestRef.from_content(manifest_content),
            checkpoint_policy=parse_checkpoint_policy((manifest_content.get("spec") or {}).get("checkpoint")),
            backtrack_policy=dict(
                parse_gov_spec((manifest_content.get("spec") or {}).get("governance")).backtrack or {}
            ),
        )
        session.snapshot_tree = self.snapshot_tree
        session.spec_log = self.spec_log
        rev = self.spec_log.materialize(resolved_id, manifest_content)
        session.spec_revision = rev.revision
        self._attach_controller(session)
        self.sessions[resolved_id] = session
        return session

    def control(
        self,
        *,
        capability: ControlCapability | None = None,
        deny_navigate: Any | None = None,
    ) -> Any:
        from mas.ctl.session.control import SessionControl

        return SessionControl(self, capability=capability, deny_navigate=deny_navigate)

    def clear_session(self, session_id: str) -> None:
        from mas.runtime.engine.inflight_llm import clear as clear_inflight

        self.sessions.pop(session_id, None)
        self.turn_queue.clear_session(session_id)
        self.snapshot_tree.clear_session(session_id)
        self.spec_log.clear_session(session_id)
        clear_inflight(session_id)

    def get(self, session_id: str) -> Session:
        """Return a managed session or raise a descriptive lookup error."""
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session {session_id!r}") from exc

    def checkpoint(self, session_id: str, *, label: str = "") -> Path:
        """Persist a managed session through its configured checkpoint store."""
        if self.checkpoint_store is None:
            raise RuntimeError("session manager has no checkpoint store")
        return self.get(session_id).checkpoint(self.checkpoint_store, label=label)

    def restore_checkpoint(
        self,
        session: Session,
        path: Path,
        *,
        manifest_content: dict[str, Any],
    ) -> Session:
        """Restore a checkpoint into an already materialized session."""
        if self.checkpoint_store is None:
            raise RuntimeError("session manager has no checkpoint store")
        payload = self.checkpoint_store.load_payload(path)
        content = self._manifest_content(payload, manifest_content)
        if session.manifest_ref is not None:
            if session.manifest_ref.content_hash != ManifestRef.from_content(content).content_hash:
                raise ValueError("checkpoint manifest does not match the active manifest")
        session.instance.load_checkpoint(payload["kernel"])
        lineage_data = payload.get("lineage") or {}
        if payload.get("version") == 2:
            self.sessions.pop(session.session_id, None)
            session.session_id = str(lineage_data["session_id"])
            session.lineage = SessionLineage(
                session_id=session.session_id,
                parent_session_id=lineage_data.get("parent_session_id"),
                forked_from_checkpoint=lineage_data.get("forked_from_checkpoint"),
                root_session_id=lineage_data.get("root_session_id"),
                created_at=lineage_data["created_at"],
            )
        session.working_memory.restore_session(
            session.session_id,
            payload.get("working_memory") or [],
        )
        session.manifest_ref = ManifestRef.from_content(content)
        session.controller.restore_turn(int(payload.get("turn", 0)))
        session.checkpoint_history.append(str(path))
        self._attach_controller(session)
        self.sessions[session.session_id] = session
        return session

    def resume_from_checkpoint(self, path: Path, *, manifest_content: dict[str, Any] | None = None) -> Session:
        """Rebuild a session from embedded manifest content or a v1 manifest."""
        if self.checkpoint_store is None or self.session_factory is None:
            raise RuntimeError("resume requires a checkpoint store and session factory")
        payload = self.checkpoint_store.load_payload(path)
        content = self._manifest_content(payload, manifest_content)
        session_id = str((payload.get("lineage") or {}).get("session_id") or uuid.uuid4())
        lineage_data = payload.get("lineage") or {}
        lineage = SessionLineage(
            session_id=session_id,
            parent_session_id=lineage_data.get("parent_session_id"),
            forked_from_checkpoint=lineage_data.get("forked_from_checkpoint"),
            root_session_id=lineage_data.get("root_session_id") or session_id,
            created_at=lineage_data.get("created_at") or SessionLineage(session_id).created_at,
        )
        session = self._restore(payload, content, session_id, lineage)
        session.backtrack_count = int(payload.get("backtrack_count", 0))
        session.checkpoint_history.append(str(path))
        return session

    def fork_from_checkpoint(
        self,
        path: Path,
        *,
        new_session_id: str | None = None,
        manifest_content: dict[str, Any] | None = None,
    ) -> Session:
        """Create a new session from a checkpoint while preserving its ancestry."""
        if self.checkpoint_store is None or self.session_factory is None:
            raise RuntimeError("fork requires a checkpoint store and session factory")
        payload = self.checkpoint_store.load_payload(path)
        content = self._manifest_content(payload, manifest_content)
        source_lineage = payload.get("lineage") or {}
        parent_id = str(source_lineage.get("session_id") or "") or None
        session_id = new_session_id or str(uuid.uuid4())
        lineage = SessionLineage(
            session_id=session_id,
            parent_session_id=parent_id,
            forked_from_checkpoint=path.name,
            root_session_id=source_lineage.get("root_session_id") or parent_id or session_id,
        )
        session = self._restore(payload, content, session_id, lineage)
        session.backtrack_count = 0
        session.checkpoint_history.append(str(path))
        return session

    def _restore(
        self,
        payload: dict[str, Any],
        manifest_content: dict[str, Any],
        session_id: str,
        lineage: SessionLineage,
    ) -> Session:
        if self.session_factory is None:
            raise RuntimeError("session factory is not configured")
        instance, controller = self.session_factory(manifest_content, session_id)
        instance.load_checkpoint(payload["kernel"])
        registry = WorkingMemoryRegistry()
        registry.restore_session(session_id, payload.get("working_memory") or [])
        session = Session(
            session_id=session_id,
            instance=instance,
            controller=controller,
            working_memory=registry,
            lineage=lineage,
            status=SessionStatus.ACTIVE,
            manifest_ref=ManifestRef.from_content(manifest_content),
            checkpoint_policy=parse_checkpoint_policy((manifest_content.get("spec") or {}).get("checkpoint")),
            backtrack_policy=dict(
                parse_gov_spec((manifest_content.get("spec") or {}).get("governance")).backtrack or {}
            ),
        )
        controller.restore_turn(int(payload.get("turn", 0)))
        self._attach_controller(session)
        session.snapshot_tree = self.snapshot_tree
        session.spec_log = self.spec_log
        rev = self.spec_log.materialize(session_id, manifest_content)
        if "spec_revision" in payload:
            session.spec_revision = int(payload["spec_revision"] or 0)
            session.spec_log.restore_manifest(session_id, manifest_content, session.spec_revision)
        else:
            session.spec_revision = rev.revision
        self.sessions[session_id] = session
        from mas.runtime.boundary.related_state import restore_all

        restore_all(session.related_state, session_id, payload.get("related") or [])
        return session

    def _attach_controller(self, session: Session) -> None:
        session.controller.session_id = session.session_id
        session.controller.working_memory_registry = session.working_memory
        session.controller.managed_session = session
        session.controller.turn_queue = self.turn_queue
        ctx = getattr(getattr(session.instance, "driver", None), "ctx", None)
        if ctx is not None:
            ctx.session_id = session.session_id
            ctx.spec_log = self.spec_log
            ctx.managed_session = session
            ctx.current_spec = self.spec_log.current_spec(session.session_id)
            ctx.execute_sandbox = session.execute_sandbox
            from mas.runtime.engine.tools import is_control_tools_enabled

            ctx.allow_control_tools = is_control_tools_enabled(ctx.current_spec)
            from mas.ctl.session.control import SessionControl

            # A2A message/send is not an LLM tool. Steer (preempt / replace /
            # after) stays on this control surface; A2A has no steer RPC.
            # Call layers (circuit breaker) keep what they learn in the running spec, through this handle only.
            ctx.layer_control = SessionControl(
                self,
                capability=ControlCapability(
                    actor="layers",
                    surface="plugin",
                    session_ids=frozenset({session.session_id}),
                    methods=frozenset({"layer_state", "swap_layer_state", "disable_tool", "enable_tool"}),
                ),
            )
            ctx.control = SessionControl(
                self,
                capability=ControlCapability(
                    actor="llm",
                    surface="llm",
                    session_ids=frozenset({session.session_id}),
                    methods=frozenset(
                        {
                            "pause",
                            "resume",
                            "inspect",
                            "list_checkpoints",
                            "navigate",
                            "cancel_inflight",
                            "run_script",
                            "snapshot",
                            "persist",
                            "steer",
                        }
                    ),
                ),
            )
        if self.checkpoint_store is not None:
            session.controller.checkpoint_store = self.checkpoint_store
        kernel = getattr(session.instance, "kernel", None)
        config = getattr(kernel, "config", None)
        if config is not None:
            from dataclasses import replace

            kernel.config = replace(config, on_decision_snapshot=session.on_governance_decision)
        self._bind_debug_plugins(session)

    def _bind_debug_plugins(self, session: Session) -> None:
        kernel = getattr(session.instance, "kernel", None)
        config = getattr(kernel, "config", None)
        gov = getattr(config, "egress_governance_plugin", None)
        plugins = getattr(gov, "plugins", None)
        chain = list(plugins) if plugins else ([gov] if gov is not None else [])
        control = self.control(
            capability=ControlCapability(
                actor="debug_script",
                surface="plugin",
                session_ids=frozenset({session.session_id}),
                methods=frozenset(
                    {
                        "pause",
                        "resume",
                        "inspect",
                        "snapshot",
                        "list_checkpoints",
                    }
                ),
            )
        )
        for plugin in chain:
            bind = getattr(plugin, "bind_session", None)
            if callable(bind):
                bind(session, control=control, manager=self)

    @staticmethod
    def _manifest_content(
        payload: dict[str, Any],
        fallback: dict[str, Any] | None,
    ) -> dict[str, Any]:
        manifest = payload.get("manifest")
        if isinstance(manifest, dict):
            content = manifest.get("content")
            if not isinstance(content, dict):
                raise ValueError("checkpoint manifest content must be an object")
            if ManifestRef.from_content(content).content_hash != manifest.get("content_hash"):
                raise ValueError("checkpoint manifest hash mismatch")
            return content
        if fallback is None:
            raise ValueError("version 1 checkpoint resume requires manifest content")
        return fallback

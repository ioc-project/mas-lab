#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    WorkingMemorySnapshot,
)
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.session import ManifestRef, Session, SessionLineage, SessionStatus


class _Store:
    def __init__(self) -> None:
        self.payload = None

    def save(self, snapshot: dict, *, label: str = "") -> Path:
        self.payload = snapshot
        return Path("session.checkpoint.json")


def test_session_checkpoint_captures_kernel_memory_manifest_and_lineage() -> None:
    registry = WorkingMemoryRegistry()
    ctx = AutoCtxAssembler()
    ctx.note_user_input("hello")
    ctx.note_agent_response("world")
    ctx.working_memory.record_assistant_tool_call(
        call_id="tool-call-current",
        tool_name="lookup",
        arguments={"query": "unfinished turn"},
    )
    session = Session(
        session_id="session-1",
        instance=SimpleNamespace(
            snapshot=lambda: {"q": {}, "run": {}},
            driver=SimpleNamespace(ctx=ctx),
        ),
        controller=SimpleNamespace(_turn=3, agent_id="agent-1"),
        working_memory=registry,
        lineage=SessionLineage(session_id="session-1"),
        manifest_ref=ManifestRef.from_content({"name": "agent-1", "spec": {}}),
    )
    store = _Store()

    checkpoint_path = session.checkpoint(store, label="turn-3")

    assert checkpoint_path.name == "session.checkpoint.json"
    assert session.status is SessionStatus.ACTIVE
    assert session.checkpoint_history == [str(checkpoint_path)]
    assert store.payload["version"] == 2
    assert store.payload["turn"] == 3
    assert any(message["content"] == "hello" for message in store.payload["working_memory"][0]["committed_messages"])
    assert store.payload["working_memory"][0]["working_messages"][0]["tool_calls"][0]["id"] == "tool-call-current"
    assert store.payload["manifest"]["content"]["name"] == "agent-1"
    assert store.payload["lineage"]["root_session_id"] == "session-1"


def test_self_contained_checkpoint_requires_manifest_content() -> None:
    session = Session(
        session_id="session-1",
        instance=SimpleNamespace(snapshot=lambda: {"q": {}, "run": {}}),
        controller=SimpleNamespace(_turn=0),
        working_memory=WorkingMemoryRegistry(),
        lineage=SessionLineage(session_id="session-1"),
    )

    with pytest.raises(ValueError, match="requires manifest content"):
        session.checkpoint(_Store())


def test_session_checkpoint_captures_and_backtrack_restores_system_plugins() -> None:
    from mas.runtime.system_plugins import BoundSystemPlugins, SessionContext, SystemPluginBase, ToolDefinition
    from mas.runtime.system_plugins.load import _Bound

    class _Probe(SystemPluginBase):
        name = "probe"

        def tools(self, ctx=None):
            return (ToolDefinition(name="probe"),)

        def bind(self, session):
            return {"n": 1}

        def call(self, tool, args, *, state, context):
            return state

        def snapshot(self, state):
            return dict(state)

        def restore(self, snapshot):
            return dict(snapshot)

    plugin = _Probe()
    bound = BoundSystemPlugins()
    bound.add(
        _Bound(
            name="probe",
            urn="mas.system_plugin.probe",
            plugin=plugin,
            state=plugin.bind(SessionContext()),
            settings={},
            capability="probe",
        )
    )
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda snapshot: None,
        driver=SimpleNamespace(engine=SimpleNamespace(tool_provider=SimpleNamespace(system_plugins=lambda: bound))),
    )
    session = Session(
        session_id="session-1",
        instance=instance,
        controller=SimpleNamespace(_turn=1, agent_id="agent-1", restore_turn=lambda n: None),
        working_memory=WorkingMemoryRegistry(),
        lineage=SessionLineage(session_id="session-1"),
        manifest_ref=ManifestRef.from_content({"name": "agent-1", "spec": {}}),
    )
    store = _Store()
    session.checkpoint(store, label="turn-1")
    assert store.payload["system_plugins"] == {"mas.system_plugin.probe": {"n": 1}}
    bound._bound[0].state["n"] = 99
    session.checkpoint_history = ["latest.checkpoint.json"]

    class _HistoryStore:
        def load_payload(self, path: Path) -> dict:
            return {
                "turn": 1,
                "kernel": {"q": {}, "run": {}},
                "working_memory": [],
                "system_plugins": store.payload["system_plugins"],
            }

    session.backtrack(_HistoryStore())
    assert bound._bound[0].state == {"n": 1}


def test_session_take_snapshot_and_restore_restores_system_plugins() -> None:
    from mas.runtime.system_plugins import BoundSystemPlugins, SessionContext, SystemPluginBase, ToolDefinition
    from mas.runtime.system_plugins.load import _Bound

    class _Probe(SystemPluginBase):
        name = "probe"

        def tools(self, ctx=None):
            return (ToolDefinition(name="probe"),)

        def bind(self, session):
            return {"n": 1}

        def call(self, tool, args, *, state, context):
            return state

        def snapshot(self, state):
            return dict(state)

        def restore(self, snapshot):
            return dict(snapshot)

    plugin = _Probe()
    bound = BoundSystemPlugins()
    bound.add(
        _Bound(
            name="probe",
            urn="mas.system_plugin.probe",
            plugin=plugin,
            state=plugin.bind(SessionContext()),
            settings={},
            capability="probe",
        )
    )
    session = Session(
        session_id="session-1",
        instance=SimpleNamespace(
            snapshot=lambda: {"q": {}, "run": {}},
            load_checkpoint=lambda snapshot: None,
            driver=SimpleNamespace(engine=SimpleNamespace(tool_provider=SimpleNamespace(system_plugins=lambda: bound))),
        ),
        controller=SimpleNamespace(_turn=1, agent_id="agent-1", restore_turn=lambda n: None),
        working_memory=WorkingMemoryRegistry(),
        lineage=SessionLineage(session_id="session-1"),
        manifest_ref=ManifestRef.from_content({"name": "agent-1", "spec": {}}),
    )
    snap = session.take_snapshot(label="n0")
    assert snap.system_plugins == {"mas.system_plugin.probe": {"n": 1}}
    bound._bound[0].state["n"] = 99
    session.restore_snapshot(snap)
    assert bound._bound[0].state == {"n": 1}


def test_backtrack_restores_state_truncates_history_and_steers() -> None:
    restored = {}
    steering = []
    instance = SimpleNamespace(load_checkpoint=lambda snapshot: restored.update(snapshot))
    controller = SimpleNamespace(
        _turn=9,
        run_turn=lambda text, *, auto_hitl: steering.append((text, auto_hitl)),
    )
    controller.restore_turn = lambda n: setattr(controller, "_turn", int(n))
    registry = WorkingMemoryRegistry()
    registry.put("session-1", "agent-1", WorkingMemorySnapshot(turn_history=[("old", "state")]))
    session = Session(
        session_id="session-1",
        instance=instance,
        controller=controller,
        working_memory=registry,
        lineage=SessionLineage(session_id="session-1"),
        checkpoint_history=["older.checkpoint.json", "latest.checkpoint.json"],
    )

    class _HistoryStore:
        def load_payload(self, path: Path) -> dict:
            assert path == Path("latest.checkpoint.json")
            return {
                "turn": 4,
                "kernel": {"q": {"step": 4}, "run": {}},
                "working_memory": [
                    {
                        "agent_id": "agent-1",
                        "turn_history": [["question", "answer"]],
                        "committed_messages": [],
                        "conversation_chunks": None,
                    }
                ],
            }

    restored_path = session.backtrack(
        _HistoryStore(),
        steering_text="avoid the failed approach",
    )

    assert restored_path == Path("latest.checkpoint.json")
    assert restored == {"q": {"step": 4}, "run": {}}
    assert controller._turn == 4
    assert session.checkpoint_history == ["older.checkpoint.json", "latest.checkpoint.json"]
    assert registry.get("session-1", "agent-1").turn_history == [("question", "answer")]
    assert steering == [("/steer avoid the failed approach", False)]


def test_backtrack_rejects_steps_outside_retained_history() -> None:
    session = Session(
        session_id="session-1",
        instance=SimpleNamespace(),
        controller=SimpleNamespace(),
        working_memory=WorkingMemoryRegistry(),
        lineage=SessionLineage(session_id="session-1"),
        checkpoint_history=["only.checkpoint.json"],
    )

    with pytest.raises(ValueError, match="exceeds retained"):
        session.backtrack(_Store(), steps=2)


def test_snapshot_ref_digest_has_no_system_plugins_key_without_plugins() -> None:
    from mas.runtime.session.snapshot import SnapshotRef, _canonical_hash

    ref = SnapshotRef.from_state(session_id="s", parent_snapshot_id=None, kernel_version=1, turn=1)
    legacy = _canonical_hash({"kernel_version": 1, "wm_version": 0, "spec_revision": None, "turn": 1, "related": []})
    assert ref.state_digest == legacy  # snapshot ids from before system plugins stay valid
    with_state = SnapshotRef.from_state(
        session_id="s", parent_snapshot_id=None, kernel_version=1, turn=1, system_plugins={"u": {"n": 1}}
    )
    assert with_state.state_digest != legacy

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

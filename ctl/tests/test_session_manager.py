#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.ctl.adapters.checkpoint import JsonCheckpointStore
from mas.ctl.session.manager import SessionManager


def _factory(_manifest: dict, _session_id: str):
    instance = SimpleNamespace(
        loaded=None,
        load_checkpoint=lambda snapshot: setattr(instance, "loaded", snapshot),
    )

    class _Controller:
        def __init__(self) -> None:
            self.session_id = ""
            self.working_memory_registry = None
            self._turn = 0

        def restore_turn(self, n: int) -> None:
            self._turn = int(n)

    return instance, _Controller()


def test_fork_from_checkpoint_creates_independent_lineage_and_memory(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path)
    source = store.save(
        {
            "version": 2,
            "label": "source",
            "turn": 4,
            "lineage": {
                "session_id": "parent",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "parent",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {"step": 4}, "run": {}},
            "working_memory": [
                {
                    "agent_id": "agent",
                    "turn_history": [["question", "answer"]],
                    "committed_messages": [{"role": "user", "content": "question"}],
                    "conversation_chunks": None,
                }
            ],
            "manifest": {"content": {"name": "agent"}, "content_hash": "b" * 64},
        },
        label="source",
    )
    from mas.runtime.session import ManifestRef

    source_payload = store.load_payload(source)
    source_payload["manifest"] = {
        "content": {"name": "agent"},
        "content_hash": ManifestRef.from_content({"name": "agent"}).content_hash,
    }
    source.write_text(__import__("json").dumps(source_payload), encoding="utf-8")
    manager = SessionManager(store, _factory)

    forked = manager.fork_from_checkpoint(source, new_session_id="child")

    assert forked.lineage.parent_session_id == "parent"
    assert forked.lineage.root_session_id == "parent"
    assert forked.lineage.forked_from_checkpoint == Path(source).name
    assert forked.controller.session_id == "child"
    assert forked.controller._turn == 4
    assert forked.instance.loaded == {"q": {"step": 4}, "run": {}}
    snapshot = forked.working_memory.get("child", "agent")
    assert snapshot is not None
    assert snapshot.turn_history == [("question", "answer")]


def test_resume_rejects_manifest_hash_mismatch(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path)
    path = store.save(
        {
            "version": 2,
            "label": "broken",
            "turn": 0,
            "lineage": {
                "session_id": "session",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "session",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {}, "run": {}},
            "working_memory": [],
            "manifest": {"content": {"name": "agent"}, "content_hash": "0" * 64},
        },
        label="broken",
    )
    manager = SessionManager(store, _factory)

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        manager.resume_from_checkpoint(path)


def test_restore_checkpoint_reuses_stored_session_id_and_memory(tmp_path) -> None:
    from mas.runtime.session import ManifestRef

    content = {"name": "agent"}
    digest = ManifestRef.from_content(content).content_hash
    store = JsonCheckpointStore(tmp_path)
    path = store.save(
        {
            "version": 2,
            "label": "resume",
            "turn": 2,
            "lineage": {
                "session_id": "stored-session",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "stored-session",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {}, "run": {}},
            "working_memory": [
                {
                    "agent_id": "agent",
                    "turn_history": [["q", "a"]],
                    "committed_messages": [],
                    "conversation_chunks": None,
                }
            ],
            "manifest": {"content": content, "content_hash": digest},
        },
        label="resume",
    )
    manager = SessionManager(store, _factory)
    instance, controller = _factory(content, "temporary-session")
    session = manager.create(instance, controller, content, session_id="temporary-session")

    manager.restore_checkpoint(session, path, manifest_content=content)

    assert session.session_id == "stored-session"
    assert session.controller.session_id == "stored-session"
    assert session.working_memory.get("stored-session", "agent").turn_history == [("q", "a")]
    assert set(manager.sessions) == {"stored-session"}


def test_resume_from_checkpoint_rebuilds_session_from_embedded_manifest(tmp_path) -> None:
    from mas.runtime.session import ManifestRef

    content = {"name": "agent"}
    store = JsonCheckpointStore(tmp_path)
    path = store.save(
        {
            "version": 2,
            "label": "portable",
            "turn": 5,
            "lineage": {
                "session_id": "portable-session",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "portable-session",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {"position": 5}, "run": {}},
            "working_memory": [
                {
                    "agent_id": "agent",
                    "turn_history": [["q1", "a1"]],
                    "committed_messages": [],
                    "conversation_chunks": None,
                }
            ],
            "manifest": {
                "content": content,
                "content_hash": ManifestRef.from_content(content).content_hash,
            },
        },
        label="portable",
    )

    session = SessionManager(store, _factory).resume_from_checkpoint(path)

    assert session.session_id == "portable-session"
    assert session.controller._turn == 5
    assert session.instance.loaded == {"q": {"position": 5}, "run": {}}
    assert session.working_memory.get("portable-session", "agent").turn_history == [("q1", "a1")]

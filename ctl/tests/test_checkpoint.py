from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from mas.ctl.adapters.checkpoint import InMemoryCheckpointStore, JsonCheckpointStore, _validate_checkpoint_payload
from mas.ctl.cli.commands.checkpoint import checkpoint_group


def test_checkpoint_validation_does_not_swallow_schema_errors(monkeypatch) -> None:
    def reject_payload(*args, **kwargs):
        raise ValueError("checkpoint schema mismatch")

    monkeypatch.setattr("mas.ctl.validate.validation_enabled", lambda: True)
    monkeypatch.setattr("mas.ctl.validate.validate_data", reject_payload)

    with pytest.raises(ValueError, match="checkpoint schema mismatch"):
        _validate_checkpoint_payload({"version": 1, "kernel": {"q": {}, "run": {}}})


def test_json_store_round_trips_full_v2_payload_and_keeps_kernel_load_api(tmp_path) -> None:
    payload = {
        "version": 2,
        "label": "fork-point",
        "turn": 2,
        "lineage": {
            "session_id": "session-1",
            "parent_session_id": None,
            "forked_from_checkpoint": None,
            "root_session_id": "session-1",
            "created_at": "2026-09-30T12:00:00+00:00",
        },
        "kernel": {"q": {}, "run": {}},
        "working_memory": [
            {
                "agent_id": "agent-1",
                "turn_history": [["hello", "world"]],
                "committed_messages": [{"role": "user", "content": "hello"}],
                "conversation_chunks": None,
            }
        ],
        "manifest": {"content": {"name": "agent-1"}, "content_hash": "a" * 64},
    }
    store = JsonCheckpointStore(tmp_path)

    path = store.save(payload, label="fork-point")

    assert store.load_payload(path) == payload
    assert store.load(path) == payload["kernel"]


def test_checkpoint_fork_creates_new_lineage_without_modifying_source(tmp_path) -> None:
    from mas.runtime.session import ManifestRef

    manifest = {"name": "agent"}
    source_store = JsonCheckpointStore(tmp_path)
    source = source_store.save(
        {
            "version": 2,
            "label": "baseline",
            "turn": 3,
            "lineage": {
                "session_id": "parent",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "parent",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {}, "run": {}},
            "working_memory": [],
            "manifest": {
                "content": manifest,
                "content_hash": ManifestRef.from_content(manifest).content_hash,
            },
        },
        label="baseline",
    )

    result = CliRunner().invoke(checkpoint_group, ["fork", str(source), "--session-id", "child"])

    assert result.exit_code == 0, result.output
    fork_path = Path(result.output.strip())
    forked = source_store.load_payload(fork_path)
    assert forked["lineage"]["session_id"] == "child"
    assert forked["lineage"]["parent_session_id"] == "parent"
    assert forked["lineage"]["forked_from_checkpoint"] == source.name
    assert forked["backtrack_count"] == 0
    assert source_store.load_payload(source)["lineage"]["session_id"] == "parent"


def test_checkpoint_fork_seed_is_visible_to_retention(tmp_path) -> None:
    from mas.runtime.session import ManifestRef

    manifest = {"name": "agent"}
    store = JsonCheckpointStore(tmp_path)
    source = store.save(
        {
            "version": 2,
            "label": "baseline",
            "turn": 1,
            "lineage": {
                "session_id": "parent",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "parent",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {}, "run": {}},
            "working_memory": [],
            "manifest": {
                "content": manifest,
                "content_hash": ManifestRef.from_content(manifest).content_hash,
            },
        },
        label="parent-baseline",
    )

    result = CliRunner().invoke(checkpoint_group, ["fork", str(source), "--session-id", "child"])
    assert result.exit_code == 0, result.output
    fork_path = Path(result.output.strip())

    later = store.save({"q": {}, "run": {}}, label="child-turn-1")
    store.retain("child", "single", 1)

    assert later.exists()
    assert not fork_path.exists()
    assert source.exists()


def test_checkpoint_retention_prunes_only_the_selected_session(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path)
    first = store.save({"q": {}, "run": {}}, label="session-a-turn-1")
    second = store.save({"q": {}, "run": {}}, label="session-a-turn-2")
    other = store.save({"q": {}, "run": {}}, label="session-b-turn-1")

    store.retain("session-a", "last_n", 1)

    assert not first.exists()
    assert second.exists()
    assert other.exists()


def test_checkpoint_retention_uses_creation_order_not_filename_order(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path)
    older = store.save({"q": {}, "run": {}}, label="session-a-turn-2")
    newer = store.save({"q": {}, "run": {}}, label="session-a-turn-10")

    store.retain("session-a", "last_n", 1)

    assert not older.exists()
    assert newer.exists()


def test_in_memory_checkpoint_store_round_trips_payload() -> None:
    store = InMemoryCheckpointStore()
    payload = {
        "version": 2,
        "label": "mem",
        "turn": 0,
        "lineage": {
            "session_id": "s",
            "parent_session_id": None,
            "forked_from_checkpoint": None,
            "root_session_id": "s",
            "created_at": "2026-09-30T12:00:00+00:00",
        },
        "kernel": {"q": {}, "run": {}},
        "working_memory": [],
        "manifest": {"content": {}, "content_hash": "a" * 64},
    }

    path = store.save(payload, label="s-mem")

    assert store.load_payload(path) == payload


def test_hybrid_checkpoint_store_writes_memory_and_disk(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import HybridCheckpointStore, build_checkpoint_store
    from mas.library.standard.plugins.checkpoint import (
        HybridCheckpointStore as LibraryHybridCheckpointStore,
    )
    from mas.runtime.spec.checkpoint import parse_checkpoint_policy

    payload = {
        "version": 2,
        "label": "mix",
        "turn": 1,
        "lineage": {
            "session_id": "s",
            "parent_session_id": None,
            "forked_from_checkpoint": None,
            "root_session_id": "s",
            "created_at": "2026-09-30T12:00:00+00:00",
        },
        "kernel": {"q": {}, "run": {}},
        "working_memory": [],
        "manifest": {"content": {}, "content_hash": "a" * 64},
    }
    store = HybridCheckpointStore(tmp_path)
    path = store.save(payload, label="s-mix")
    assert path.parent == tmp_path
    assert path.is_file()
    assert store.load_payload(path)["label"] == "mix"
    assert store.list_checkpoints() == [path]
    policy = parse_checkpoint_policy({"mode": "every_turn", "storage": "hybrid"})
    built = build_checkpoint_store(policy, tmp_path)
    assert isinstance(built, LibraryHybridCheckpointStore)

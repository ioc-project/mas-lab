#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Checkpoint schema validation on the ctl store path."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.adapters.checkpoint import (
    HybridCheckpointStore,
    InMemoryCheckpointStore,
    JsonCheckpointStore,
    build_checkpoint_store,
)
from mas.runtime.spec.checkpoint import parse_checkpoint_policy

_BAD_V2 = {"version": 2, "kernel": {"q": {}, "run": {}}}


def test_disk_storage_without_a_directory_is_a_value_error() -> None:
    policy = parse_checkpoint_policy({"mode": "every_turn", "storage": "disk"})
    with pytest.raises(ValueError, match="requires a directory"):
        build_checkpoint_store(policy, None)


def test_hybrid_storage_without_a_directory_is_a_value_error() -> None:
    policy = parse_checkpoint_policy({"mode": "every_turn", "storage": "hybrid"})
    with pytest.raises(ValueError, match="requires a directory"):
        build_checkpoint_store(policy, None)


def test_json_store_save_rejects_a_bad_payload(tmp_path: Path) -> None:
    store = JsonCheckpointStore(tmp_path)
    with pytest.raises(ValueError, match="validation failed"):
        store.save(_BAD_V2, label="bad")


def test_json_store_load_payload_rejects_a_bad_payload(tmp_path: Path) -> None:
    store = JsonCheckpointStore(tmp_path)
    path = tmp_path / "bad.checkpoint.json"
    path.write_text('{"version": 2, "kernel": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="validation failed"):
        store.load_payload(path)


def test_in_memory_store_save_rejects_a_bad_payload() -> None:
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="validation failed"):
        store.save(_BAD_V2, label="bad")


def test_in_memory_store_load_payload_rejects_a_bad_payload() -> None:
    from mas.ctl.adapters.checkpoint import _validate_checkpoint_payload

    store = InMemoryCheckpointStore()
    store.payload_validator = None
    path = store.save(_BAD_V2, label="bad")
    store.payload_validator = _validate_checkpoint_payload
    with pytest.raises(ValueError, match="validation failed"):
        store.load_payload(path)


def test_hybrid_disk_save_rejects_a_bad_payload(tmp_path: Path) -> None:
    store = HybridCheckpointStore(tmp_path)
    with pytest.raises(ValueError, match="validation failed"):
        store.disk.save(_BAD_V2, label="bad")


def test_hybrid_memory_save_rejects_a_bad_payload(tmp_path: Path) -> None:
    store = HybridCheckpointStore(tmp_path)
    with pytest.raises(ValueError, match="validation failed"):
        store.memory.save(_BAD_V2, label="bad")


def test_build_checkpoint_store_attaches_the_validator(tmp_path: Path) -> None:
    policy = parse_checkpoint_policy({"mode": "every_turn", "storage": "disk"})
    store = build_checkpoint_store(policy, tmp_path)
    assert store is not None
    assert store.payload_validator is not None
    with pytest.raises(ValueError, match="validation failed"):
        store.save(_BAD_V2, label="bad")


def test_build_checkpoint_store_attaches_hybrid_disk_and_memory_validators(
    tmp_path: Path,
) -> None:
    from mas.library.standard.plugins.checkpoint import HybridCheckpointStore

    policy = parse_checkpoint_policy({"mode": "every_turn", "storage": "hybrid"})
    store = build_checkpoint_store(policy, tmp_path)
    assert isinstance(store, HybridCheckpointStore)
    assert store.disk.payload_validator is not None
    assert store.memory.payload_validator is not None
    with pytest.raises(ValueError, match="validation failed"):
        store.save(_BAD_V2, label="bad")

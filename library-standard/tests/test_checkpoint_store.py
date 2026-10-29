#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Library checkpoint stores call an optional payload validator."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.library.standard.plugins.checkpoint import InMemoryCheckpointStore, JsonCheckpointStore


def test_in_memory_store_runs_the_payload_validator() -> None:
    seen: list[dict] = []

    def reject(payload: dict) -> None:
        seen.append(payload)
        raise ValueError("bad checkpoint")

    store = InMemoryCheckpointStore(payload_validator=reject)
    with pytest.raises(ValueError, match="bad checkpoint"):
        store.save({"version": 2, "kernel": {}}, label="bad")
    assert seen

    store = InMemoryCheckpointStore()
    path = store.save({"version": 2, "kernel": {"q": {}}}, label="ok")
    store.payload_validator = seen.append
    loaded = store.load_payload(path)
    assert loaded["kernel"] == {"q": {}}
    assert seen[-1] is loaded
    store.payload_validator = reject
    with pytest.raises(ValueError, match="bad checkpoint"):
        store.load_payload(path)
    with pytest.raises(FileNotFoundError):
        store.load_payload(Path("missing.checkpoint.json"))


def test_json_store_runs_the_payload_validator(tmp_path: Path) -> None:
    seen: list[dict] = []

    def reject(payload: dict) -> None:
        seen.append(payload)
        raise ValueError("bad checkpoint")

    store = JsonCheckpointStore(tmp_path, payload_validator=reject)
    with pytest.raises(ValueError, match="bad checkpoint"):
        store.save({"version": 2, "kernel": {}}, label="bad")
    assert seen

    store = JsonCheckpointStore(tmp_path)
    path = store.save({"version": 2, "kernel": {"q": {}}}, label="ok")
    store.payload_validator = reject
    with pytest.raises(ValueError, match="bad checkpoint"):
        store.load_payload(path)

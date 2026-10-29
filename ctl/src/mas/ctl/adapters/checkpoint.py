#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Checkpoint persistence — session snapshots above the kernel.

Implementations are library ``checkpoint_store`` plugins. This module
re-exports them so ctl callers keep a stable import path. Schema
validation is attached here so library-standard does not import ctl.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mas.library.standard.plugins.checkpoint import (
    HybridCheckpointStore as LibraryHybridCheckpointStore,
)
from mas.library.standard.plugins.checkpoint import (
    InMemoryCheckpointStore as LibraryInMemoryCheckpointStore,
)
from mas.library.standard.plugins.checkpoint import (
    JsonCheckpointStore as LibraryJsonCheckpointStore,
)
from mas.library.standard.plugins.checkpoint import instantiate_store
from mas.runtime.session.state import CheckpointStore
from mas.runtime.spec.checkpoint import CheckpointPolicy


def _validate_checkpoint_payload(data: dict) -> None:
    from mas.ctl.validate import validate_data, validation_enabled

    if validation_enabled():
        validate_data(data, kind="checkpoint", source="checkpoint.json").raise_if_failed()


def attach_checkpoint_validator(store: object | None) -> None:
    """Attach ctl schema validation to a library checkpoint store."""
    if store is None:
        return
    if isinstance(store, LibraryHybridCheckpointStore):
        attach_checkpoint_validator(store.disk)
        attach_checkpoint_validator(store.memory)
        return
    if isinstance(store, (LibraryJsonCheckpointStore, LibraryInMemoryCheckpointStore)):
        store.payload_validator = _validate_checkpoint_payload


@dataclass
class JsonCheckpointStore(LibraryJsonCheckpointStore):
    def __post_init__(self) -> None:
        super().__post_init__()
        attach_checkpoint_validator(self)


@dataclass
class InMemoryCheckpointStore(LibraryInMemoryCheckpointStore):
    def __post_init__(self) -> None:
        attach_checkpoint_validator(self)


@dataclass
class HybridCheckpointStore(LibraryHybridCheckpointStore):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.disk = JsonCheckpointStore(self.directory)
        attach_checkpoint_validator(self.memory)


__all__ = [
    "CheckpointStore",
    "HybridCheckpointStore",
    "InMemoryCheckpointStore",
    "JsonCheckpointStore",
    "attach_checkpoint_validator",
    "build_checkpoint_store",
    "_validate_checkpoint_payload",
]


def build_checkpoint_store(
    policy: CheckpointPolicy,
    directory: Path | None,
) -> JsonCheckpointStore | InMemoryCheckpointStore | HybridCheckpointStore | None:
    """Resolve ``spec.checkpoint.storage`` through the checkpoint_store plugin type."""
    kind = policy.resolved_storage()
    if kind == "none":
        if directory is None:
            return None
        kind = "disk"
    try:
        store = instantiate_store(kind, directory)
    except ValueError:
        if kind == "memory":
            store = InMemoryCheckpointStore()
        elif directory is None:
            raise ValueError(f"checkpoint storage {kind!r} requires a directory") from None
        elif kind == "hybrid":
            store = HybridCheckpointStore(directory)
        else:
            store = JsonCheckpointStore(directory)
    attach_checkpoint_validator(store)
    return store

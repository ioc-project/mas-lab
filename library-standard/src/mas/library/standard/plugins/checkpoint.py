#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Checkpoint store plugins — persist/load for the kernel ``persist`` op.

Storage is a library plugin type, not a boundary slot and not a kernel op.
The kernel already has ``snapshot`` (in-process tree) and ``persist`` (write
through this contract). Implementations live in mas-library-standard.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


@dataclass
class JsonCheckpointStore:
    """Disk plugin: one JSON file per checkpoint (kernel persist)."""

    plugin_id: ClassVar[str] = "disk"
    directory: Path
    memory_seeds: list[dict] = field(default_factory=list)
    turn_counter: int = 0
    payload_validator: Callable[[dict], None] | None = None

    def __post_init__(self) -> None:
        self.directory = Path(self.directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: dict, *, label: str = "") -> Path:
        self.turn_counter += 1
        name = label or f"turn-{self.turn_counter:04d}"
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
        path = self.directory / f"{safe}.checkpoint.json"
        if snapshot.get("version") == 2:
            payload = dict(snapshot)
        else:
            payload = {
                "version": 1,
                "label": label or name,
                "turn": self.turn_counter,
                "kernel": snapshot,
                "memory_seeds": list(self.memory_seeds),
            }
        if self.payload_validator is not None:
            self.payload_validator(payload)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def load(self, path: Path) -> dict:
        data = self.load_payload(path)
        return data["kernel"]

    def load_payload(self, path: Path) -> dict:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if self.payload_validator is not None:
            self.payload_validator(data)
        self.memory_seeds = list(data.get("memory_seeds") or [])
        self.turn_counter = int(data.get("turn", 0))
        return data

    def list_checkpoints(self) -> list[Path]:
        return sorted(self.directory.glob("*.checkpoint.json"))

    def retain(self, session_id: str, mode: str, n: int) -> None:
        matching = sorted(
            self.directory.glob(f"{session_id}-*.checkpoint.json"),
            key=lambda path: path.stat().st_mtime_ns,
        )
        keep_count = 1 if mode == "single" else n if mode == "last_n" else len(matching)
        for path in matching[:-keep_count] if keep_count else matching:
            path.unlink()


@dataclass
class InMemoryCheckpointStore:
    """Memory plugin: process-local map for backtrack without files."""

    plugin_id: ClassVar[str] = "memory"
    snapshots: dict[Path, dict] = field(default_factory=dict)
    counter: int = 0
    memory_seeds: list[dict] = field(default_factory=list)
    payload_validator: Callable[[dict], None] | None = None

    def save(self, snapshot: dict, *, label: str = "") -> Path:
        self.counter += 1
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
        path = Path(f"{safe or 'checkpoint'}-{self.counter:08d}.checkpoint.json")
        if self.payload_validator is not None:
            self.payload_validator(snapshot)
        self.snapshots[path] = deepcopy(snapshot)
        return path

    def load(self, path: Path) -> dict:
        return self.load_payload(path)["kernel"]

    def load_payload(self, path: Path) -> dict:
        try:
            data = deepcopy(self.snapshots[Path(path)])
        except KeyError as exc:
            raise FileNotFoundError(path) from exc
        if self.payload_validator is not None:
            self.payload_validator(data)
        return data

    def list_checkpoints(self) -> list[Path]:
        return sorted(self.snapshots)

    def retain(self, session_id: str, mode: str, n: int) -> None:
        matching = sorted(path for path in self.snapshots if path.name.startswith(f"{session_id}-"))
        keep_count = 1 if mode == "single" else n if mode == "last_n" else len(matching)
        for path in matching[:-keep_count] if keep_count else matching:
            del self.snapshots[path]


@dataclass
class HybridCheckpointStore:
    """Hybrid plugin: memory copy plus disk files (kernel persist + live backtrack)."""

    plugin_id: ClassVar[str] = "hybrid"
    directory: Path
    memory: InMemoryCheckpointStore = field(default_factory=InMemoryCheckpointStore)
    _alias: dict[Path, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory).expanduser()
        self.disk = JsonCheckpointStore(self.directory)

    @property
    def memory_seeds(self) -> list[dict]:
        return self.disk.memory_seeds

    @memory_seeds.setter
    def memory_seeds(self, value: list[dict]) -> None:
        self.disk.memory_seeds = list(value)

    def save(self, snapshot: dict, *, label: str = "") -> Path:
        mem_path = self.memory.save(snapshot, label=label)
        disk_path = self.disk.save(snapshot, label=label)
        self._alias[mem_path] = disk_path
        self._alias[disk_path] = disk_path
        return disk_path

    def load(self, path: Path) -> dict:
        return self.load_payload(path)["kernel"]

    def load_payload(self, path: Path) -> dict:
        path = Path(path)
        try:
            return self.memory.load_payload(path)
        except FileNotFoundError:
            aliased = self._alias.get(path)
            if aliased is not None and aliased != path:
                try:
                    return self.memory.load_payload(aliased)
                except FileNotFoundError:
                    pass
        return self.disk.load_payload(path)

    def list_checkpoints(self) -> list[Path]:
        return self.disk.list_checkpoints() or self.memory.list_checkpoints()

    def retain(self, session_id: str, mode: str, n: int) -> None:
        self.memory.retain(session_id, mode, n)
        self.disk.retain(session_id, mode, n)


def instantiate_store(kind: str, directory: Path | None) -> Any:
    """Build a store from the ``checkpoint_store`` plugin registry."""
    from mas.runtime.registry import get_registry

    info = get_registry().resolve_by_type("checkpoint_store", kind)
    if info is None:
        raise ValueError(f"unknown checkpoint_store plugin {kind!r}")
    cls = info.load_class()
    if kind == "memory":
        return cls()
    if directory is None:
        raise ValueError(f"checkpoint storage {kind!r} requires a directory")
    return cls(directory=directory)

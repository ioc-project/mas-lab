#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Opaque artifact ids for run outputs. Paths never leave this module."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _artifact_id(run_id: str, relative: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{relative}".encode("utf-8")).hexdigest()
    return digest


@dataclass
class Artifact:
    artifact_id: str
    name: str
    kind: str
    size: int
    media_type: str
    children: list["Artifact"] = field(default_factory=list)
    # Internal
    path: Path | None = None

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "media_type": self.media_type,
        }
        if self.children:
            payload["children"] = [c.public_dict() for c in self.children]
        return payload


class ArtifactRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, Artifact] = {}
        self._by_run: dict[str, list[str]] = {}

    def register_tree(self, run_id: str, root: Path) -> list[Artifact]:
        if not root.exists():
            return []
        artifacts: list[Artifact] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            art = self.register_file(run_id, rel, path)
            artifacts.append(art)
        return artifacts

    def register_file(self, run_id: str, name: str, path: Path) -> Artifact:
        aid = _artifact_id(run_id, name)
        media, _ = mimetypes.guess_type(name)
        kind = "file"
        suffix = Path(name).suffix.lower()
        if suffix in {".csv"}:
            kind = "csv"
        elif suffix in {".json", ".jsonl"}:
            kind = "json"
        elif suffix in {".yaml", ".yml"}:
            kind = "yaml"
        elif suffix in {".html", ".htm"}:
            kind = "html"
        elif suffix in {".svg"}:
            kind = "svg"
        elif suffix in {".png", ".jpg", ".jpeg"}:
            kind = "image"
        art = Artifact(
            artifact_id=aid,
            name=Path(name).name,
            kind=kind,
            size=path.stat().st_size if path.exists() else 0,
            media_type=media or "application/octet-stream",
            path=path.resolve(),
        )
        self._by_id[aid] = art
        self._by_run.setdefault(run_id, []).append(aid)
        return art

    def get(self, artifact_id: str) -> Artifact | None:
        return self._by_id.get(artifact_id)

    def for_run(self, run_id: str) -> list[Artifact]:
        return [self._by_id[i] for i in self._by_run.get(run_id, []) if i in self._by_id]

    def drop_run(self, run_id: str) -> None:
        for aid in self._by_run.pop(run_id, []):
            self._by_id.pop(aid, None)


_registry: ArtifactRegistry | None = None


def get_artifact_registry() -> ArtifactRegistry:
    global _registry
    if _registry is None:
        _registry = ArtifactRegistry()
    return _registry


def set_artifact_registry(registry: ArtifactRegistry | None) -> None:
    global _registry
    _registry = registry

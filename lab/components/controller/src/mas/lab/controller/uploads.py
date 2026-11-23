#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Multipart uploads referenced by opaque upload_id (never a client path)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

MANIFEST_UPLOAD_SUFFIXES = frozenset({".yaml", ".yml", ".json"})


def is_manifest_upload(filename: str) -> bool:
    """True for a YAML or JSON file name, read as a file system would store it.

    Surrounding spaces and trailing dots are dropped and case is ignored, so ``u.yaml.`` and
    ``"u.yaml "`` count as YAML, exactly as the item that reads the upload sees them.
    """
    name = Path(str(filename or "")).name.strip().rstrip(". ").casefold()
    return Path(name).suffix in MANIFEST_UPLOAD_SUFFIXES


@dataclass
class Upload:
    upload_id: str
    name: str
    size: int
    path: Path

    def public_dict(self) -> dict:
        return {"upload_id": self.upload_id, "name": self.name, "size": self.size}


class UploadStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, Upload] = {}

    def save(self, filename: str, data: bytes) -> Upload:
        from mas.lab.controller.constants import MAX_UPLOAD_BYTES

        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
        upload_id = str(uuid.uuid4())
        safe_name = Path(filename).name or "upload"
        path = self.root / upload_id
        path.write_bytes(data)
        item = Upload(upload_id=upload_id, name=safe_name, size=len(data), path=path)
        self._items[upload_id] = item
        return item

    def get(self, upload_id: str) -> Upload | None:
        return self._items.get(upload_id)

    def delete(self, upload_id: str) -> None:
        item = self._items.pop(upload_id, None)
        if item is not None:
            item.path.unlink(missing_ok=True)


_store: UploadStore | None = None


def get_upload_store() -> UploadStore:
    global _store
    if _store is None:
        from mas.lab.controller.config import controller_dir

        _store = UploadStore(controller_dir() / "state" / "uploads")
    return _store


def init_upload_store(root: Path) -> UploadStore:
    global _store
    _store = UploadStore(root)
    return _store

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parsed documents for one lab batch. No run identity (session, credentials, tags)."""

from __future__ import annotations

import copy
import hashlib
import threading
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

import yaml

_current: ContextVar["BatchCache | None"] = ContextVar("mas_batch_cache", default=None)


class BatchCache:
    """Read-only YAML trees keyed by path and checked by content hash. Callers get a copy.

    The file is read and hashed on every load; ``yaml.safe_load`` runs only when
    the hash is new. An edit replaces the entry of its path.
    """

    def __init__(self) -> None:
        self._docs: dict[str, tuple[str, Any]] = {}
        self._schema: dict[Path, Any] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)

    def load_yaml(self, path: Path) -> Any:
        resolved = Path(path).expanduser().resolve()
        key = str(resolved)
        text = resolved.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            hit = self._docs.get(key)
        if hit is not None and hit[0] == digest:
            return copy.deepcopy(hit[1])
        data = yaml.safe_load(text) or {}
        with self._lock:
            self._docs[key] = (digest, data)
        return copy.deepcopy(data)

    def load_mapping(self, path: Path) -> dict[str, Any]:
        raw = self.load_yaml(path)
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: manifest must be a YAML mapping")
        return raw

    def schema_registry(self, root: Path) -> Any | None:
        with self._lock:
            return self._schema.get(Path(root).resolve())

    def store_schema_registry(self, root: Path, registry: Any) -> None:
        with self._lock:
            self._schema[Path(root).resolve()] = registry

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()
            self._schema.clear()


def bind_batch_cache(cache: BatchCache) -> Token:
    return _current.set(cache)


def reset_batch_cache(token: Token) -> None:
    _current.reset(token)


def current_batch_cache() -> BatchCache | None:
    return _current.get()

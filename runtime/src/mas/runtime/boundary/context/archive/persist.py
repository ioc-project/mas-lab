#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Archive on disk — every chunk, not only spilled overflow.

Off unless ``persist_dir`` is set (ctl sets that only when
``mas_ctl.archive.enabled`` is true). Then each leaf and summary is written
as soon as it exists. RAM is a cache: folded leaves drop their in-memory
payload after the watermark. Shallow checkpoints reference this directory;
deep checkpoints hydrate the payloads.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mas.runtime.boundary.context.archive.chunks import MessageChunk
from mas.runtime.spec.schema_defaults_generated import ARCHIVE_MEMORY_WATERMARK_BYTES

_log = logging.getLogger(__name__)

DEFAULT_MEMORY_WATERMARK_BYTES = ARCHIVE_MEMORY_WATERMARK_BYTES


def chunk_payload_bytes(chunk: Any) -> int:
    if not isinstance(chunk, MessageChunk):
        return len(str(getattr(chunk, "text", "") or "").encode())
    if not chunk.messages:
        return 0
    return len(json.dumps(chunk.messages, ensure_ascii=False, default=str).encode())


def store_payload_bytes(store: Any) -> int:
    return sum(chunk_payload_bytes(chunk) for chunk in store.chunks.values())


def _chunk_path(directory: Path, chunk_id: str) -> Path | None:
    """File for one chunk. Reject ids that would leave ``directory``."""
    raw = str(chunk_id or "")
    name = Path(raw).name
    if not name or name != raw:
        _log.warning("archive persist skipped (unsafe chunk_id %r)", chunk_id)
        return None
    return directory / f"{name}.json"


def _hot_message_ids(store: Any) -> set[str]:
    ids = getattr(store, "covering_message_ids", None)
    if callable(ids):
        return set(ids())
    covering = list(getattr(store, "order", ()))
    return {
        cid
        for cid in covering
        if isinstance(store.chunks.get(cid), MessageChunk)
    }


def persist_chunk(chunk: Any, directory: Path) -> bool:
    """Write one chunk document. Returns False when the directory cannot be used."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.warning("archive persist skipped (cannot create %s): %s", directory, exc)
        return False
    path = _chunk_path(directory, chunk.chunk_id)
    if path is None:
        return False
    if isinstance(chunk, MessageChunk) and not chunk.messages and chunk.spilled and path.exists():
        return True
    try:
        path.write_text(
            json.dumps(chunk.to_dict(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("archive persist skipped for %s: %s", chunk.chunk_id, exc)
        return False
    return True


def persist_store(store: Any, directory: Path) -> int:
    """Write every in-memory chunk. Returns the number of successful writes."""
    written = 0
    for chunk in store.chunks.values():
        if persist_chunk(chunk, directory):
            written += 1
    return written


def spill_cold_payloads(
    store: Any,
    directory: Path,
    *,
    watermark: int = DEFAULT_MEMORY_WATERMARK_BYTES,
) -> int:
    """Drop folded message payloads from RAM until under ``watermark``.

    Chunks are already on disk when ``persist_dir`` is set. This only evicts
    the hot copy. Returns the number of chunks dropped from RAM. ``watermark
    <= 0`` means never evict.
    """
    if watermark <= 0:
        return 0
    total = store_payload_bytes(store)
    if total <= watermark:
        return 0
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.warning("archive spill skipped (cannot create %s): %s", directory, exc)
        return 0

    hot = _hot_message_ids(store)
    spilled = 0
    for cid, chunk in list(store.chunks.items()):
        if total <= watermark:
            break
        if not isinstance(chunk, MessageChunk) or chunk.spilled or cid in hot:
            continue
        if not chunk.messages:
            continue
        nbytes = chunk_payload_bytes(chunk)
        if not persist_chunk(chunk, directory):
            continue
        chunk.messages = []
        chunk.spilled = True
        total -= nbytes
        spilled += 1
    return spilled


def load_chunk_payload(chunk: MessageChunk, directory: Path | None) -> list[dict[str, Any]]:
    """Return messages, loading from disk when the leaf was spilled.

    Does not hydrate ``chunk.messages`` — inspect must not undo a spill.
    Accepts both a raw message list and a full chunk document on disk.
    """
    if chunk.messages:
        return list(chunk.messages)
    if not chunk.spilled or directory is None:
        return []
    path = _chunk_path(directory, chunk.chunk_id)
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("archive load failed for %s: %s", chunk.chunk_id, exc)
        return []
    if isinstance(raw, dict):
        raw = raw.get("messages") or []
    return list(raw) if isinstance(raw, list) else []


def load_chunk_document(chunk_id: str, directory: Path | None) -> dict[str, Any] | None:
    """Load a persisted chunk document (leaf or summary)."""
    if directory is None:
        return None
    path = _chunk_path(directory, chunk_id)
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("archive load failed for %s: %s", chunk_id, exc)
        return None
    return raw if isinstance(raw, dict) else None

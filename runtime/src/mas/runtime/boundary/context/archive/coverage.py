#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Covering set: which archive chunks assemble into the next model request."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mas.runtime.boundary.context.archive.chunks import Chunk
    from mas.runtime.boundary.context.archive.store import ConversationChunkStore

from mas.runtime.boundary.context.archive.chunks import MessageChunk, SummaryChunk


def expand_leaves(chunks: dict[str, Chunk], view_ids: list[str]) -> list[str]:
    """Depth-first leaf ids covered by ``view_ids`` (nested summaries allowed)."""
    out: list[str] = []
    seen: set[str] = set()

    def walk(cid: str) -> None:
        if cid in seen or cid not in chunks:
            return
        seen.add(cid)
        chunk = chunks[cid]
        if isinstance(chunk, SummaryChunk):
            for child in chunk.child_chunk_ids:
                walk(child)
            return
        out.append(cid)

    for cid in view_ids:
        walk(cid)
    return out


def message_chunk_ids(chunks: dict[str, Chunk]) -> list[str]:
    return [cid for cid, chunk in chunks.items() if isinstance(chunk, MessageChunk)]


def covers_archive(chunks: dict[str, Chunk], view_ids: list[str]) -> bool:
    """Every message leaf is reachable exactly once from the covering view."""
    expanded = expand_leaves(chunks, view_ids)
    leaves = message_chunk_ids(chunks)
    return len(expanded) == len(leaves) == len(set(expanded)) and set(expanded) == set(leaves)


def view_id(view_ids: list[str]) -> str:
    """Stable id for one request's covering set (annotate LLM calls with this)."""
    digest = hashlib.sha256("\0".join(view_ids).encode()).hexdigest()
    return digest[:16]


def inspect_tree(store: ConversationChunkStore) -> dict[str, Any]:
    """Debugger dump: covering set, summaries, and which leaves are still verbatim."""
    covering = list(store.covering_ids())
    in_request = set(expand_leaves(store.chunks, covering))
    summaries = [
        {
            "chunk_id": chunk.chunk_id,
            "child_chunk_ids": list(chunk.child_chunk_ids),
            "text": chunk.text,
            "in_request": chunk.chunk_id in covering,
        }
        for chunk in store.chunks.values()
        if isinstance(chunk, SummaryChunk)
    ]
    load = getattr(store, "_messages_of", None)
    leaves = [
        {
            "chunk_id": cid,
            "pin_policy": str(getattr(store.chunks[cid], "pin_policy", "") or ""),
            "pinned": bool(getattr(store.chunks[cid], "pinned", False)),
            "spilled": bool(getattr(store.chunks[cid], "spilled", False)),
            "in_request": cid in covering,
            "covered_by_summary": cid in in_request and cid not in covering,
            "messages": list(load(store.chunks[cid]))
            if callable(load) and isinstance(store.chunks[cid], MessageChunk)
            else list(getattr(store.chunks[cid], "messages", []) or []),
        }
        for cid in message_chunk_ids(store.chunks)
    ]
    return {
        "view_id": view_id(covering),
        "covering_ids": covering,
        "covers_archive": covers_archive(store.chunks, covering),
        "summaries": summaries,
        "leaves": leaves,
    }

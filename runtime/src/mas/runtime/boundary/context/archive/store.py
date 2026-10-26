#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session conversation archive — append-only leaves, covering set for assembly.

``order`` is the covering set: message leaves and/or summary nodes. Expanding
summaries reaches every leaf exactly once. ``project_messages`` turns that set
into the OpenAI ``messages[]`` sent to the model. Chunk ids stay internal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.runtime.boundary.context.archive.chunks import (
    Chunk,
    MessageChunk,
    SummaryChunk,
    chunk_from_dict,
    new_chunk_id,
)
from mas.runtime.boundary.context.archive.coverage import (
    covers_archive,
    inspect_tree,
    view_id,
)
from mas.runtime.boundary.context.archive.persist import (
    load_chunk_payload,
    persist_chunk,
    persist_store,
    spill_cold_payloads,
)
from mas.runtime.boundary.context.chunk_meta import (
    PIN_NEVER,
    is_summary_message,
    strongest_pin_policy,
    summary_message,
)
from mas.runtime.spec.schema_defaults_generated import ARCHIVE_MEMORY_WATERMARK_BYTES


@dataclass
class ConversationChunkStore:
    """Durable conversation archive. Bounded in the *request view*, not storage."""

    chunks: dict[str, Chunk] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    summary_chunk_id: str = ""
    persist_dir: str = ""
    memory_watermark_bytes: int = ARCHIVE_MEMORY_WATERMARK_BYTES

    def covering_ids(self) -> list[str]:
        ids = list(self.order)
        if self.summary_chunk_id and self.summary_chunk_id not in ids:
            ids = [self.summary_chunk_id, *ids]
        return ids

    def covering_message_ids(self) -> set[str]:
        return {
            cid
            for cid in self.covering_ids()
            if isinstance(self.chunks.get(cid), MessageChunk)
        }

    @property
    def view_id(self) -> str:
        return view_id(self.covering_ids())

    def configure(
        self,
        *,
        persist_dir: str | None = None,
        memory_watermark_bytes: int | None = None,
    ) -> None:
        if persist_dir:
            self.persist_dir = str(persist_dir)
            self.persist_all()
        if memory_watermark_bytes is not None:
            self.memory_watermark_bytes = max(0, int(memory_watermark_bytes))
        self.maybe_spill()

    def persist_all(self) -> None:
        if not self.persist_dir:
            return
        persist_store(self, Path(self.persist_dir))

    def _persist(self, chunk: Chunk) -> None:
        if not self.persist_dir:
            return
        persist_chunk(chunk, Path(self.persist_dir))

    def maybe_spill(self) -> None:
        if not self.persist_dir:
            return
        spill_cold_payloads(
            self,
            Path(self.persist_dir),
            watermark=self.memory_watermark_bytes,
        )

    def _messages_of(self, chunk: MessageChunk) -> list[dict[str, Any]]:
        if chunk.messages:
            return list(chunk.messages)
        directory = Path(self.persist_dir) if self.persist_dir else None
        return load_chunk_payload(chunk, directory)

    def append_turn(self, messages: list[dict[str, Any]]) -> str:
        if not messages:
            return ""
        chunk = MessageChunk(
            chunk_id=new_chunk_id(),
            messages=list(messages),
            pin_policy=strongest_pin_policy(messages),
        )
        self.chunks[chunk.chunk_id] = chunk
        self.order.append(chunk.chunk_id)
        self._persist(chunk)
        self.maybe_spill()
        return chunk.chunk_id

    def fold(self, child_ids: list[str], text: str) -> str:
        """Replace a covering prefix with a summary node; children stay stored.

        ``pin_policy: never`` leaves in the prefix stay in the covering
        view (after the summary) so they are never folded out of the request.
        """
        if not child_ids:
            return ""
        if self.order[: len(child_ids)] != child_ids:
            raise ValueError("fold child_ids must be a prefix of the covering view")
        pinned = [
            cid
            for cid in child_ids
            if isinstance(self.chunks.get(cid), MessageChunk)
            and self.chunks[cid].pin_policy == PIN_NEVER
        ]
        folded = [cid for cid in child_ids if cid not in pinned]
        if not folded:
            return ""
        summary = SummaryChunk(
            chunk_id=new_chunk_id(),
            text=text,
            child_chunk_ids=list(folded),
        )
        self.chunks[summary.chunk_id] = summary
        self.order = [summary.chunk_id, *pinned, *self.order[len(child_ids) :]]
        self.summary_chunk_id = summary.chunk_id
        self._persist(summary)
        self.maybe_spill()
        return summary.chunk_id

    def cover_with_managed(self, managed: list[dict[str, Any]]) -> None:
        """Align the covering view with an assembled history list.

        A leading summary row becomes a nested summary over the prefix
        that is no longer verbatim. Message leaves stay in ``chunks``.
        """
        if not self.order:
            return
        summary_text = ""
        rest = list(managed)
        if rest and is_summary_message(rest[0]):
            content = str(rest[0].get("content") or "")
            summary_text = content.split("\n", 1)[-1] if "\n" in content else ""
            rest = rest[1:]

        if not rest:
            self.fold(list(self.order), summary_text)
            return

        suffix_len = 0
        acc: list[dict[str, Any]] = []
        matched = False
        for cid in reversed(self.order):
            chunk = self.chunks.get(cid)
            if isinstance(chunk, MessageChunk):
                acc = self._messages_of(chunk) + acc
            elif isinstance(chunk, SummaryChunk):
                acc = [_summary_message(chunk)] + acc
            suffix_len += 1
            if acc == rest:
                matched = True
                break
        if not matched or suffix_len == len(self.order):
            return
        prefix = self.order[: len(self.order) - suffix_len]
        if prefix:
            self.fold(prefix, summary_text)

    def project_messages(self) -> list[dict[str, Any]]:
        """OpenAI chat messages for the covering set. Not chunk ids."""
        out: list[dict[str, Any]] = []
        for chunk_id in self.covering_ids():
            chunk = self.chunks.get(chunk_id)
            if isinstance(chunk, SummaryChunk):
                if chunk.text.strip():
                    out.append(_summary_message(chunk))
                continue
            if isinstance(chunk, MessageChunk):
                out.extend(self._messages_of(chunk))
        return out

    def covers_archive(self) -> bool:
        return covers_archive(self.chunks, self.covering_ids())

    def inspect(self) -> dict[str, Any]:
        return inspect_tree(self)

    def to_dict(self, *, hydrate: bool = True) -> dict[str, Any]:
        """Serialize the store.

        ``hydrate=True`` (deep) resolves spilled leaves so the blob is
        self-contained. ``hydrate=False`` (shallow) keeps pointers: empty
        message lists plus ``persist_dir``.
        """
        chunks: dict[str, Any] = {}
        for cid, chunk in self.chunks.items():
            data = chunk.to_dict()
            if isinstance(chunk, MessageChunk):
                if hydrate:
                    data["messages"] = self._messages_of(chunk)
                    data["spilled"] = False
                else:
                    data["messages"] = []
                    data["spilled"] = bool(self.persist_dir) or chunk.spilled
            chunks[cid] = data
        return {
            "summary_chunk_id": self.summary_chunk_id,
            "order": list(self.order),
            "chunks": chunks,
            "persist_dir": self.persist_dir,
            "memory_watermark_bytes": self.memory_watermark_bytes,
            "view_id": self.view_id,
        }

    def checkpoint_payload(self, portability: str = "self_contained") -> dict[str, Any]:
        """Deep (self-contained) or shallow (reference) checkpoint body.

        Shallow requires ``persist_dir``. Without an on-disk archive the
        payload falls back to deep so a lone checkpoint still restores.
        """
        kind = str(portability or "self_contained")
        if kind in {"shallow", "reference"} and self.persist_dir:
            return self.to_dict(hydrate=False)
        return self.to_dict(hydrate=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ConversationChunkStore:
        if not data:
            return cls()
        store = cls(
            summary_chunk_id=str(data.get("summary_chunk_id") or ""),
            order=[str(x) for x in data.get("order") or []],
            persist_dir=str(data.get("persist_dir") or ""),
            memory_watermark_bytes=int(
                data.get("memory_watermark_bytes") or ARCHIVE_MEMORY_WATERMARK_BYTES
            ),
        )
        for cid, raw in (data.get("chunks") or {}).items():
            store.chunks[str(cid)] = chunk_from_dict(raw)
        return store


def _summary_message(chunk: SummaryChunk) -> dict[str, Any]:
    return summary_message(chunk.text, child_count=len(chunk.child_chunk_ids))

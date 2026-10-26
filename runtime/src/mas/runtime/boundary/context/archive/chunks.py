#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Archive chunk types — leaves (messages) and summaries that point at children."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from mas.runtime.boundary.context.chunk_meta import PIN_NEVER
from mas.runtime.spec.schema_defaults_generated import CHUNK_PIN_POLICY_DEFAULT

ChunkKind = Literal["messages", "summary"]


def new_chunk_id() -> str:
    return f"chunk_{uuid4().hex[:12]}"


@dataclass
class MessageChunk:
    """Leaf: original messages. Never deleted; may spill payload to disk."""

    chunk_id: str
    messages: list[dict[str, Any]]
    kind: ChunkKind = "messages"
    pin_policy: str = CHUNK_PIN_POLICY_DEFAULT
    spilled: bool = False

    @property
    def pinned(self) -> bool:
        return self.pin_policy == PIN_NEVER

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "messages": list(self.messages),
            "pin_policy": self.pin_policy,
            "pinned": self.pinned,
            "spilled": self.spilled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageChunk:
        policy = str(data.get("pin_policy") or "")
        if not policy and data.get("pinned"):
            policy = PIN_NEVER
        return cls(
            chunk_id=str(data["chunk_id"]),
            messages=list(data.get("messages") or []),
            pin_policy=policy or CHUNK_PIN_POLICY_DEFAULT,
            spilled=bool(data.get("spilled", False)),
        )


@dataclass
class SummaryChunk:
    """Internal node: request-facing text covering ``child_chunk_ids`` (nested ok)."""

    chunk_id: str
    text: str
    child_chunk_ids: list[str]
    kind: ChunkKind = "summary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "text": self.text,
            "child_chunk_ids": list(self.child_chunk_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SummaryChunk:
        return cls(
            chunk_id=str(data["chunk_id"]),
            text=str(data.get("text") or ""),
            child_chunk_ids=[str(x) for x in data.get("child_chunk_ids") or []],
        )


Chunk = MessageChunk | SummaryChunk


def chunk_from_dict(data: dict[str, Any]) -> Chunk:
    if (data.get("kind") or "messages") == "summary":
        return SummaryChunk.from_dict(data)
    return MessageChunk.from_dict(data)

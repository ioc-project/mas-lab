#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic conversation-chunk metadata (pin policy, semantic, source).

Providers attach this object; the runtime never inspects markup. Schema:
``docs/schemas/runtime/fragments/chunk-meta.schema.yaml``.
"""

from __future__ import annotations

from typing import Any

from mas.runtime.spec.schema_defaults_generated import (
    CHUNK_MESSAGE_KEY,
    CHUNK_PIN_EPHEMERAL,
    CHUNK_PIN_NEVER,
    CHUNK_PIN_POLICIES,
    CHUNK_PIN_POLICY_DEFAULT,
    CHUNK_PIN_PREFER,
    CHUNK_SEMANTIC_CATALOG,
    CHUNK_SEMANTIC_DEFAULT,
    CHUNK_SEMANTIC_HISTORY,
    CHUNK_SEMANTIC_INSTRUCTION,
    CHUNK_SEMANTIC_SUMMARY,
    CHUNK_SEMANTIC_TOOL_RESULT,
    CHUNK_SEMANTICS,
    CHUNK_SUMMARY_HEADING,
)

PIN_NEVER = CHUNK_PIN_NEVER
PIN_PREFER = CHUNK_PIN_PREFER
PIN_EPHEMERAL = CHUNK_PIN_EPHEMERAL
PIN_POLICIES = CHUNK_PIN_POLICIES

SEMANTIC_CATALOG = CHUNK_SEMANTIC_CATALOG
SEMANTIC_INSTRUCTION = CHUNK_SEMANTIC_INSTRUCTION
SEMANTIC_HISTORY = CHUNK_SEMANTIC_HISTORY
SEMANTIC_TOOL_RESULT = CHUNK_SEMANTIC_TOOL_RESULT
SEMANTIC_SUMMARY = CHUNK_SEMANTIC_SUMMARY
SEMANTICS = CHUNK_SEMANTICS

_PIN_RANK = {PIN_NEVER: 0, PIN_PREFER: 1, PIN_EPHEMERAL: 2}


def normalize_chunk_meta(raw: Any) -> dict[str, Any]:
    """Return a schema-shaped chunk object. Unknown keys are dropped."""
    data = raw if isinstance(raw, dict) else {}
    pin = str(data.get("pin_policy") or CHUNK_PIN_POLICY_DEFAULT)
    if pin not in PIN_POLICIES:
        pin = CHUNK_PIN_POLICY_DEFAULT
    semantic = str(data.get("semantic") or CHUNK_SEMANTIC_DEFAULT)
    if semantic not in SEMANTICS:
        semantic = CHUNK_SEMANTIC_DEFAULT
    meta = {
        "pin_policy": pin,
        "semantic": semantic,
        "source": str(data.get("source") or ""),
        "section_id": str(data.get("section_id") or ""),
    }
    return {key: value for key, value in meta.items() if value or key in {"pin_policy", "semantic"}}


def message_chunk_meta(msg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(msg, dict):
        return normalize_chunk_meta({})
    return normalize_chunk_meta(msg.get(CHUNK_MESSAGE_KEY))


def attach_chunk_meta(msg: dict[str, Any], raw: Any) -> dict[str, Any]:
    """Write normalized metadata onto ``msg`` (mutates and returns it)."""
    msg[CHUNK_MESSAGE_KEY] = normalize_chunk_meta(raw)
    return msg


def pin_policy_of(msg: dict[str, Any] | None) -> str:
    return message_chunk_meta(msg)["pin_policy"]


def semantic_of(msg: dict[str, Any] | None) -> str:
    return message_chunk_meta(msg)["semantic"]


def section_id_of(msg: dict[str, Any] | None) -> str:
    return str(message_chunk_meta(msg).get("section_id") or "")


def strongest_pin_policy(messages: list[dict[str, Any]]) -> str:
    """Leaf pin policy: strongest (never > prefer > ephemeral) among messages."""
    best = CHUNK_PIN_POLICY_DEFAULT
    best_rank = _PIN_RANK.get(best, 2)
    for msg in messages:
        policy = pin_policy_of(msg)
        rank = _PIN_RANK.get(policy, 2)
        if rank < best_rank:
            best, best_rank = policy, rank
    return best


def is_never_pin(msg: dict[str, Any] | None) -> bool:
    return pin_policy_of(msg) == PIN_NEVER


def is_summary_message(msg: dict[str, Any] | None) -> bool:
    if not isinstance(msg, dict):
        return False
    if semantic_of(msg) == SEMANTIC_SUMMARY:
        return True
    content = str(msg.get("content") or "")
    return msg.get("role") == "system" and content.startswith(CHUNK_SUMMARY_HEADING)


def summary_message(text: str, *, child_count: int) -> dict[str, Any]:
    msg = {
        "role": "system",
        "content": f"{CHUNK_SUMMARY_HEADING} {child_count} chunk(s)]\n{text}",
    }
    attach_chunk_meta(
        msg,
        {"pin_policy": PIN_EPHEMERAL, "semantic": SEMANTIC_SUMMARY, "source": "archive"},
    )
    return msg


def strip_chunk_meta(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the extra message key so provider payloads stay OpenAI-shaped."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if CHUNK_MESSAGE_KEY in msg:
            out.append({key: value for key, value in msg.items() if key != CHUNK_MESSAGE_KEY})
        else:
            out.append(msg)
    return out


def pop_tool_chunk(result: Any) -> tuple[Any, dict[str, Any]]:
    """Split a tool return into (payload-without-chunk, normalized meta)."""
    if not isinstance(result, dict) or "chunk" not in result:
        return result, {}
    payload = {key: value for key, value in result.items() if key != "chunk"}
    return payload, normalize_chunk_meta(result.get("chunk"))


def stash_pending_chunk_meta(ctx: Any, call_id: str, meta: dict[str, Any]) -> None:
    if not meta:
        return
    queue: list[tuple[str, dict[str, Any]]] = getattr(ctx, "_chunk_meta_pending", None) or []
    ctx._chunk_meta_pending = queue
    queue.append((str(call_id or ""), meta))


def take_pending_chunk_meta(ctx: Any, call_id: str) -> dict[str, Any]:
    queue: list[tuple[str, dict[str, Any]]] = getattr(ctx, "_chunk_meta_pending", None) or []
    cid = str(call_id or "")
    for index, (key, meta) in enumerate(queue):
        if key == cid:
            queue.pop(index)
            return meta
    if cid:
        for index, (key, meta) in enumerate(queue):
            if not key:
                queue.pop(index)
                return meta
    return {}

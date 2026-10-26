#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pairing repair and history slicing for context plugins.

The kernel only asserts pairing (``assert_provider_payload``). Plugins repair
and slice here.
"""

from __future__ import annotations

from typing import Any

from mas.runtime.boundary.context.chunk_meta import strip_chunk_meta
from mas.runtime.boundary.context.provider_invariant import tool_call_pairs


def start_of_tool_group(messages: list[dict[str, Any]], index: int) -> int:
    """If ``messages[index]`` is a tool result, back up to its assistant."""
    if not messages:
        return 0
    i = max(0, min(index, len(messages) - 1))
    while i > 0 and messages[i].get("role") == "tool":
        i -= 1
    return i


def tool_group_len(messages: list[dict[str, Any]], index: int = 0) -> int:
    """Rows in the assistant tool-call group starting at ``index`` (ask + results)."""
    if not messages or index < 0 or index >= len(messages):
        return 0
    first = messages[index]
    if first.get("role") == "assistant" and first.get("tool_calls"):
        call_ids = {c.get("id") for c in first["tool_calls"] if c.get("id")}
        n = 1
        while (
            index + n < len(messages)
            and messages[index + n].get("role") == "tool"
            and messages[index + n].get("tool_call_id") in call_ids
        ):
            n += 1
        return n
    return 1


def split_user_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split on ``role=user``; each turn is that user plus everything until the next."""
    turns: list[list[dict[str, Any]]] = []
    i = 0
    n = len(messages)
    if i < n and messages[i].get("role") != "user":
        preamble: list[dict[str, Any]] = []
        while i < n and messages[i].get("role") != "user":
            preamble.append(messages[i])
            i += 1
        turns.append(preamble)
    while i < n:
        turn = [messages[i]]
        i += 1
        while i < n and messages[i].get("role") != "user":
            turn.append(messages[i])
            i += 1
        turns.append(turn)
    return turns


def sanitize_provider_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One result per tool call, immediately after the ask.

    Extra answers are dropped. A missing result becomes an empty string
    (valid protocol; a missing row is not). HITL used to record a result
    under the wrong id — rebound in declaration order.

    Call once on the fully assembled list (history + user + working memory).
    History slicers must not call this before working memory is concatenated.
    """
    if not messages:
        return []
    return strip_chunk_meta(_sanitize_completed(_repair_tool_call_bindings(messages)))


def _empty_tool_result(call_id: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": ""}


def _repair_tool_call_bindings(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    unresolved: set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            repaired.append(msg)
            unresolved = {str(c.get("id")) for c in msg["tool_calls"] if c.get("id")}
            continue

        if msg.get("role") == "tool" and unresolved:
            tool_id = str(msg.get("tool_call_id") or "")
            if tool_id not in unresolved:
                rebound = dict(msg)
                rebound["tool_call_id"] = sorted(unresolved)[0]
                repaired.append(rebound)
                unresolved.discard(rebound["tool_call_id"])
                continue
            unresolved.discard(tool_id)
            repaired.append(msg)
            continue

        if msg.get("role") != "tool":
            unresolved = set()
        repaired.append(msg)
    return repaired


def _sanitize_completed(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    declared = tool_call_pairs(messages)[0]
    rows = [
        msg for msg in messages if msg.get("role") != "tool" or str(msg.get("tool_call_id") or "") in declared
    ]
    out: list[dict[str, Any]] = []
    i = 0
    n = len(rows)
    while i < n:
        msg = rows[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            call_ids = [str(c.get("id")) for c in msg["tool_calls"] if c.get("id")]
            wanted = set(call_ids)
            j = i + 1
            tool_rows: list[dict[str, Any]] = []
            while j < n and rows[j].get("role") == "tool":
                tool_rows.append(rows[j])
                j += 1
            kept: list[dict[str, Any]] = []
            seen: set[str] = set()
            for tool in tool_rows:
                tid = str(tool.get("tool_call_id") or "")
                if tid in wanted and tid not in seen:
                    kept.append(tool)
                    seen.add(tid)
            trailing = j == n
            if not kept and not trailing:
                stripped = {k: v for k, v in msg.items() if k != "tool_calls"}
                if str(stripped.get("content") or "").strip():
                    out.append(stripped)
            else:
                out.append(msg)
                out.extend(kept)
                out.extend(_empty_tool_result(tid) for tid in call_ids if tid not in seen)
            i = j
            continue
        if msg.get("role") == "tool":
            i += 1
            continue
        out.append(msg)
        i += 1
    return out

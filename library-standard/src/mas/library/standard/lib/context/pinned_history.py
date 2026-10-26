#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fold/trim helpers driven by generic chunk metadata, not provider markup."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.context.payload import tool_group_len
from mas.runtime.boundary.context.chunk_meta import (
    SEMANTIC_INSTRUCTION,
    attach_chunk_meta,
    is_never_pin,
    message_chunk_meta,
    section_id_of,
    semantic_of,
)


def is_retained_instruction(msg: dict[str, Any]) -> bool:
    """A covering-view row that must not be trimmed (projected never-pin)."""
    if not is_never_pin(msg):
        return False
    if semantic_of(msg) != SEMANTIC_INSTRUCTION:
        return False
    return msg.get("role") in {"system", "tool"}


def _project_instruction(msg: dict[str, Any]) -> dict[str, Any]:
    projected = {"role": "system", "content": str(msg.get("content") or "")}
    attach_chunk_meta(projected, message_chunk_meta(msg))
    return projected


def _stub_tool(msg: dict[str, Any]) -> dict[str, Any]:
    section = section_id_of(msg) or "pinned"
    stubbed = {**msg, "content": f"[content retained verbatim: {section}]"}
    return stubbed


def split_pinned_prefix(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a prefix about to be folded into ``(rest, retained)``.

    ``retained`` is one system row per ``section_id`` (latest never-pin
    instruction wins). ``rest`` stubs those tool results so a summarizer
    never sees the body. Pairing stays intact.
    """
    found: dict[str, dict[str, Any]] = {}
    rest: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        size = tool_group_len(messages, index)
        group = messages[index : index + size]
        pinned = [msg for msg in group if is_never_pin(msg)]
        if not pinned:
            rest.extend(group)
            index += size
            continue
        for msg in pinned:
            section = section_id_of(msg) or f"pin:{index}"
            found[section] = _project_instruction(msg)
        for msg in group:
            if msg.get("role") == "tool" and is_never_pin(msg):
                rest.append(_stub_tool(msg))
            elif is_retained_instruction(msg) and msg.get("role") == "system":
                continue
            else:
                rest.append(msg)
        index += size
    return rest, list(found.values())


def split_pinned_tool_groups(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into ``(never-pin groups, rest)`` without breaking tool-call groups."""
    pinned: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        size = tool_group_len(messages, index)
        group = messages[index : index + size]
        if any(is_never_pin(msg) for msg in group):
            pinned.extend(group)
        else:
            rest.extend(group)
        index += size
    return pinned, rest


def drop_prefix_duplicates(
    messages: list[dict[str, Any]],
    section_ids: set[str],
) -> list[dict[str, Any]]:
    """Drop retained instruction rows already present in the system prefix."""
    if not section_ids:
        return list(messages)
    return [
        msg
        for msg in messages
        if not (
            is_retained_instruction(msg)
            and msg.get("role") == "system"
            and section_id_of(msg) in section_ids
        )
    ]



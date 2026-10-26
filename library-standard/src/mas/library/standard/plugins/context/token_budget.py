#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Assembly token trim — tool-group-aware (enabled via context_manager.params.trimmer)."""

from __future__ import annotations

import json
import logging
from typing import Any

from mas.library.standard.lib.context.payload import tool_group_len
from mas.library.standard.lib.context.pinned_history import (
    is_retained_instruction,
    split_pinned_tool_groups,
)

_log = logging.getLogger(__name__)


def _estimate_tokens_lists(*parts: list[dict[str, Any]]) -> int:
    char_total = 0
    msg_count = 0
    for msgs in parts:
        for m in msgs:
            msg_count += 1
            content = m.get("content", "")
            if isinstance(content, str):
                char_total += len(content)
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                char_total += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return char_total // 4 + msg_count * 4


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return _estimate_tokens_lists(messages)


def estimate_tool_tokens(tools: list[dict[str, Any]] | None) -> int:
    """Estimate the token cost of tool schemas sent outside ``messages[]``."""
    if not tools:
        return 0
    return len(json.dumps(tools, ensure_ascii=False, default=str)) // 4 + 4 * len(tools)


def _oldest_group_size(msgs: list[dict[str, Any]]) -> int:
    """How many leading rows are one assistant tool-call plus its results."""
    return tool_group_len(msgs, 0)


def _sync_pin_tail_after_assistant_drop(
    first: dict[str, Any],
    pinned_tail: list[dict[str, Any]],
) -> None:
    if first.get("role") != "assistant" or not first.get("tool_calls"):
        return
    call_ids = {str(c.get("id")) for c in first["tool_calls"] if c.get("id")}
    while pinned_tail and pinned_tail[0].get("role") == "tool":
        tid = str(pinned_tail[0].get("tool_call_id") or "")
        if tid in call_ids:
            pinned_tail.pop(0)
            call_ids.discard(tid)
        else:
            break


def trim_messages_to_budget(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    reserve_tokens: int = 0,
    pin_tail: list[dict[str, Any]] | None = None,
    fixed_tokens: int = 0,
) -> list[dict[str, Any]]:
    if max_tokens <= 0:
        return list(messages) + list(pin_tail or [])
    budget = max(0, max_tokens - max(0, reserve_tokens) - max(0, fixed_tokens))
    never_tail, pinned_tail = split_pinned_tool_groups(list(pin_tail or []))
    if _estimate_tokens_lists(messages, never_tail, pinned_tail) <= budget:
        return list(messages) + never_tail + pinned_tail

    head: list[dict[str, Any]] = []
    tail = list(messages)
    if tail and tail[0].get("role") == "system":
        head.append(tail.pop(0))
    total = _estimate_tokens_lists(head, tail, never_tail, pinned_tail)
    while tail and total > budget and len(tail) > 1:
        if is_retained_instruction(tail[0]):
            head.append(tail.pop(0))
            continue
        n = _oldest_group_size(tail)
        if n >= len(tail):
            break
        first = tail[0]
        del tail[:n]
        _sync_pin_tail_after_assistant_drop(first, pinned_tail)
        total = _estimate_tokens_lists(head, tail, never_tail, pinned_tail)

    while pinned_tail and total > budget:
        n = _oldest_group_size(pinned_tail)
        if n >= len(pinned_tail):
            break
        del pinned_tail[:n]
        total = _estimate_tokens_lists(head, tail, never_tail, pinned_tail)

    result: list[dict[str, Any]] = []
    result.extend(head)
    result.extend(tail)
    result.extend(never_tail)
    result.extend(pinned_tail)
    over = _estimate_tokens_lists(result)
    if over > budget:
        _log.warning(
            "token budget %d exceeded by the most recent pinned tool-call "
            "group alone (~%d tokens, %d pinned tail message(s)): raise the "
            "context budget or reduce what is injected into the prompt",
            budget,
            over,
            len(pinned_tail),
        )
    return result

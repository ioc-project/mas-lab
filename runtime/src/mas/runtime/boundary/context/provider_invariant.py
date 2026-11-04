#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Layer-1 protocol check: tool-call / tool-result pairing.

The kernel asserts this after the assembler plugin returns. Repair and
history slicing live in ``library-standard/`` (``lib.context.payload``).
"""

from __future__ import annotations

from typing import Any


class ProviderPayloadError(ValueError):
    """Assembled ``messages[]`` violate tool-call / tool-result pairing."""


def tool_call_pairs(messages: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return (declared tool_call ids, tool-result ids)."""
    declared = {str(c["id"]) for m in messages if m.get("tool_calls") for c in m["tool_calls"] if c.get("id")}
    returned = {str(m["tool_call_id"]) for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}
    return declared, returned


def consecutive_tool_result_count(messages: list[dict[str, Any]], assistant_index: int) -> int:
    """How many ``role=tool`` rows follow ``messages[assistant_index]``."""
    n = 0
    j = assistant_index + 1
    while j < len(messages) and messages[j].get("role") == "tool":
        n += 1
        j += 1
    return n


def assert_provider_payload(messages: list[dict[str, Any]]) -> None:
    """Sendable payload: each tool call has exactly one result, immediately after it.

    Raises :class:`ProviderPayloadError` (survives ``python -O``). Custom
    ``spec.assembler`` plugins must produce a paired payload.
    """
    declared, returned = tool_call_pairs(messages)
    orphan = returned - declared
    if orphan:
        raise ProviderPayloadError(f"orphan tool results: {orphan}")
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            n_use = len([c for c in msg["tool_calls"] if c.get("id")])
            n_res = consecutive_tool_result_count(messages, i)
            if n_res != n_use:
                raise ProviderPayloadError(
                    f"tool results {n_res} != tool calls {n_use} at messages[{i}]"
                )
        if msg.get("role") == "tool":
            prev = messages[i - 1] if i else None
            if prev is None:
                raise ProviderPayloadError("tool result at start of payload")
            prev_role = prev.get("role")
            if prev_role not in {"assistant", "tool"}:
                raise ProviderPayloadError(
                    f"tool result at messages[{i}] follows role={prev_role!r}"
                )
            if prev_role == "assistant" and not prev.get("tool_calls"):
                raise ProviderPayloadError(
                    f"tool result at messages[{i}] follows a text-only assistant"
                )

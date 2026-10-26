#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Context / working-memory observability — kernel path (LangGraph-style messages)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from mas.runtime.engine.llm_output_limits import estimate_prompt_tokens, estimate_tool_tokens


def _preview(text: str, *, limit: int = 120) -> str:
    blob = str(text or "").strip()
    if len(blob) <= limit:
        return blob
    return blob[: limit - 3] + "..."


def _token_estimate(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _message_source(msg: dict[str, Any], *, index: int, layer: str) -> str:
    role = str(msg.get("role") or "?")
    if role == "system":
        if str(msg.get("content") or "").startswith("[Conversation summary —"):
            return "conversation/summarized"
        return "context/system"
    if role == "tool":
        return f"{layer}/tool_result"
    if role == "assistant" and msg.get("tool_calls"):
        return f"{layer}/tool_call"
    if role == "assistant":
        return f"{layer}/assistant"
    if role == "user":
        return f"{layer}/user"
    return f"{layer}/{role}"


def segments_from_messages(
    messages: list[dict[str, Any]],
    *,
    layer: str = "assembled",
) -> list[dict[str, Any]]:
    """Build L4-compatible segment dicts from OpenAI-shaped messages."""
    segments: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        role = str(msg.get("role") or "?")
        content = msg.get("content") or ""
        if role == "assistant" and msg.get("tool_calls"):
            calls = msg.get("tool_calls") or []
            names = []
            for call in calls:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                names.append(str(fn.get("name") or "?"))
            content = f"tool_calls: {', '.join(names)}"
        segments.append(
            {
                "part_id": str(uuid.uuid4()),
                "role": role,
                "source": _message_source(msg, index=idx, layer=layer),
                "section_id": f"{layer}/{idx}/{role}",
                "index": idx,
                "tokens": _token_estimate(str(content)),
                "content_preview": _preview(str(content)),
                "tool_call_id": msg.get("tool_call_id") or "",
                "retained": True,
            }
        )
    return segments


def record_context_mutation(
    observability: Any | None,
    *,
    action: str,
    turn_index: int = 0,
    correlation_id: int = 0,
    role: str = "",
    call_id: str = "",
    content: str = "",
    committed_count: int = 0,
    wm_count: int = 0,
    op: str = "",
) -> None:
    """Log working-memory / turn-history mutations (state that affects the next LLM call).

    ``op`` (e.g. "LLM_CALL"/"TOOL_CALL"), when the mutation is caused by one
    specific engine-op result (see driver.py's two ``wm_append`` call sites),
    lets ``_resolve_transition_ids`` resolve a real ``parent_call_id`` via the
    same ``_interval_call_id`` mechanism that op's own ENGINE_IO/ENGINE_IO_RETURN
    pair uses. Turn/session-scoped mutations (turn_start, wm_clear, ...) have
    no single owning call and correctly leave this blank — "no parent" is the
    honest answer for those, not something to approximate.
    """
    if observability is None:
        return
    record = getattr(observability, "record_context_mutation", None)
    if not callable(record):
        return
    record(
        action=action,
        turn_index=turn_index,
        correlation_id=correlation_id,
        role=role,
        call_id=call_id,
        content_preview=_preview(content),
        committed_count=committed_count,
        wm_count=wm_count,
        op=op,
    )


def record_context_assembly(
    observability: Any | None,
    *,
    correlation_id: int,
    messages: list[dict[str, Any]],
    turn_index: int = 0,
    agent_id: str = "agent",
    tools: list[str] | None = None,
    tool_schemas: list[dict[str, Any]] | None = None,
    model: str = "",
    context_window: int | None = None,
    completion_reserve: int | None = None,
    context_parts: list[dict[str, Any]] | None = None,
    archive_view_id: str = "",
    archive_covering_ids: list[str] | None = None,
    system_prefix_hash: str = "",
    system_prefix_reused: bool = False,
) -> dict[str, Any]:
    """Log the exact messages[] snapshot sent to the LLM (pre-call).

    ``tools`` is the function names from the OpenAI ``tools`` array on that
    same request. ``None`` means the caller did not record them.
    """
    segments = segments_from_messages(messages)
    tool_tokens = estimate_tool_tokens(tool_schemas)
    estimated_prompt_tokens = estimate_prompt_tokens(messages, tool_schemas)
    breakdown: dict[str, int] = {"tool_definitions": tool_tokens}
    for segment in segments:
        source = str(segment.get("source") or "conversation")
        breakdown[source] = breakdown.get(source, 0) + int(segment.get("tokens") or 0)
    reserve = max(0, int(completion_reserve or 0))
    remaining = max(0, context_window - estimated_prompt_tokens - reserve) if context_window else None
    usage = {
        "captured_at": datetime.now(UTC).isoformat(),
        "model": str(model or ""),
        "context_window": context_window,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "completion_reserve": reserve,
        "estimated_remaining_tokens": remaining,
        "fill_ratio": (estimated_prompt_tokens / context_window) if context_window else None,
        "token_breakdown": breakdown,
        "context_parts": [
            {
                "source": str(part.get("source") or ""),
                "section_id": str(part.get("section_id") or ""),
                "role": str(part.get("role") or ""),
                "placement": str(part.get("placement") or ""),
                "tokens": max(0, int(part.get("tokens") or 0)),
                "pinned": bool(part.get("pinned", False)),
            }
            for part in (context_parts or [])
        ],
        "archive_view_id": str(archive_view_id or ""),
        "archive_covering_ids": list(archive_covering_ids or []),
        "system_prefix_hash": str(system_prefix_hash or ""),
        "system_prefix_reused": bool(system_prefix_reused),
    }
    if observability is None or not messages:
        return usage
    record = getattr(observability, "record_context_assembled", None)
    if not callable(record):
        return usage
    record(
        correlation_id=correlation_id,
        turn_index=turn_index,
        agent_id=agent_id,
        messages=messages,
        segments=segments,
        total_tokens=sum(s.get("tokens") or 0 for s in segments),
        tools=tools,
        context_usage=usage,
    )
    return usage


def record_engine_llm_return(
    observability: Any | None,
    *,
    correlation_id: int,
    text: str,
    next_step: str = "STOP",
) -> None:
    if observability is None:
        return
    record = getattr(observability, "record_engine_llm_return", None)
    if not callable(record):
        return
    record(correlation_id=correlation_id, text=text, next_step=next_step)

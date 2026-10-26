#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dispatch LLM ``messages[]`` assembly and turn-commit compact to ``spec.assembler``.

The kernel does not choose history/trim policy. It instantiates the registry
``assembler`` (default ``assembler``), asserts layer-1 pairing,
and records assembly telemetry. After each turn it asks the same plugin to
rewrite stored history to the context manager's recency cap.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

from mas.runtime.boundary.context.archive import ConversationChunkStore
from mas.runtime.boundary.context.assembly_cache import cached_assembler
from mas.runtime.boundary.context.provider_invariant import assert_provider_payload
from mas.runtime.boundary.context.telemetry import record_context_assembly


def _accepts_keyword(fn: Any, name: str) -> bool:
    """Keep optional runtime hints additive for custom assembler plugins."""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _archive_request_meta(ctx: Any, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Telemetry for the covering set used to assemble this LLM request."""
    store = getattr(ctx, "conversation_chunks", None)
    covering = list(store.covering_ids()) if isinstance(store, ConversationChunkStore) else []
    view = getattr(store, "view_id", "") if isinstance(store, ConversationChunkStore) else ""
    prefix = ""
    if messages and messages[0].get("role") == "system":
        prefix = str(messages[0].get("content") or "")
    prefix_hash = hashlib.sha256(prefix.encode()).hexdigest()[:16] if prefix else ""
    reused = bool(prefix_hash) and prefix_hash == getattr(ctx, "last_system_prefix_hash", None)
    try:
        ctx.last_archive_view_id = view
        ctx.last_system_prefix_hash = prefix_hash
        ctx.last_system_prefix_reused = reused
    except (AttributeError, TypeError):
        pass
    return {
        "archive_view_id": view,
        "archive_covering_ids": covering,
        "system_prefix_hash": prefix_hash,
        "system_prefix_reused": reused,
    }


def _openai_tool_names(tools: list[dict[str, Any]] | None) -> list[str] | None:
    """Function names from an OpenAI ``tools`` array. ``None`` if not recorded."""
    if tools is None:
        return None
    names: list[str] = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        name = str((fn or {}).get("name") or "") if isinstance(fn, dict) else ""
        if name:
            names.append(name)
    return names


def _assembly_key(
    correlation_id: int,
    messages: list[dict[str, Any]],
    tool_names: list[str] | None,
) -> tuple[Any, ...]:
    """Comparable identity of one assembly (correlation id, messages, tools)."""
    return (
        correlation_id,
        json.dumps(messages, sort_keys=True, default=str, separators=(",", ":")),
        tuple(tool_names or ()),
    )


def _remember_assembly(ctx: Any, key: tuple[Any, ...]) -> bool:
    """Remember *key* on ``ctx``. Return True if it is new (should be recorded).

    One LLM call is assembled up to three times; only the first report is kept.
    A later assembly with a different key is new and is still recorded.
    """
    if getattr(ctx, "_recorded_assembly_key", None) == key:
        return False
    try:
        ctx._recorded_assembly_key = key
    except (AttributeError, TypeError):
        pass
    return True


def assemble_llm_messages(
    ctx: Any,
    *,
    manifest: dict | None = None,
    correlation_id: int = 0,
    tools: list[dict[str, Any]] | None = None,
    resolved_model: str | None = None,
    context_window: int | None = None,
    completion_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI-shaped messages via the registered assembler plugin."""
    plugin = cached_assembler(ctx, manifest)
    assemble = getattr(plugin, "assemble_messages", None)
    if not callable(assemble):
        raise TypeError(
            f"{type(plugin).__name__} is not a context assembler "
            "(missing assemble_messages)"
        )
    assemble_kwargs: dict[str, Any] = {
        "manifest": manifest,
        "correlation_id": correlation_id,
        "tools": tools,
    }
    for name, value in (
        ("resolved_model", resolved_model),
        ("context_window", context_window),
        ("completion_tokens", completion_tokens),
    ):
        if _accepts_keyword(assemble, name):
            assemble_kwargs[name] = value
    messages = assemble(ctx, **assemble_kwargs)
    assert_provider_payload(messages)

    archive_meta = _archive_request_meta(ctx, messages)
    obs = getattr(ctx, "observability", None)
    cid = correlation_id or int(getattr(ctx, "_assembly_correlation_id", 0) or 0)
    tool_names = _openai_tool_names(tools)
    if obs is not None and messages and not _remember_assembly(
        ctx, _assembly_key(cid, messages, tool_names)
    ):
        obs = None
    context_usage = record_context_assembly(
        obs,
        correlation_id=cid,
        messages=messages,
        turn_index=int(getattr(ctx, "turn_index", 0) or 0),
        agent_id=str(getattr(ctx, "agent_id", "agent") or "agent"),
        tools=tool_names,
        tool_schemas=tools,
        model=resolved_model or "",
        context_window=context_window,
        completion_reserve=completion_tokens,
        context_parts=getattr(ctx, "context_usage_parts", None),
        archive_view_id=archive_meta["archive_view_id"],
        archive_covering_ids=archive_meta["archive_covering_ids"],
        system_prefix_hash=archive_meta["system_prefix_hash"],
        system_prefix_reused=archive_meta["system_prefix_reused"],
    )
    try:
        ctx.last_context_usage = context_usage
    except (AttributeError, TypeError):
        pass
    return messages


def compact_committed_history(ctx: Any, *, manifest: dict | None = None) -> None:
    """Ask the assembler plugin to rewrite stored history to its recency cap."""
    resolved = manifest if manifest is not None else getattr(ctx, "manifest", None)
    plugin = cached_assembler(ctx, resolved)
    compact = getattr(plugin, "compact_committed_history", None)
    if not callable(compact):
        return
    compact(ctx, manifest=resolved)


def llm_request_tools(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    _ = messages
    return tools or None


def llm_tool_choice(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
) -> str | None:
    _ = messages
    return "auto" if tools else None


def has_tool_results(messages: list[dict[str, Any]]) -> bool:
    return any(m.get("role") == "tool" for m in messages)


__all__ = [
    "assemble_llm_messages",
    "compact_committed_history",
    "has_tool_results",
    "llm_request_tools",
    "llm_tool_choice",
]

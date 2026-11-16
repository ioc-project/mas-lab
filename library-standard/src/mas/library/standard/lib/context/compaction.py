#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``spec.working_memory.compaction`` → ``spec.context_manager`` for compile/bootstrap."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mas.runtime.engine.protocol import CompactionSummarizeEngine

_STRATEGY_TO_CM_TYPE: dict[str, str] = {
    "keep_recent": "stack",
    "sliding_window": "sliding_window",
    "summarize": "summarising",
}

_STRATEGY_PARAM_KEYS: dict[str, tuple[str, ...]] = {
    "keep_recent": ("max_messages",),
    "sliding_window": ("window_size",),
    "summarize": ("summary_threshold", "keep_turns", "model"),
}


def context_manager_binding_from_compaction(compaction: dict[str, Any]) -> dict[str, Any]:
    """Translate one ``working_memory.compaction`` block into a context_manager binding."""
    strategy = str(compaction.get("strategy") or "keep_recent").strip()
    cm_type = _STRATEGY_TO_CM_TYPE.get(strategy)
    if cm_type is None:
        raise ValueError(
            f"unknown working_memory.compaction.strategy: {strategy!r} (expected one of {sorted(_STRATEGY_TO_CM_TYPE)})"
        )
    param_keys = _STRATEGY_PARAM_KEYS.get(strategy, ())
    params = {k: compaction[k] for k in param_keys if k in compaction}
    model = params.pop("model", None)
    if model:
        params["summarizer"] = {"type": "llm", "params": {"model": model}}
    return {"type": cm_type, "params": params}


def resolve_working_memory_context_manager(spec: dict[str, Any]) -> dict[str, Any] | None:
    """If compaction is set and context_manager is not, return the equivalent binding."""
    if spec.get("context_manager"):
        return None
    working_memory = spec.get("working_memory")
    if not isinstance(working_memory, dict):
        return None
    compaction = working_memory.get("compaction")
    if not isinstance(compaction, dict):
        return None
    return context_manager_binding_from_compaction(compaction)


def build_llm_summarize_fn(engine: CompactionSummarizeEngine) -> Callable[[list[dict[str, Any]]], Awaitable[str]]:
    """Return ``engine.asummarize_messages`` (test helper / escape hatch)."""
    if not isinstance(engine, CompactionSummarizeEngine):
        raise TypeError(f"{type(engine).__name__} does not implement CompactionSummarizeEngine")
    return engine.asummarize_messages


def apply_working_memory_compaction(spec: dict[str, Any], *, engine: Any = None) -> None:
    """Mutate ``spec``: alias ``working_memory.compaction`` → ``context_manager`` when omitted."""
    _ = engine
    binding = resolve_working_memory_context_manager(spec)
    if binding is not None:
        spec["context_manager"] = binding

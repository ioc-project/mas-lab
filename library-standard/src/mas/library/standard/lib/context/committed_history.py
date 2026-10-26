#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Assemble the model request from the archive covering set.

The archive always holds every leaf. The covering set is an internal id list
(summary nodes and/or verbatim leaves). ``project_messages`` / ``committed_messages``
are the chat rows sent to the model. When the next request would exceed the
window, older covering entries fold into a summary; under-budget runs leave
both stores untouched.
"""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.context.history_budget import (
    context_manager_history_budget_hint,
)
from mas.library.standard.lib.context.payload import split_user_turns
from mas.library.standard.lib.context.spec import configure_archive_store
from mas.runtime.boundary.context.archive import ConversationChunkStore
from mas.runtime.boundary.context.assembly_cache import cached_context_manager


def _turn_history_to_past(turn_history: list[tuple[str, str]]) -> list[dict[str, Any]]:
    past: list[dict[str, Any]] = []
    for user_q, assistant_a in turn_history:
        past.append({"role": "user", "content": user_q})
        if assistant_a.strip():
            past.append({"role": "assistant", "content": assistant_a})
    return past


def request_history(ctx: Any, *, manifest: dict | None = None) -> list[dict[str, Any]]:
    """Chat messages for the covering set (or committed fallback)."""
    store = getattr(ctx, "conversation_chunks", None)
    if isinstance(store, ConversationChunkStore):
        configure_archive_store(store, ctx, manifest)
        if store.order or store.summary_chunk_id:
            return store.project_messages()
    committed = list(getattr(ctx, "committed_messages", []) or [])
    if committed:
        return committed
    return _turn_history_to_past(list(getattr(ctx, "turn_history", []) or []))


def apply_request_view(
    ctx: Any,
    managed: list[dict[str, Any]],
    *,
    manifest: dict | None = None,
) -> None:
    """Align the covering set with assembled history; leaves stay stored."""
    store = getattr(ctx, "conversation_chunks", None)
    if isinstance(store, ConversationChunkStore):
        configure_archive_store(store, ctx, manifest)
        if store.order or store.summary_chunk_id:
            store.cover_with_managed(managed)

    if isinstance(getattr(ctx, "committed_messages", None), list):
        ctx.committed_messages[:] = list(managed)
    else:
        ctx.committed_messages = list(managed)

    history = getattr(ctx, "turn_history", None)
    if not isinstance(history, list):
        return
    n_managed_turns = len(split_user_turns(managed))
    if n_managed_turns == 0:
        ctx.turn_history = []
    elif len(history) > n_managed_turns:
        ctx.turn_history = history[-n_managed_turns:]


def compact_committed_history(ctx: Any, *, manifest: dict | None = None) -> None:
    """Fold the covering prefix when the next request would not fit.

    Leaves stay stored. Under-budget ``managed == past`` is a no-op, so a run
    that never hit the window keeps the same committed bytes as before.
    """
    resolved = manifest if manifest is not None else getattr(ctx, "manifest", None)
    past = request_history(ctx, manifest=resolved)
    if not past:
        return

    cm = cached_context_manager(ctx, resolved)
    budget = context_manager_history_budget_hint(resolved, fixed_context_tokens=0)
    managed = cm.manage_history(past, budget)
    if managed == past:
        return
    apply_request_view(ctx, managed, manifest=resolved)

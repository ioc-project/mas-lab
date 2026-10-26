#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session-scoped working memory — ``spec.working_memory.persistent`` backing store.

Keyed by ``(session_id, agent_id)``, this is what lets a delegated sub-agent's
committed turn history survive across two separate ``delegate_to_<agent_id>``
calls within the same mas-ctl session, instead of starting fresh each time —
without a second system prompt, since ``injected_context`` (the system prompt)
is always rebuilt from the manifest at materialize time; only the prior
user/assistant exchange (``AutoCtxAssembler.committed_messages``/
``turn_history``) is restored here.

Not to be confused with ``WorkingMemoryStore`` (``working_memory.py``) — that
is the L1 in-turn tool-call scratch buffer, cleared every turn. This registry
holds the cross-turn conversation buffer instead, scoped to one session.

In-memory and process-lifetime only, per the initial scope: one mas-ctl
process/run owns one registry. Cross-process/persisted-across-restart storage
is a follow-up (``spec.memory.persistence`` already reserves the schema shape
for it).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mas.runtime.boundary.context.conversation_chunks import ConversationChunkStore

if TYPE_CHECKING:
    from mas.runtime.driver.mocks import AutoCtxAssembler


def _shallow_maps(messages: Any) -> list[Any]:
    """Copy list + dict shells; share string payloads. The CoW grain for WM."""
    out: list[Any] = []
    for item in messages or []:
        out.append(dict(item) if isinstance(item, dict) else item)
    return out


@dataclass
class WorkingMemorySnapshot:
    turn_history: list[tuple[str, str]] = field(default_factory=list)
    committed_messages: list[dict[str, Any]] = field(default_factory=list)
    conversation_chunks: dict[str, Any] | None = None
    working_messages: list[dict[str, Any]] = field(default_factory=list)
    open_tool_call_id: str = ""
    synced_tool_result_cids: list[int] = field(default_factory=list)
    frozen: bool = False
    version: int = 0


@dataclass
class WorkingMemoryConfig:
    """Mirrors ``spec.working_memory`` on the manifest — attached to
    ``RuntimeInstance.working_memory`` by ``instantiate_runtime()``.

    Kept as a nested config object (not a flattened
    ``instance.working_memory_persistent`` bool) so the manifest field and
    the runtime attribute read the same way: ``working_memory.persistent``
    on both sides, one concept, not a renamed derivative of it.
    """

    persistent: bool = True


class WorkingMemoryRegistry:
    """In-memory ``(session_id, agent_id) -> WorkingMemorySnapshot`` store.

    Concurrent turns never share a key, so no lock is needed — but no method
    here may ``await``, or a compound get/modify/put would interleave.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], WorkingMemorySnapshot] = {}

    def get(self, session_id: str, agent_id: str) -> WorkingMemorySnapshot | None:
        if not session_id or not agent_id:
            return None
        return self._store.get((session_id, agent_id))

    def put(self, session_id: str, agent_id: str, snapshot: WorkingMemorySnapshot) -> None:
        if not session_id or not agent_id:
            return
        key = (session_id, agent_id)
        previous = self._store.get(key)
        if previous is not None and previous.frozen:
            snapshot.version = previous.version + 1
        elif previous is not None:
            snapshot.version = previous.version
        self._store[key] = snapshot

    def freeze_session(self, session_id: str) -> int:
        """O(1) per agent: mark live snapshots immutable. Returns max version."""
        max_version = 0
        for (stored_session_id, _agent_id), snapshot in self._store.items():
            if stored_session_id != session_id:
                continue
            snapshot.frozen = True
            max_version = max(max_version, snapshot.version)
        return max_version

    def wm_version(self, session_id: str) -> int:
        versions = [
            snap.version
            for (stored_session_id, _agent_id), snap in self._store.items()
            if stored_session_id == session_id
        ]
        return max(versions) if versions else 0

    def drop(self, session_id: str, agent_id: str) -> None:
        self._store.pop((session_id, agent_id), None)

    def clear_session(self, session_id: str) -> None:
        for key in [k for k in self._store if k[0] == session_id]:
            del self._store[key]

    def clone_for_fork(self, session_id: str, new_session_id: str) -> None:
        """Copy one session's snapshots into a distinct session namespace."""
        for (source_id, agent_id), snapshot in list(self._store.items()):
            if source_id == session_id:
                self.put(new_session_id, agent_id, deepcopy(snapshot))

    def export_session(self, session_id: str) -> list[dict[str, Any]]:
        """JSON-compatible working-memory state. Shallow CoW spine, not deepcopy.

        Message dicts are copied one level so a later append on the live
        list cannot alias the snapshot; string payloads are shared.
        ``persist()`` is what serializes. ``deepcopy`` here was the
        snapshot cost center.
        """
        return [
            {
                "agent_id": agent_id,
                "turn_history": list(snapshot.turn_history),
                "committed_messages": _shallow_maps(snapshot.committed_messages),
                "conversation_chunks": snapshot.conversation_chunks,
                "working_messages": _shallow_maps(snapshot.working_messages),
                "open_tool_call_id": snapshot.open_tool_call_id,
                "synced_tool_result_cids": list(snapshot.synced_tool_result_cids),
            }
            for (stored_session_id, agent_id), snapshot in sorted(self._store.items())
            if stored_session_id == session_id
        ]

    def restore_session(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        """Replace one session's snapshots from checkpoint data."""
        self.clear_session(session_id)
        for entry in entries:
            self.put(
                session_id,
                entry["agent_id"],
                WorkingMemorySnapshot(
                    turn_history=[tuple(turn) for turn in entry.get("turn_history", [])],
                    committed_messages=_shallow_maps(entry.get("committed_messages", [])),
                    conversation_chunks=entry.get("conversation_chunks"),
                    working_messages=_shallow_maps(entry.get("working_messages", [])),
                    open_tool_call_id=str(entry.get("open_tool_call_id") or ""),
                    synced_tool_result_cids=list(entry.get("synced_tool_result_cids", [])),
                ),
            )

    def clear(self) -> None:
        self._store.clear()


def snapshot_ctx(ctx: "AutoCtxAssembler") -> WorkingMemorySnapshot:
    """Capture the cross-turn conversation buffer off an ``AutoCtxAssembler``."""
    chunk_store = ctx.conversation_chunks
    chunks_dict = None
    if chunk_store.order or chunk_store.summary_chunk_id:
        portability = str(getattr(ctx, "checkpoint_portability", "self_contained") or "self_contained")
        chunks_dict = chunk_store.checkpoint_payload(portability)
    working_memory = getattr(ctx, "working_memory", None)
    return WorkingMemorySnapshot(
        turn_history=list(ctx.turn_history),
        committed_messages=_shallow_maps(ctx.committed_messages),
        conversation_chunks=chunks_dict,
        working_messages=_shallow_maps(getattr(working_memory, "messages", [])),
        open_tool_call_id=str(getattr(working_memory, "_open_tool_call_id", "")),
        synced_tool_result_cids=sorted(
            getattr(working_memory, "_synced_tool_result_cids", set())
        ),
    )


def restore_ctx(ctx: "AutoCtxAssembler", snapshot: WorkingMemorySnapshot) -> None:
    """Replace ``ctx``'s cross-turn conversation buffer with a saved snapshot."""
    ctx.turn_history = list(snapshot.turn_history)
    ctx.committed_messages = _shallow_maps(snapshot.committed_messages)
    ctx.conversation_chunks = ConversationChunkStore.from_dict(snapshot.conversation_chunks)
    working_memory = getattr(ctx, "working_memory", None)
    if working_memory is not None:
        working_memory.messages = _shallow_maps(snapshot.working_messages)
        working_memory._open_tool_call_id = snapshot.open_tool_call_id
        working_memory._synced_tool_result_cids = set(snapshot.synced_tool_result_cids)


def clear_ctx_working_memory(ctx: "AutoCtxAssembler") -> None:
    """Start ``ctx`` fresh for a non-persistent agent (system prompt untouched)."""
    ctx.turn_history = []
    ctx.committed_messages = []
    ctx.conversation_chunks = ConversationChunkStore()
    working_memory = getattr(ctx, "working_memory", None)
    if working_memory is not None:
        working_memory.clear()


def is_persistent(instance: Any) -> bool:
    """``instance.working_memory.persistent`` — mirrors ``spec.working_memory.
    persistent`` on the manifest (default true). See ``WorkingMemoryConfig``.

    One check, shared by every caller that drives a turn on ``instance``:
    the delegation dispatch (``make_workflow_send``) and a directly-driven
    session (``SessionController``) alike — ``persistent`` means the same
    thing regardless of whether the agent was reached via `delegate_to_*`
    or talked to directly.
    """
    return bool(getattr(getattr(instance, "working_memory", None), "persistent", True))


def sync_working_memory_in(
    instance: Any,
    *,
    memory_key: str,
    agent_id: str,
    registry: WorkingMemoryRegistry | None = None,
) -> None:
    """Before a turn: restore or clear this agent's cross-turn buffer.

    ``spec.working_memory.persistent`` (default true): true restores any
    prior snapshot for this ``(memory_key, agent_id)`` so the agent
    continues its previous exchange rather than starting fresh — no second
    system prompt, since ``injected_context`` isn't touched here, only
    ``committed_messages``/``turn_history``. false always starts fresh, even
    though ``instance`` (and its ``ctx``) is the same Python object reused
    across every turn.

    Also clears when persistent and NO snapshot exists for this key: the
    same ``instance``/``ctx`` object can serve more than one ``(memory_key,
    agent_id)`` bucket over its lifetime (e.g. two different context_ids, or
    a later session reusing it) — a genuinely new bucket must start empty,
    not silently inherit whatever a DIFFERENT bucket left sitting in ctx.
    """
    ctx = getattr(instance.driver, "ctx", None)
    if ctx is None:
        return
    if not is_persistent(instance):
        clear_ctx_working_memory(ctx)
        return
    owner = registry or get_working_memory_registry()
    snapshot = owner.get(memory_key, agent_id)
    if snapshot is not None:
        restore_ctx(ctx, snapshot)
    else:
        clear_ctx_working_memory(ctx)


def sync_working_memory_out(
    instance: Any,
    *,
    memory_key: str,
    agent_id: str,
    registry: WorkingMemoryRegistry | None = None,
) -> None:
    """After a turn: save this agent's cross-turn buffer for next time."""
    if not is_persistent(instance):
        return
    ctx = getattr(instance.driver, "ctx", None)
    if ctx is None:
        return
    owner = registry or get_working_memory_registry()
    owner.put(memory_key, agent_id, snapshot_ctx(ctx))


_REGISTRY = WorkingMemoryRegistry()


def get_working_memory_registry() -> WorkingMemoryRegistry:
    """Process-wide registry — scoped to the lifetime of one mas-ctl session/run."""
    return _REGISTRY


def reset_working_memory_registry() -> None:
    """Test/teardown hook — drop all sessions from the process-wide registry."""
    _REGISTRY.clear()

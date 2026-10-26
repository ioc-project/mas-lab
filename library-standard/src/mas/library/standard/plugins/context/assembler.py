#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ContextAssemblerPlugin — single-point assembly of the prompt from ContextParts.

Role in the pipeline
--------------------
All other plugins contribute ``ContextPart`` objects via ``on_collect_context()``.
This plugin is the *only* one responsible for transforming those parts into the
final ``messages[]`` list that reaches the LLM.

It hooks at TWO points:

1. ``on_collect_context()`` — *not overridden here* (assembler is not a
   contributor).

2. ``on_pre_llm_call(data)`` — (a) applies the ConversationStrategy to trim /
   compress conversation history, then (b) reads all ContextParts from the
   registry, orders them, optionally applies a ContextStrategy (budget
   eviction), and writes ``data["messages"]``.

   As a side-channel for the ``ObservabilityPlugin`` it also writes three keys
   into ``hook_data`` that are **consumed and removed** before the LLM call:

       ``_context_segments``  — list of {role, source, section_id, tokens, pinned, …}
       ``_evicted_parts``     — list of parts removed by the budget strategy
       ``_summarized_turns``  — int, turns compressed by ConversationStrategy

   These keys are **never** forwarded to the LLM call dispatcher.  They are the
   sole input used by ``ObservabilityPlugin`` to automatically derive and emit
   ``processing_type`` spans (memory_injection, context_compression, etc.).
   See ``docs/CONTEXT_SEGMENTATION.md`` §ProcessingCall Telemetry.

This replaces the previous pattern where every plugin mutated ``messages[]``
independently in its own ``on_pre_llm_call`` hook.

Context-window strategies (ContextStrategy)
--------------------------------------------
An optional ``ContextStrategy`` can be injected at construction to control
what happens when the total estimated token count exceeds a configured budget:

    ``MaxTokenStrategy``  — evicts non-pinned parts from lowest-priority first.
    ``ContextStrategy``   — no-op base class (default in development).

Conversation-history strategies (ConversationStrategy)
-------------------------------------------------------
An optional ``ConversationStrategy`` manages the *existing* user/assistant
turns in ``messages[]`` before context parts are injected:

    ``SlidingWindowConversation``  — keep only the last N user turns.
    ``SummarizingConversation``    — keep last N turns verbatim; compose a
                                     summarizer sub-plugin (``llm`` | ``drop``)
                                     for older history. The CM owns recency
                                     and hysteresis; the summarizer only
                                     produces summary text.
    ``ConversationStrategy``       — no-op base (default).

Ordering rules
--------------
The assembled system message concatenates all system-placement parts,
ordered by placement band (SYSTEM_HEADER → ... → SYSTEM_MEMORY) then by
``priority`` within each band (lower number = earlier).

Conversation history preservation
-----------------------------------
The *base* assembler never modifies historical user/assistant turns.
A ``ConversationStrategy`` may transform them before injection.
The assembler only injects:
  - A new/augmented ``system`` message at index 0.
  - Optional ``user_prepend`` / ``user_append`` around the last user turn.

A marker (``<!-- ctx-assembled -->`` in the system message) prevents double
injection of context parts when called twice in the same turn (idempotent).
The conversation-history transformation always runs (not gated by the marker).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mas.library.standard.lib.context.committed_history import (
    apply_request_view,
    request_history,
)
from mas.library.standard.lib.context.committed_history import (
    compact_committed_history as compact_request_view,
)
from mas.library.standard.lib.context.history_budget import (
    assembly_trimmer_params,
    context_manager_history_budget_hint,
)
from mas.library.standard.lib.context.payload import sanitize_provider_messages
from mas.library.standard.lib.context.pinned_history import drop_prefix_duplicates
from mas.library.standard.lib.context.working_memory import (
    bounded_working_memory_tail,
    working_memory_slice_limit,
)
from mas.library.standard.plugins.context.token_budget import (
    estimate_tokens,
    estimate_tool_tokens,
    trim_messages_to_budget,
)
from mas.runtime.boundary.context.assembly_cache import cached_context_manager
from mas.runtime.boundary.context.chunk_meta import PIN_EPHEMERAL, PIN_NEVER
from mas.runtime.boundary.context.working_memory import WorkingMemoryStore
from mas.runtime.contracts.base import BasePlugin
from mas.runtime.contracts.cm_factory import CMFactory
from mas.runtime.contracts.context_contract import (
    _SYSTEM_PLACEMENTS_ORDER,
    ContextPart,
    ContextPlacement,
)
from mas.runtime.contracts.context_manager_contract import ContextManagerContract

logger = logging.getLogger(__name__)

_ASSEMBLY_MARKER = "<!-- ctx-assembled -->"


# ---------------------------------------------------------------------------
# Eviction strategies
# ---------------------------------------------------------------------------


class ContextStrategy:
    """Base class for context-budget strategies.

    ``filter(parts, budget_tokens) -> List[ContextPart]``
    receives the full list (already sorted) and returns the list to render.
    Default: return unchanged.
    """

    def filter(self, parts: List[ContextPart], budget_tokens: int) -> List[ContextPart]:  # noqa: A003
        return parts


class MaxTokenStrategy(ContextStrategy):
    """Evict non-pinned parts, starting from the *lowest* priority, until the
    total estimated token count is within ``budget_tokens``.

    Pinned parts are never evicted.  If even all non-pinned parts are removed
    and budget is still exceeded, pinned parts are kept as-is (overflow logged).
    """

    def filter(self, parts: List[ContextPart], budget_tokens: int) -> List[ContextPart]:  # noqa: A003
        total = sum(p.token_estimate or 0 for p in parts)
        if total <= budget_tokens:
            return parts

        # Build an evictable list sorted by priority descending (drop high numbers first)
        evictable = sorted(
            [p for p in parts if not p.pinned],
            key=lambda p: (-p.priority, p.source),
        )
        kept = set(id(p) for p in parts if p.pinned)
        remaining_budget = budget_tokens - sum(p.token_estimate or 0 for p in parts if p.pinned)

        for part in evictable:
            if remaining_budget >= (part.token_estimate or 0):
                kept.add(id(part))
                remaining_budget -= part.token_estimate or 0
            else:
                logger.debug(
                    "ContextAssembler: evicted section_id=%s (tokens=%d) to stay within budget",
                    part.section_id,
                    part.token_estimate,
                )

        result = [p for p in parts if id(p) in kept]
        if total > budget_tokens and sum(p.token_estimate or 0 for p in result) > budget_tokens:
            logger.warning(
                "ContextAssembler: pinned parts alone exceed budget (%d > %d tokens)",
                sum(p.token_estimate or 0 for p in result),
                budget_tokens,
            )
        return result


# ---------------------------------------------------------------------------
# Context manager resolution (registry / CMFactory — not inline strategies)
# ---------------------------------------------------------------------------


class _NoOpContextManager(ContextManagerContract):
    """Keep full history unchanged."""

    def manage_history(
        self,
        past: List[Dict[str, str]],
        budget_tokens: int,
    ) -> List[Dict[str, str]]:
        return past


def inject_context_plugins(
    ctx: Any,
    system_parts: list[str],
    *,
    part_usage: list[dict[str, Any]] | None = None,
) -> None:
    """Append ContextPart contents from ``ctx.plugin_collection``, placement-ordered."""
    collection = getattr(ctx, "plugin_collection", None)
    if not collection:
        return

    raw_parts = collection.collect_results("collect_context")
    if not raw_parts:
        return

    placement_order = {pl: i for i, pl in enumerate(_SYSTEM_PLACEMENTS_ORDER)}

    def _sort_key(part: Any) -> tuple[int, int]:
        placement = getattr(part, "placement", ContextPlacement.SYSTEM_BODY)
        priority = getattr(part, "priority", 60)
        return (placement_order.get(placement, 99), priority)

    for part in sorted(
        (p for p in raw_parts if isinstance(p, ContextPart)),
        key=_sort_key,
    ):
        if str(part.content).strip():
            system_parts.append(str(part.content).strip())
            if part_usage is not None:
                part_usage.append(
                    {
                        "source": str(part.source),
                        "section_id": str(part.section_id),
                        "role": str(part.role),
                        "placement": str(getattr(part.placement, "value", part.placement)),
                        "tokens": int(part.token_estimate or 0),
                        "pinned": bool(part.pinned),
                        "pin_policy": PIN_NEVER if part.pinned else PIN_EPHEMERAL,
                    }
                )


# ---------------------------------------------------------------------------
# ContextAssemblerPlugin
# ---------------------------------------------------------------------------


class ContextAssemblerPlugin(BasePlugin):
    """Collects ContextParts from all registered ContextContract plugins and
    assembles the final ``messages[]`` list before each LLM call.

    Parameters
    ----------
    token_budget:
        Maximum estimated tokens for all injected context parts combined.
        None = no limit (ContextStrategy no-op).
    strategy:
        Eviction strategy for context *parts*.  Defaults to
        ``MaxTokenStrategy`` when ``token_budget`` is set, ``ContextStrategy``
        (no-op) otherwise.
    conversation_strategy:
        :class:`ContextManagerContract` instance for the legacy
        ``on_pre_llm_call`` path. Live ``assemble_messages`` always uses the
        cached context manager (with ``ctx.engine``). When omitted here,
        attach_agent instantiates from the agent manifest.
    always_reassemble:
        If True, re-assemble context parts on every call even if the marker is
        present in the system message.  Default: False (idempotent within a
        turn).  Note: conversation strategy always runs.
    """

    def __init__(
        self,
        token_budget: Optional[int] = None,
        strategy: Optional[ContextStrategy] = None,
        conversation_strategy: Optional[ContextManagerContract] = None,
        manifest: Optional[Dict[str, Any]] = None,
        always_reassemble: bool = False,
        emit_segments: bool = True,
    ) -> None:
        super().__init__()
        self._token_budget = token_budget
        self._always_reassemble = always_reassemble
        # When True: the assembled segment metadata is stored in
        # hook_data["_context_segments"] for the ObservabilityPlugin to emit
        # as a ``context_assembled`` JSONL event (with full provenance).
        # The LLM still receives plain flat text regardless of this setting.
        # Set to False to degrade to pre-segmentation behaviour (no segment event).
        self._emit_segments = emit_segments
        if strategy is not None:
            self._strategy = strategy
        elif token_budget is not None:
            self._strategy = MaxTokenStrategy()
        else:
            self._strategy = ContextStrategy()
        self._manifest = manifest
        if conversation_strategy is not None:
            self._conv_strategy = conversation_strategy
        else:
            self._conv_strategy = _NoOpContextManager()

    # ------------------------------------------------------------------
    # Hook
    # ------------------------------------------------------------------

    def attach_agent(self, agent: Any) -> None:
        super().attach_agent(agent)
        self.registry = getattr(agent, "registry", None)
        if isinstance(self._conv_strategy, _NoOpContextManager):
            manifest = getattr(agent, "manifest", None) or getattr(agent, "config", {})
            if isinstance(manifest, dict):
                engine = getattr(agent, "engine", None)
                if engine is None:
                    driver = getattr(agent, "driver", None)
                    engine = getattr(driver, "engine", None)
                self._conv_strategy = CMFactory.create(manifest=manifest, engine=engine)

    def assemble_messages(
        self,
        ctx: Any,
        *,
        manifest: Optional[Dict[str, Any]] = None,
        correlation_id: int = 0,
        tools: Optional[List[Dict[str, Any]]] = None,
        resolved_model: str | None = None,
        context_window: int | None = None,
        completion_tokens: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Engine path: system → managed history → user → WM / token trim / pairing.

        ``correlation_id`` and ``tools`` are accepted for the kernel dispatcher
        signature; telemetry is recorded in the kernel after this returns.
        """
        _ = correlation_id
        manifest = manifest if manifest is not None else self._manifest

        messages: List[Dict[str, Any]] = []
        system_parts: List[str] = []
        part_usage: list[dict[str, Any]] = []
        try:
            ctx.context_usage_parts = part_usage
        except (AttributeError, TypeError):
            pass
        for line in getattr(ctx, "injected_context", []) or []:
            if str(line).strip():
                system_parts.append(str(line).strip())
        for key, content in getattr(ctx, "memory_seeds", []) or []:
            system_parts.append(f"[memory:{key}] {content}")
        inject_context_plugins(ctx, system_parts, part_usage=part_usage)
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        past = request_history(ctx, manifest=manifest)

        last_user_text = str(getattr(ctx, "last_user_text", "") or "")
        store = getattr(ctx, "working_memory", None)
        wm_messages: List[Dict[str, Any]] = []
        if isinstance(store, WorkingMemoryStore) and store.messages:
            wm_messages = bounded_working_memory_tail(
                store.messages, working_memory_slice_limit(manifest)
            )

        fixed_context_tokens = (
            estimate_tokens(messages)
            + estimate_tokens([{"role": "user", "content": last_user_text}])
            + estimate_tokens(wm_messages)
            + estimate_tool_tokens(tools)
        )
        cm = cached_context_manager(ctx, manifest)
        managed = cm.manage_history(
            past,
            context_manager_history_budget_hint(
                manifest,
                resolved_model=resolved_model,
                context_window_override=context_window,
                completion_tokens_override=completion_tokens,
                fixed_context_tokens=fixed_context_tokens,
            ),
        )
        if managed != past:
            apply_request_view(ctx, managed, manifest=manifest)
        pinned_ids = {
            str(part.get("section_id") or "")
            for part in part_usage
            if part.get("section_id")
            and (part.get("pin_policy") == PIN_NEVER or part.get("pinned"))
        }
        messages.extend(drop_prefix_duplicates(managed, pinned_ids))
        if last_user_text:
            messages.append({"role": "user", "content": last_user_text})

        if not messages and not wm_messages:
            messages.append({"role": "user", "content": "Hello"})

        trimmer = assembly_trimmer_params(
            manifest,
            resolved_model=resolved_model,
            context_window_override=context_window,
            completion_tokens_override=completion_tokens,
        )
        if trimmer is not None:
            max_tokens, reserve = trimmer
            messages = trim_messages_to_budget(
                messages,
                max_tokens=max_tokens,
                reserve_tokens=reserve,
                pin_tail=wm_messages,
                fixed_tokens=estimate_tool_tokens(tools),
            )
        else:
            messages = messages + wm_messages

        return sanitize_provider_messages(messages)

    def compact_committed_history(self, ctx: Any, *, manifest: Optional[Dict[str, Any]] = None) -> None:
        """Fold the covering set after a turn commit; next request is re-assembled."""
        compact_request_view(ctx, manifest=manifest if manifest is not None else self._manifest)

    def on_pre_llm_call(self, hook_data: Dict[str, Any], **_: Any) -> Dict[str, Any]:
        """Called by PluginRegistry before every LLM call.

        Step 1 — Conversation history management (always runs):
            Applies ``ConversationStrategy`` to past user/assistant turns,
            trimming or compressing them as configured.

        Step 2 — Context part injection (idempotent within a turn):
            1. Collects ContextParts from all registered ContextContract plugins
               via the registry's ``collect_results("collect_context")``.
            2. Orders and optionally trims them.
            3. Injects into ``hook_data["messages"]``.
        """
        messages: List[Dict[str, str]] = hook_data.get("messages", [])

        # Step 1: conversation history management — always applied.
        # _apply_conversation_strategy writes _last_summarized_turns on self.
        messages = self._apply_conversation_strategy(messages)
        hook_data["messages"] = messages
        summarized = getattr(self, "_last_summarized_turns", 0)
        if summarized:
            hook_data["_summarized_turns"] = summarized
        # C5: pass compaction evidence metadata to ObservabilityPlugin
        compaction_meta = getattr(self, "_last_compaction_metadata", None)
        if compaction_meta is not None:
            hook_data["_compaction_metadata"] = compaction_meta

        # Step 2: context part injection — idempotency check
        if not self._always_reassemble:
            sys_msg = next((m for m in messages if m.get("role") == "system"), None)
            if sys_msg and _ASSEMBLY_MARKER in sys_msg.get("content", ""):
                return hook_data

        registry = getattr(self, "registry", None)
        if registry is None:
            # No registry available (unit test), nothing to collect
            return hook_data

        # Collect all parts from ContextContract plugins
        raw = registry.collect_results("collect_context")
        if raw and isinstance(raw[0], ContextPart):
            all_parts_raw = list(raw)
        else:
            all_parts_raw = [p for lst in raw if lst for p in lst]

        if not all_parts_raw:
            return hook_data

        # Apply strategy (budget trimming / eviction).
        # Track which parts were evicted so ObservabilityPlugin can emit a
        # context_compression ProcessingCall when the budget was exceeded.
        all_parts = all_parts_raw
        if self._token_budget:
            all_parts = self._strategy.filter(all_parts_raw, self._token_budget)
            if self._emit_segments and len(all_parts) < len(all_parts_raw):
                kept_ids = {id(p) for p in all_parts}
                hook_data["_evicted_parts"] = [
                    {
                        "section_id": p.section_id,
                        "source": p.source,
                        "role": p.role or p.source,
                        "tokens": p.token_estimate,
                        "mechanism": getattr(p.provenance, "mechanism", "collect_context"),
                        "trigger": getattr(p.provenance, "trigger", "runtime_default"),
                        "source_type": getattr(p.provenance, "source_type", "unknown"),
                        "sensitivity": getattr(p.provenance, "sensitivity", ""),
                    }
                    for p in all_parts_raw
                    if id(p) not in kept_ids
                ]

        # Sort: placement order first, then priority within placement
        placement_order = {pl: i for i, pl in enumerate(_SYSTEM_PLACEMENTS_ORDER)}
        system_parts = [p for p in all_parts if p.placement in placement_order]
        system_parts.sort(key=lambda p: (placement_order.get(p.placement, 99), p.priority))

        user_prepend_parts = sorted(
            [p for p in all_parts if p.placement == ContextPlacement.USER_PREPEND],
            key=lambda p: p.priority,
        )
        user_append_parts = sorted(
            [p for p in all_parts if p.placement == ContextPlacement.USER_APPEND],
            key=lambda p: p.priority,
        )

        ordered_all = system_parts + user_prepend_parts + user_append_parts
        assembly_payload = {
            "parts": ordered_all,
            "messages": list(messages),
            "budget_tokens": self._token_budget,
            "summarized_turns": summarized,
            "evicted_parts": hook_data.get("_evicted_parts", []),
        }
        assembly_payload = registry.execute_hooks(
            "pre_context_assembly",
            assembly_payload,
            agent_id=getattr(self, "agent_id", None),
        )
        if assembly_payload:
            ordered_all = assembly_payload.get("parts", ordered_all)
            messages = assembly_payload.get("messages", messages)

        system_parts = [p for p in ordered_all if p.placement in placement_order]
        user_prepend_parts = [p for p in ordered_all if p.placement == ContextPlacement.USER_PREPEND]
        user_append_parts = [p for p in ordered_all if p.placement == ContextPlacement.USER_APPEND]

        # Segment map — stored in hook_data for the ObservabilityPlugin to emit
        # as a ``context_assembled`` JSONL event.  The LLM receives flat text.
        if self._emit_segments:
            hook_data["_context_segments"] = [p.to_observability_dict() for p in ordered_all]

        # L4: emit per-part context_part_contributed events for provenance tracking.
        # This enables token-level attribution (Φ₄) — each part is individually
        # traceable in the audit trail with its part_id, source, and provenance.
        recorder = getattr(getattr(self, "agent", None), "recorder", None)
        if recorder is not None and self._emit_segments:
            import time as _time

            _ts = _time.time()
            _agent_id = getattr(self, "agent_id", "unknown")
            for part in ordered_all:
                prov = part.provenance
                _evt = {
                    "kind": "context_part_contributed",
                    "agent_id": _agent_id,
                    "timestamp": _ts,
                    "part_id": part.part_id,
                    "source": part.source,
                    "section_id": part.section_id,
                    "mechanism": getattr(prov, "mechanism", "collect_context"),
                    "cause": getattr(prov, "trigger", "runtime_default"),
                    "cause_type": getattr(prov, "source_type", "unknown"),
                    "token_estimate": part.token_estimate or 0,
                    "retained": True,
                }
                _sens = getattr(prov, "sensitivity", "")
                if _sens:
                    _evt["sensitivity"] = _sens
                recorder.emit(_evt)
            for evicted in hook_data.get("_evicted_parts", []):
                _evt = {
                    "kind": "context_part_contributed",
                    "agent_id": _agent_id,
                    "timestamp": _ts,
                    "source": evicted.get("source", ""),
                    "section_id": evicted.get("section_id", ""),
                    "mechanism": evicted.get("mechanism", "collect_context"),
                    "cause": evicted.get("trigger", "runtime_default"),
                    "cause_type": evicted.get("source_type", "unknown"),
                    "token_estimate": evicted.get("tokens", 0),
                    "retained": False,
                    "eviction_reason": "budget_exceeded",
                }
                _sens = evicted.get("sensitivity", "")
                if _sens:
                    _evt["sensitivity"] = _sens
                recorder.emit(_evt)

        # Build / augment system message
        messages = self._inject_system(messages, system_parts)

        # Inject around the last user turn
        if user_prepend_parts or user_append_parts:
            messages = self._inject_user(messages, user_prepend_parts, user_append_parts)

        hook_data["messages"] = messages
        registry.execute_hooks(
            "post_context_assembly",
            {
                "parts": ordered_all,
                "segments": hook_data.get("_context_segments", []),
                "messages": messages,
                "budget_tokens": self._token_budget,
            },
            agent_id=getattr(self, "agent_id", None),
        )
        return hook_data

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _inject_system(
        self,
        messages: List[Dict[str, str]],
        system_parts: List[ContextPart],
    ) -> List[Dict[str, str]]:
        """Merge system parts into the system message (create one if absent)."""
        if not system_parts:
            return messages

        body = "\n\n".join(p.content for p in system_parts)
        block = f"{_ASSEMBLY_MARKER}\n\n{body}"

        sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), None)
        if sys_idx is not None:
            existing = messages[sys_idx].get("content", "")
            # Handle multimodal content: extract text for concatenation
            if isinstance(existing, list):
                # Content is a multimodal array — prepend text block to the list
                from mas.runtime.contracts.content_types import ContentPart

                new_parts = [ContentPart.text_part(block)] + list(existing)
                messages[sys_idx] = {
                    "role": "system",
                    "content": [p.to_dict() if isinstance(p, ContentPart) else p for p in new_parts],
                }
            else:
                messages[sys_idx] = {"role": "system", "content": existing.rstrip() + "\n\n" + block}
        else:
            messages = [{"role": "system", "content": block}] + messages

        return messages

    def _inject_user(
        self,
        messages: List[Dict[str, str]],
        prepend_parts: List[ContextPart],
        append_parts: List[ContextPart],
    ) -> List[Dict[str, str]]:
        """Prepend / append content around the last user message."""
        # Find last user message
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return messages

        current = messages[last_user_idx].get("content", "")

        # Handle multimodal content — use merge_content from content_types
        if isinstance(current, list):
            from mas.runtime.contracts.content_types import ContentPart as CP

            if prepend_parts:
                prefix_text = "\n\n".join(p.content for p in prepend_parts)
                new_parts = [CP.text_part(prefix_text)] + list(current)
                current = [p.to_dict() if isinstance(p, CP) else p for p in new_parts]
            if append_parts:
                suffix_text = "\n\n".join(p.content for p in append_parts)
                if isinstance(current, list):
                    current = list(current) + [{"type": "text", "text": suffix_text}]
                else:
                    current = current + "\n\n" + suffix_text
        else:
            if prepend_parts:
                prefix = "\n\n".join(p.content for p in prepend_parts) + "\n\n"
                current = prefix + current
            if append_parts:
                suffix = "\n\n" + "\n\n".join(p.content for p in append_parts)
                current = current + suffix
        messages[last_user_idx] = {"role": "user", "content": current}
        return messages

    # ------------------------------------------------------------------
    # Conversation-history helper
    # ------------------------------------------------------------------

    def _apply_conversation_strategy(
        self,
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """Apply the ConversationStrategy to past user/assistant turns.

        Splits ``messages`` into three groups:
        - system messages (preserved as-is, at index 0 if present)
        - past user/assistant turns (all turns before the last user message)
        - current user turn (the last user message and anything after it)

        The strategy is applied only to *past* turns.  The current user turn
        and system messages are never passed to it.
        """
        # No strategy registered or default no-op → skip the split overhead
        if isinstance(self._conv_strategy, _NoOpContextManager):
            return messages  # no-op, fast path

        system_msgs = [m for m in messages if m.get("role") == "system"]
        turn_msgs = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]

        # Find the last user message — that's the current (live) turn
        last_user_idx: Optional[int] = None
        for i in range(len(turn_msgs) - 1, -1, -1):
            if turn_msgs[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None or last_user_idx == 0:
            # Zero or one user message → nothing to manage
            return messages

        past = turn_msgs[:last_user_idx]
        current_and_after = turn_msgs[last_user_idx:]

        managed_past = self._conv_strategy.manage_history(past, self._token_budget or 0)
        # Detect summarisation: if managed_past is shorter than past, turns
        # were either removed (SlidingWindowConversation) or compressed into a
        # summary block (SummarizingConversation).  Store on self so
        # on_pre_llm_call can pass _summarized_turns to ObservabilityPlugin.
        self._last_summarized_turns = max(0, len(past) - len(managed_past))
        # C5: expose compaction evidence metadata if the strategy tracks it
        self._last_compaction_metadata = getattr(self._conv_strategy, "last_compaction_metadata", None)

        return sanitize_provider_messages(system_msgs + managed_past + current_and_after)

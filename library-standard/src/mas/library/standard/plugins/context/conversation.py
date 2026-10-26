#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Conversation-history strategies — ContextManagerContract plugins.

Each context manager **is** a plugin with strategy code (not an empty shell):

- ``StackConversation`` — optional ``max_messages`` cap, tool-group aligned.
- ``SlidingWindowConversation`` — keep the last N user turns; drop older.
- ``SummarizingConversation`` — keep last ``keep_turns`` verbatim, hysteresis
  cache, then call a **summarizer sub-plugin** (registry type ``summarizer``:
  ``llm`` or ``drop``) on older turns. The CM owns recency/cache; the
  summarizer only produces summary text (or ``None`` to drop).

``manage_history`` bounds the LLM view. Turn commit persists that same
recency cap onto the stored log.

All three keep ``pin_policy: never`` instruction chunks out of the folded
prefix: each section's latest body stays as a ``system`` row and is never
summarized or dropped.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

from mas.library.standard.lib.context.payload import split_user_turns, start_of_tool_group
from mas.library.standard.lib.context.pinned_history import split_pinned_prefix
from mas.runtime.contracts.context_manager_contract import ContextManagerContract
from mas.runtime.spec.defaults import DEFAULT_HYSTERESIS_RATIO, DEFAULT_KEEP_TURNS

_log = logging.getLogger(__name__)

# Matches spec.context_manager.params.keep_turns / hysteresis_ratio schema defaults.
_DEFAULT_KEEP_TURNS = DEFAULT_KEEP_TURNS
_DEFAULT_HYSTERESIS_RATIO = DEFAULT_HYSTERESIS_RATIO


def _flatten(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [msg for turn in turns for msg in turn]


class StackConversation(ContextManagerContract):
    """Keep full history, optionally capped to last ``max_messages`` messages.

    If the cut would land on a tool result, the slice starts at that result's
    assistant instead, so the ask and answers stay together.
    """

    def __init__(self, max_messages: int | None = None) -> None:
        if max_messages is not None and max_messages < 1:
            raise ValueError(f"max_messages must be >= 1, got {max_messages}")
        self.max_messages = max_messages

    def manage_history(
        self,
        past: list[dict[str, Any]],
        budget_tokens: int,
    ) -> list[dict[str, Any]]:
        _ = budget_tokens
        if not past or self.max_messages is None or len(past) <= self.max_messages:
            return list(past)
        start = start_of_tool_group(past, len(past) - self.max_messages)
        evicted = start
        if evicted:
            _log.debug(
                "StackConversation: evicted %d message(s), keeping last %d (tool-group aligned)",
                evicted,
                len(past) - start,
            )
        _, pinned = split_pinned_prefix(list(past[:start]))
        return pinned + list(past[start:])


class SlidingWindowConversation(ContextManagerContract):
    """Keep the last ``keep_turns`` user turns verbatim; drop older turns."""

    def __init__(
        self,
        max_turns: int = _DEFAULT_KEEP_TURNS,
        window_size: int | None = None,
        keep_turns: int | None = None,
    ) -> None:
        if keep_turns is not None:
            max_turns = int(keep_turns)
        elif window_size is not None:
            max_turns = int(window_size)
        self.max_turns = max(1, int(max_turns))
        self.keep_turns = self.max_turns

    def manage_history(
        self,
        past: list[dict[str, Any]],
        budget_tokens: int,
    ) -> list[dict[str, Any]]:
        _ = budget_tokens
        if not past:
            return []
        turns = split_user_turns(past)
        if len(turns) <= self.max_turns:
            return list(past)
        kept = turns[-self.max_turns :]
        _log.debug(
            "SlidingWindowConversation: evicted %d turn(s), keeping last %d",
            len(turns) - self.max_turns,
            self.max_turns,
        )
        _, pinned = split_pinned_prefix(_flatten(turns[: -self.max_turns]))
        return pinned + _flatten(kept)


class SummarizingConversation(ContextManagerContract):
    """Keep recent user turns verbatim; summarize only older history.

    After a compaction, the cached summary is reused while new turns append
    verbatim, until the managed payload exceeds ``budget * (1 + hysteresis_ratio)``.
    Turn commit writes the bounded view back to the stored log.
    """

    def __init__(
        self,
        summary_threshold: int = 0,
        keep_turns: int = _DEFAULT_KEEP_TURNS,
        hysteresis_ratio: float = _DEFAULT_HYSTERESIS_RATIO,
        summarize_fn: Callable[[list[dict[str, Any]]], str] | None = None,
    ) -> None:
        self.summary_threshold = max(0, int(summary_threshold))
        self.keep_turns = max(1, int(keep_turns))
        self.hysteresis_ratio = min(1.0, max(0.0, float(hysteresis_ratio)))
        self._summarize_fn = summarize_fn
        self._summarizer: Any | None = None
        self.last_compaction_metadata: dict[str, Any] | None = None
        self._cached_summary: str | None = None
        self._cached_n_compressed: int = 0
        self._cached_prefix_fp: str | None = None
        self._cached_prefix_ids: tuple[int, int, int] | None = None
        self._cached_skills: list[dict[str, Any]] = []

    def bind_summarizer(self, summarizer: Any) -> None:
        """Attach a summarizer plugin, a ``summarize`` callable, or ``None`` (drop)."""
        if summarizer is None:
            self._summarizer = None
            self._summarize_fn = None
            return
        if callable(summarizer) and not hasattr(summarizer, "summarize"):
            self._summarizer = None
            self._summarize_fn = summarizer
            return
        fn = getattr(summarizer, "summarize", None)
        self._summarizer = summarizer
        self._summarize_fn = fn if callable(fn) else None

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content)
        return total // 4 + len(messages) * 4

    @staticmethod
    def _prefix_ids(turns: list[list[dict[str, Any]]], n: int) -> tuple[int, int, int] | None:
        """Object identity of the prefix endpoints (append-only live path)."""
        if n <= 0 or n > len(turns) or not turns[0] or not turns[n - 1]:
            return None
        return (n, id(turns[0][0]), id(turns[n - 1][-1]))

    @staticmethod
    def _fingerprint(turns: list[list[dict[str, Any]]]) -> str:
        """Content identity of a turn prefix — used when message objects were copied."""
        digest = hashlib.sha256()
        for turn in turns:
            for msg in turn:
                digest.update(str(msg.get("role") or "").encode())
                digest.update(b"\x1f")
                content = msg.get("content", "")
                digest.update(content.encode() if isinstance(content, str) else repr(content).encode())
                digest.update(b"\x1e")
            digest.update(b"\x1d")
        return digest.hexdigest()

    def _remember_prefix(self, turns: list[list[dict[str, Any]]], n: int) -> None:
        self._cached_n_compressed = n
        self._cached_prefix_ids = self._prefix_ids(turns, n)
        self._cached_prefix_fp = self._fingerprint(turns[:n])

    def _prefix_unchanged(self, turns: list[list[dict[str, Any]]], n: int) -> bool:
        mark = self._prefix_ids(turns, n)
        if mark is not None and mark == self._cached_prefix_ids:
            return True
        if self._cached_prefix_fp is None or self._fingerprint(turns[:n]) != self._cached_prefix_fp:
            return False
        self._cached_prefix_ids = mark
        return True

    def _clear_cache(self) -> None:
        self._cached_summary = None
        self._cached_n_compressed = 0
        self._cached_prefix_fp = None
        self._cached_prefix_ids = None
        self._cached_skills = []

    def _budget(self, budget_tokens: int) -> int:
        return budget_tokens if budget_tokens > 0 else self.summary_threshold

    def _retrigger_ceiling(self, high: int) -> int:
        return max(high, int(high * (1.0 + self.hysteresis_ratio)))

    def _summary_block(self, n_compressed: int, text: str) -> dict[str, Any]:
        return {
            "role": "system",
            "content": (
                f"[Conversation summary — {n_compressed} earlier exchange(s)]\n"
                f"{text}"
            ),
        }

    def _cached_view(self, turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
        n = self._cached_n_compressed
        if n <= 0 or n >= len(turns):
            return None
        if not self._prefix_unchanged(turns, n):
            return None
        suffix = self._cached_skills + _flatten(turns[n:])
        if self._cached_summary is None:
            return suffix
        return [self._summary_block(n, self._cached_summary)] + suffix

    def manage_history(
        self,
        past: list[dict[str, Any]],
        budget_tokens: int,
    ) -> list[dict[str, Any]]:
        if not past:
            self._clear_cache()
            return []

        high = self._budget(budget_tokens)
        raw_tokens = self._estimate_tokens(past)
        if high <= 0 or raw_tokens <= high:
            self._clear_cache()
            return list(past)

        turns = split_user_turns(past)
        keep = self.keep_turns
        if self._cached_n_compressed >= len(turns):
            self._clear_cache()

        cached = self._cached_view(turns)
        if cached is not None:
            managed_tokens = self._estimate_tokens(cached)
            if managed_tokens <= self._retrigger_ceiling(high):
                self.last_compaction_metadata = {
                    "reused": True,
                    "compressed_exchanges": self._cached_n_compressed,
                    "kept_exchanges": len(turns) - self._cached_n_compressed,
                }
                return cached

        if len(turns) <= keep:
            return list(past)

        to_compress = turns[:-keep]
        verbatim = turns[-keep:]
        to_compress_msgs, pinned_msgs = split_pinned_prefix(_flatten(to_compress))
        verbatim_msgs = pinned_msgs + _flatten(verbatim)
        n_compressed = len(to_compress)
        self._cached_skills = pinned_msgs

        model = getattr(self._summarizer, "model", None)
        model_source = getattr(self._summarizer, "model_source", None)

        if self._summarize_fn is None:
            self._cached_summary = None
            self._remember_prefix(to_compress, n_compressed)
            self.last_compaction_metadata = {
                "compressed_exchanges": 0,
                "dropped_exchanges": n_compressed,
                "kept_exchanges": len(verbatim),
                "estimated_tokens": raw_tokens,
                "threshold": high,
                "model": model,
            }
            _log.info(
                "SummarizingConversation: dropped %d older turn(s) (no summarizer), "
                "keeping last %d (estimated_tokens=%d threshold=%d)",
                n_compressed,
                keep,
                raw_tokens,
                high,
            )
            return verbatim_msgs

        try:
            summary_text = self._summarize_fn(to_compress_msgs)
        except Exception as exc:
            _log.warning(
                "SummarizingConversation: summarize_fn failed (%s); keeping last %d turn(s) verbatim",
                exc,
                keep,
            )
            return verbatim_msgs

        if not (isinstance(summary_text, str) and summary_text.strip()):
            self._cached_summary = None
            self._remember_prefix(to_compress, n_compressed)
            self.last_compaction_metadata = {
                "compressed_exchanges": 0,
                "dropped_exchanges": n_compressed,
                "kept_exchanges": len(verbatim),
                "estimated_tokens": raw_tokens,
                "threshold": high,
                "model": model,
            }
            _log.info(
                "SummarizingConversation: summarizer returned no text; dropped %d older turn(s), "
                "keeping last %d (estimated_tokens=%d threshold=%d model=%s)",
                n_compressed,
                keep,
                raw_tokens,
                high,
                model or "agent",
            )
            return verbatim_msgs

        self._cached_summary = summary_text
        self._remember_prefix(to_compress, n_compressed)
        self.last_compaction_metadata = {
            "compressed_exchanges": n_compressed,
            "kept_exchanges": len(verbatim),
            "reused": False,
            "estimated_tokens": raw_tokens,
            "threshold": high,
            "model": model,
            "model_source": model_source,
        }
        _log.info(
            "SummarizingConversation: compacted %d exchange(s) model=%s source=%s "
            "estimated_tokens=%d threshold=%d keep_turns=%d",
            n_compressed,
            model or "agent",
            model_source or "agent",
            raw_tokens,
            high,
            keep,
        )
        return [self._summary_block(n_compressed, summary_text)] + verbatim_msgs

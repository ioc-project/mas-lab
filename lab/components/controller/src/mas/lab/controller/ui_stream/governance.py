#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fold context size / token counts for the governance UI stream."""

from __future__ import annotations

from typing import Any

from mas.lab.controller.ui_stream.metrics import extract_cached_tokens, extract_usage
from mas.lab.controller.ui_stream.refs import DataSourceRef

_DROP_NATIVE = frozenset({"messages", "tools", "content", "output"})


def compact_context_record(record: dict[str, Any]) -> dict[str, Any]:
    """Drop prompt text from context_assembled / context_usage records."""
    out = {key: value for key, value in record.items() if key not in _DROP_NATIVE}
    usage = out.get("context_usage")
    if isinstance(usage, dict):
        out["context_usage"] = {k: v for k, v in usage.items() if k not in _DROP_NATIVE}
    return out


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _public(bucket: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bucket.items() if not str(key).startswith("_")}


class GovernanceStore:
    def __init__(self) -> None:
        self._by_key: dict[str, dict[str, Any]] = {}

    def _key(self, ref: DataSourceRef) -> str:
        return "|".join(
            [
                ref.library or "",
                ref.experiment or "",
                ref.scenario or "",
                ref.overlay or "",
                ref.session or ref.run or "",
            ]
        )

    def _bucket(self, ref: DataSourceRef) -> dict[str, Any]:
        return self._by_key.setdefault(self._key(ref), {})

    def apply(self, ref: DataSourceRef, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._bucket(ref)
        kind = str(payload.get("kind") or "")
        updated = False
        if payload.get("agent_id"):
            current["agent_id"] = payload.get("agent_id")
            updated = True
        if payload.get("model"):
            current["model"] = payload.get("model")
            updated = True
        usage = payload.get("context_usage") if isinstance(payload.get("context_usage"), dict) else {}
        if kind in {"context_assembled", "context_usage"} or usage:
            tokens = _as_int(
                payload.get("context_tokens")
                or payload.get("estimated_prompt_tokens")
                or usage.get("estimated_prompt_tokens")
                or payload.get("total_tokens")
            )
            if tokens is not None:
                current["context_tokens"] = tokens
                updated = True
            window = _as_int(payload.get("context_window") or usage.get("context_window"))
            if window is not None:
                current["context_window"] = window
                updated = True
            fill = _as_float(payload.get("fill_ratio") or usage.get("fill_ratio"))
            if fill is not None:
                current["fill_ratio"] = fill
                updated = True
            segments = _as_int(payload.get("segments") or payload.get("message_count"))
            if segments is not None:
                current["segments"] = segments
                updated = True
            for key in ("cache_hits", "cache_misses"):
                value = _as_int(payload.get(key) or usage.get(key))
                if value is not None:
                    current[key] = value
                    updated = True
            rate = _as_float(payload.get("cache_hit_rate") or usage.get("cache_hit_rate"))
            if rate is not None:
                current["cache_hit_rate"] = rate
                updated = True
            breakdown = payload.get("token_breakdown") or usage.get("token_breakdown")
            if isinstance(breakdown, dict) and breakdown:
                current["token_breakdown"] = {
                    str(k): int(v) for k, v in breakdown.items() if isinstance(v, (int, float))
                }
                updated = True
        if kind == "llm_call_end":
            folded = extract_usage(payload)
            if folded.get("prompt_tokens") or folded.get("total_tokens") or folded.get("cached_tokens"):
                current["prompt_tokens"] = int(folded["prompt_tokens"])
                current["completion_tokens"] = int(folded["completion_tokens"])
                current["cached_tokens"] = int(folded["cached_tokens"])
                if not current.get("context_tokens") and folded["prompt_tokens"]:
                    current["context_tokens"] = int(folded["prompt_tokens"])
                updated = True
            extra = extract_cached_tokens(
                payload.get("tokens_used") if isinstance(payload.get("tokens_used"), dict) else {}
            )
            if extra:
                current["cached_tokens"] = extra
                updated = True
        if current.get("context_window") and current.get("context_tokens") and "fill_ratio" not in current:
            current["fill_ratio"] = min(
                float(current["context_tokens"]) / float(current["context_window"]), 1.0
            )
            updated = True
        out = _public(current)
        if updated:
            out["_updated"] = 1
        return out

    def snapshot(self, ref: DataSourceRef) -> dict[str, Any]:
        return _public(self._bucket(ref))

    def clear(self) -> None:
        self._by_key.clear()

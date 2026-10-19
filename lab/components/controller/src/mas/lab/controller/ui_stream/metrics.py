#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live token/time aggregation from native records and metric artefacts."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from mas.lab.controller.ui_stream.refs import DataSourceRef

_END_KINDS = frozenset({"mas_call_end", "execution_end"})


def coerce_timestamp(value: Any) -> float | None:
    """Unix seconds from a numeric, numeric-string, or ISO-8601 timestamp."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts / 1000.0 if ts > 1e12 else ts
    text = str(value or "").strip()
    if not text:
        return None
    try:
        ts = float(text)
        return ts / 1000.0 if ts > 1e12 else ts
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_SKIP_FLATTEN = frozenset(
    {
        "note",
        "schema_version",
        "snapshot_at",
        "fragments_present",
        "fragments_missing",
        "scoring",
    }
)


def _family_dotted(metric_id: str) -> str | None:
    """``family_leaf`` → ``family.leaf`` for known eval families."""
    for family in ("outcome", "stateful"):
        prefix = family + "_"
        if metric_id.startswith(prefix) and len(metric_id) > len(prefix):
            return f"{family}.{metric_id[len(prefix):]}"
    return None


def _alias_eval_keys(out: dict[str, float | int]) -> dict[str, float | int]:
    """Mirror underscore session ids onto the live dotted KPI keys."""
    aliased: dict[str, float | int] = dict(out)
    for key, value in out.items():
        leaf = key.rsplit(".", 1)[-1]
        dotted = _family_dotted(leaf) or _family_dotted(key)
        if dotted:
            aliased.setdefault(dotted, value)
        if leaf.startswith("challenge_"):
            aliased.setdefault(leaf, value)
        if (
            key.startswith("scored.") or key.startswith("session.")
            or leaf.startswith("challenge_")
            or leaf.startswith("outcome_")
            or leaf.startswith("stateful_")
        ) and not leaf.startswith("mce."):
            aliased.setdefault(f"mce.{leaf}", value)
    return aliased


def flatten_metric_tree(value: Any, prefix: str = "", out: dict[str, float | int] | None = None) -> dict[str, float | int]:
    """Lift nested numeric/bool leaves (and ``{value: n}`` score objects) to dotted keys."""
    out = out if out is not None else {}
    leaf = prefix.rsplit(".", 1)[-1] if prefix else ""
    if leaf in _SKIP_FLATTEN:
        return out
    if isinstance(value, dict):
        raw = value.get("value")
        if prefix and isinstance(raw, bool):
            out[prefix] = int(raw)
            return out
        if prefix and isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[prefix] = raw
            return out
        for key, child in value.items():
            if str(key).startswith("_") or str(key) in _SKIP_FLATTEN:
                continue
            next_key = f"{prefix}.{key}" if prefix else str(key)
            flatten_metric_tree(child, next_key, out)
        return out
    if prefix and isinstance(value, bool):
        out[prefix] = int(value)
        return out
    if prefix and isinstance(value, (int, float)) and not isinstance(value, bool):
        out[prefix] = value
    return out


def flatten_metric_payload(payload: dict[str, Any] | None) -> dict[str, float | int]:
    """Flatten a ``kind: metric`` record or a nested progress/MCE document."""
    if not isinstance(payload, dict):
        return {}
    body = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    if not isinstance(body, dict):
        return {}
    return _alias_eval_keys(flatten_metric_tree(body))


def extract_cached_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = {}
    return _as_int(
        usage.get("cached_tokens")
        or usage.get("cache_read_input_tokens")
        or details.get("cached_tokens")
        or details.get("cache_read_tokens")
    )


def extract_usage(payload: dict[str, Any]) -> dict[str, float | int]:
    usage = payload.get("tokens_used") or payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt = _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _as_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total = _as_int(usage.get("total_tokens")) or (prompt + completion)
    cost = _as_float(usage.get("cost") or payload.get("cost"))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": extract_cached_tokens(usage),
        "cost": cost,
        "llm_calls": 1 if payload.get("kind") == "llm_call_end" else 0,
    }


def _public(bucket: dict[str, float | int]) -> dict[str, float | int]:
    return {key: value for key, value in bucket.items() if not str(key).startswith("_")}


class MetricsStore:
    def __init__(self) -> None:
        self._by_key: dict[str, dict[str, float | int]] = {}

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

    def _bucket(self, ref: DataSourceRef) -> dict[str, float | int]:
        return self._by_key.setdefault(
            self._key(ref),
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "cost": 0.0,
                "llm_calls": 0,
                "n_llm_calls": 0,
                "n_tool_calls": 0,
                "duration_s": 0.0,
                "elapsed_s": 0.0,
            },
        )

    def apply_native(
        self,
        ref: DataSourceRef,
        payload: dict[str, Any],
        *,
        wall: bool = False,
    ) -> dict[str, float | int]:
        current = self._bucket(ref)
        updated = False
        kind = str(payload.get("kind") or "")
        stopped = bool(current.get("_stopped"))
        ts = coerce_timestamp(payload.get("timestamp", payload.get("ts")))
        if not stopped:
            event_span = None
            if ts is not None:
                tmin = current.get("_tmin")
                tmax = current.get("_tmax")
                current["_tmin"] = float(ts) if tmin is None else min(float(tmin), float(ts))
                current["_tmax"] = float(ts) if tmax is None else max(float(tmax), float(ts))
                event_span = round(float(current["_tmax"]) - float(current["_tmin"]), 3)
            if wall:
                now = time.time()
                if current.get("_wall_start") is None:
                    current["_wall_start"] = now
                wall_span = round(now - float(current["_wall_start"]), 3)
                current["elapsed_s"] = wall_span
                current["duration_s"] = max(wall_span, event_span or 0.0)
                updated = True
            elif event_span is not None:
                current["duration_s"] = event_span
                updated = True
        if kind in _END_KINDS:
            current["_stopped"] = 1
            updated = True
        if kind == "tool_call_start":
            current["n_tool_calls"] = int(current.get("n_tool_calls") or 0) + 1
            updated = True
        if kind == "llm_call_end":
            usage = extract_usage(payload)
            if usage["total_tokens"] or usage["cost"] or usage["llm_calls"] or usage["cached_tokens"]:
                for field in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "llm_calls"):
                    current[field] = int(current[field]) + int(usage[field])
                current["n_llm_calls"] = int(current["llm_calls"])
                current["cost"] = float(current["cost"]) + float(usage["cost"])
                updated = True
        out = _public(current)
        if updated:
            out["_updated"] = 1
        return out

    def apply_step(self, ref: DataSourceRef, payload: dict[str, Any]) -> dict[str, float | int]:
        current = self._bucket(ref)
        wall = current.get("_wall_start") is not None
        keep_if_zero = {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "n_llm_calls",
            "llm_calls",
            "n_tool_calls",
            "duration_s",
            "elapsed_s",
        }
        for key, value in flatten_metric_payload(payload).items():
            if str(key).startswith("_"):
                continue
            if wall and key in {"duration_s", "elapsed_s"}:
                continue
            if key in keep_if_zero and value == 0 and current.get(key):
                continue
            current[str(key)] = value
        return _public(current)

    def snapshot(self, ref: DataSourceRef) -> dict[str, float | int]:
        return _public(self._bucket(ref))

    def clear(self) -> None:
        self._by_key.clear()

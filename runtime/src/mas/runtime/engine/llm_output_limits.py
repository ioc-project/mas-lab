#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Output-token limits for Chat Completions calls.

No limit is sent unless one is configured: the server default applies, as in
the OpenAI SDK, LangChain or CrewAI. Resolution, highest precedence first:

1. explicit ``max_tokens_override`` (``LiveLlmEngine(max_tokens=...)``)
2. ``spec.models[]`` row after overlays and ``--override`` / ``--max-tokens``
   (then deprecated ``spec.llm``)
3. ``MAS_LLM_MAX_TOKENS`` / ``MAS_LLM_MAX_COMPLETION_TOKENS`` / ``MAS_LLM_ON_TRUNCATION``
4. infra ``spec.models.generation`` (LLMProxy / LLMLocal)
5. model catalog ``defaults``

The result is bounded by infra ``generation.max_output_tokens`` and the
catalog ``max_output_tokens``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from mas.runtime import hostenv

logger = logging.getLogger(__name__)
_WARNED_CONTEXT_MODELS: set[str] = set()

TRUNCATION_ACTIONS = ("ignore", "warn", "error", "escalate")
ENV_MAX_TOKENS = "MAS_LLM_MAX_TOKENS"
ENV_MAX_COMPLETION_TOKENS = "MAS_LLM_MAX_COMPLETION_TOKENS"
ENV_ON_TRUNCATION = "MAS_LLM_ON_TRUNCATION"
_TOKEN_KEYS = ("max_tokens", "max_completion_tokens")
_PRICING_KEYS = (
    "input_per_million_tokens",
    "cached_input_per_million_tokens",
    "output_per_million_tokens",
)


@dataclass(frozen=True)
class TruncationPolicy:
    """What to do when a completion stops with ``finish_reason == "length"``."""

    action: str = "warn"
    factor: float = 2.0
    max_tokens: int | None = None
    retries: int = 1

    @classmethod
    def from_spec(cls, raw: Any, *, base: TruncationPolicy | None = None) -> TruncationPolicy:
        out = base or cls()
        if raw is None:
            return out
        if isinstance(raw, str):
            return replace(out, action=_action(raw))
        if not isinstance(raw, Mapping):
            raise ValueError(f"on_truncation must be a string or mapping, got {type(raw).__name__}")
        changes: dict[str, Any] = {}
        if raw.get("action") is not None:
            changes["action"] = _action(raw["action"])
        if raw.get("factor") is not None:
            factor = float(raw["factor"])
            if factor <= 1:
                raise ValueError("on_truncation.factor must be > 1")
            changes["factor"] = factor
        if raw.get("max_tokens") is not None:
            changes["max_tokens"] = _positive_int(raw["max_tokens"], "on_truncation.max_tokens")
        if raw.get("retries") is not None:
            retries = int(raw["retries"])
            if retries < 0:
                raise ValueError("on_truncation.retries must be >= 0")
            changes["retries"] = retries
        return replace(out, **changes)


@dataclass(frozen=True)
class OutputLimits:
    """Resolved output-token settings for one model binding."""

    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    ceiling: int | None = None
    context_window: int | None = None
    pricing: dict[str, Any] = field(default_factory=dict)
    truncation: TruncationPolicy = field(default_factory=TruncationPolicy)

    @property
    def budget(self) -> int | None:
        """The cap actually sent (``max_completion_tokens`` wins when both are set)."""
        return self.max_completion_tokens if self.max_completion_tokens is not None else self.max_tokens

    def with_budget(self, value: int) -> OutputLimits:
        if self.max_completion_tokens is not None:
            return replace(self, max_completion_tokens=value)
        return replace(self, max_tokens=value)

    def fit_to_prompt(self, prompt_tokens: int) -> OutputLimits:
        """Shrink the budget so prompt + completion stays inside ``context_window``."""
        budget = self.budget
        if budget is None or self.context_window is None:
            return self
        remaining = self.context_window - prompt_tokens
        if remaining < 1 or remaining >= budget:
            return self
        logger.debug(
            "output tokens %d -> %d to fit context_window %d (prompt ~%d tokens)",
            budget,
            remaining,
            self.context_window,
            prompt_tokens,
        )
        return self.with_budget(remaining)

    def escalated(self, completion_tokens: int | None) -> int | None:
        """Next budget after a truncated completion, or ``None`` when it cannot grow."""
        current = self.budget if self.budget is not None else completion_tokens
        if not current:
            return None
        limit = self.truncation.max_tokens or self.ceiling
        nxt = math.ceil(current * self.truncation.factor)
        if limit is not None:
            nxt = min(nxt, limit)
        return nxt if nxt > current else None


def _action(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value not in TRUNCATION_ACTIONS:
        raise ValueError(f"on_truncation action {raw!r} not in {', '.join(TRUNCATION_ACTIONS)}")
    return value


def _positive_int(raw: Any, name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    raw = str(env.get(name) or "").strip()
    return _positive_int(raw, name) if raw else None


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _min_known(*values: int | None) -> int | None:
    known = [v for v in values if v is not None]
    return min(known) if known else None


def resolve_output_limits(
    manifest: dict[str, Any] | None,
    *,
    model: str | None = None,
    generation: Mapping[str, Any] | None = None,
    max_tokens_override: int | None = None,
    max_completion_tokens_override: int | None = None,
    env: Mapping[str, str] | None = None,
) -> OutputLimits:
    from mas.runtime.engine.llm_model_catalog import default_model_catalog
    from mas.runtime.engine.llm_request import resolve_model_context

    env = hostenv.snapshot() if env is None else env
    entry, fallback, name = resolve_model_context(manifest, model=model)
    entry = entry or {}
    fallback = fallback or {}
    generation = generation or {}
    info = default_model_catalog().get(name)
    catalog_defaults = info.defaults if info is not None else {}

    values: dict[str, int | None] = {}
    overrides = {"max_tokens": max_tokens_override, "max_completion_tokens": max_completion_tokens_override}
    env_names = {"max_tokens": ENV_MAX_TOKENS, "max_completion_tokens": ENV_MAX_COMPLETION_TOKENS}
    for key in _TOKEN_KEYS:
        raw = _first(
            overrides[key],
            entry.get(key),
            fallback.get(key),
            _env_int(env, env_names[key]),
            generation.get(key),
            catalog_defaults.get(key),
        )
        values[key] = _positive_int(raw, key) if raw is not None else None

    infra_cap = generation.get("max_output_tokens")
    ceiling = _min_known(
        _positive_int(infra_cap, "generation.max_output_tokens") if infra_cap is not None else None,
        info.max_output_tokens if info is not None else None,
    )
    if ceiling is not None:
        for key, value in values.items():
            if value is not None and value > ceiling:
                logger.warning("%s=%d exceeds output ceiling %d for %s; using %d", key, value, ceiling, name, ceiling)
                values[key] = ceiling

    policy = TruncationPolicy.from_spec(generation.get("on_truncation"))
    env_action = str(env.get(ENV_ON_TRUNCATION) or "").strip()
    if env_action:
        policy = replace(policy, action=_action(env_action))
    policy = TruncationPolicy.from_spec(fallback.get("on_truncation"), base=policy)
    policy = TruncationPolicy.from_spec(entry.get("on_truncation"), base=policy)

    window = _first(
        entry.get("context_window"),
        generation.get("context_window"),
        info.context_window if info is not None else None,
    )
    if window is None:
        from mas.runtime.spec.defaults import DEFAULT_MODEL_CONTEXT_WINDOW

        warning_key = name or "<unspecified>"
        if warning_key not in _WARNED_CONTEXT_MODELS:
            _WARNED_CONTEXT_MODELS.add(warning_key)
            logger.warning(
                "Unable to resolve context window for model %s; using fallback %d. "
                "Set spec.models[].context_window or infra generation.context_window.",
                warning_key,
                DEFAULT_MODEL_CONTEXT_WINDOW,
            )
        window = DEFAULT_MODEL_CONTEXT_WINDOW
    return OutputLimits(
        max_tokens=values["max_tokens"],
        max_completion_tokens=values["max_completion_tokens"],
        ceiling=ceiling,
        context_window=int(window) if window is not None else None,
        pricing=_numeric_pricing(
            info.pricing if info is not None else {},
            generation.get("pricing") if isinstance(generation.get("pricing"), Mapping) else {},
        ),
        truncation=policy,
    )


def estimate_prompt_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """Rough prompt size (4 chars per token), used only to keep prompt + budget in the window."""
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            from mas.runtime.contracts.content_types import MEDIA_PART_TOKEN_ESTIMATE, media_parts, text_chars

            chars += text_chars(content)
            chars += MEDIA_PART_TOKEN_ESTIMATE * 4 * len(media_parts(content))
        elif content is not None:
            chars += len(json.dumps(content, default=str))
        for call in message.get("tool_calls") or []:
            chars += len(json.dumps(call, default=str))
    if tools:
        chars += len(json.dumps(tools, default=str))
    return chars // 4 + 4 * len(messages)


def _numeric_pricing(*sources: Mapping[str, Any]) -> dict[str, float]:
    """Merge known rates while keeping catalog provenance out of runtime payloads."""
    rates: dict[str, float] = {}
    for source in sources:
        for key in _PRICING_KEYS:
            value = source.get(key)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number >= 0:
                rates[key] = number
    return rates


def estimate_tool_tokens(tools: list[dict[str, Any]] | None) -> int:
    """Estimate the prompt budget consumed by tool schemas sent separately."""
    if not tools:
        return 0
    return len(json.dumps(tools, ensure_ascii=False, default=str)) // 4 + 4 * len(tools)


def estimate_model_cost_usd(
    model: str,
    usage: Mapping[str, Any] | None,
    *,
    pricing_override: Mapping[str, Any] | None = None,
) -> float | None:
    """Estimate text-token cost from versioned catalog rates and provider usage."""
    if not isinstance(usage, Mapping):
        return None
    prompt_raw = usage.get("prompt_tokens")
    completion_raw = usage.get("completion_tokens")
    if prompt_raw is None or completion_raw is None:
        return None
    try:
        prompt_tokens = max(0, int(prompt_raw))
        completion_tokens = max(0, int(completion_raw))
    except (TypeError, ValueError):
        return None

    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    info = default_model_catalog().get(model)
    pricing = dict(info.pricing) if info is not None else {}
    if isinstance(pricing_override, Mapping):
        pricing.update(pricing_override)
    input_rate = pricing.get("input_per_million_tokens")
    output_rate = pricing.get("output_per_million_tokens")
    if input_rate is None or output_rate is None:
        return None

    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, Mapping) else {}
    cached_raw = (
        details.get("cached_tokens")
        or details.get("cache_read_input_tokens")
        or usage.get("cache_read_input_tokens")
        or usage.get("cached_tokens")
        or 0
    )
    try:
        cached_tokens = min(prompt_tokens, max(0, int(cached_raw)))
        input_rate = float(input_rate)
        output_rate = float(output_rate)
        cached_rate = float(pricing.get("cached_input_per_million_tokens", input_rate))
    except (TypeError, ValueError):
        return None
    if min(input_rate, output_rate, cached_rate) < 0:
        return None
    uncached_tokens = prompt_tokens - cached_tokens
    return (uncached_tokens * input_rate + cached_tokens * cached_rate + completion_tokens * output_rate) / 1_000_000


def output_token_kwargs(model: str | None = None, *, generation: Mapping[str, Any] | None = None) -> dict[str, int]:
    """``max_tokens`` / ``max_completion_tokens`` kwargs for direct OpenAI SDK callers (judges, generators)."""
    limits = resolve_output_limits(None, model=model, generation=generation)
    if limits.max_completion_tokens is not None:
        return {"max_completion_tokens": limits.max_completion_tokens}
    if limits.max_tokens is not None:
        return {"max_tokens": limits.max_tokens}
    return {}


def sum_usage(total: Mapping[str, Any] | None, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(total or {})
    for key, value in (extra or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = out.get(key, 0) + value
        else:
            out.setdefault(key, value)
    return out

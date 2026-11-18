#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Static Chat Completions sampling from spec.models[] plus extra/extra_body."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def sampling_temperature(configured: float, *, answering_from_tools: bool) -> float:
    """Temperature sent for one call: 0.0 when answering from tool results, else the configured one.

    The live engine and the observability request on ``llm_call_start`` both use
    this, so the stamped value cannot drift from the sent one.
    """
    return 0.0 if answering_from_tools else configured


_SAMPLING_KEYS = (
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "min_tokens",
    "min_p",
    "repetition_penalty",
    "service_tier",
    "store",
    "metadata",
    "modalities",
    "prediction",
    "prompt_cache_key",
    "prompt_cache_retention",
    "prompt_cache_breakpoints",
    "top_p",
    "top_k",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "n",
    "stop",
    "logit_bias",
    "user",
    "verbosity",
    "response_format",
    "logprobs",
    "top_logprobs",
    "parallel_tool_calls",
)


@dataclass(frozen=True)
class SamplingSettings:
    """Static sampling knobs from ``spec.models[]`` / ``spec.llm``."""

    values: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def to_spec_dict(self) -> dict[str, Any]:
        return dict(self.values)


def model_entry_from_manifest(
    manifest: dict[str, Any] | None,
    *,
    model: str | None = None,
) -> dict[str, Any] | None:
    """Return the ``spec.models[]`` row for ``model``, else the first row."""
    spec = (manifest or {}).get("spec") or {}
    rows = [row for row in (spec.get("models") or []) if isinstance(row, dict)]
    if not rows:
        return None
    want = str(model or "").strip()
    if not want:
        return rows[0]
    suffix = want.split("/")[-1]
    for row in rows:
        name = str(row.get("model") or "").strip()
        ident = str(row.get("id") or "").strip()
        if want in {name, ident} or suffix in {name, ident, name.split("/")[-1]}:
            return row
    return rows[0]


def sampling_settings_from_entry(
    entry: dict[str, Any] | None,
    *,
    fallback: dict[str, Any] | None = None,
    model: str | None = None,
) -> SamplingSettings:
    values: dict[str, Any] = {}
    for key in _SAMPLING_KEYS:
        if isinstance(entry, dict) and key in entry and entry[key] is not None:
            values[key] = entry[key]
        elif isinstance(fallback, dict) and key in fallback and fallback[key] is not None:
            values[key] = fallback[key]
    name = model or (entry or {}).get("model") or (fallback or {}).get("model")
    if name:
        from mas.runtime.engine.llm_model_catalog import default_model_catalog

        info = default_model_catalog().get(str(name))
        if info is not None:
            for key in _SAMPLING_KEYS:
                if key in values:
                    continue
                if key in info.defaults:
                    values[key] = info.defaults[key]
    return SamplingSettings(values)


def resolve_model_context(
    manifest: dict[str, Any] | None,
    *,
    model: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Return ``(entry, fallback, name)`` shared by the ``*_from_manifest`` helpers."""
    spec = (manifest or {}).get("spec") or {}
    entry = model_entry_from_manifest(manifest, model=model)
    fallback = spec.get("llm") if isinstance(spec.get("llm"), dict) else None
    name = model or (entry or {}).get("model")
    return entry, fallback, str(name) if name else None


def sampling_settings_from_manifest(
    manifest: dict[str, Any] | None,
    *,
    model: str | None = None,
) -> SamplingSettings:
    entry, fallback, name = resolve_model_context(manifest, model=model)
    return sampling_settings_from_entry(entry, fallback=fallback, model=name)


def extra_from_manifest(manifest: dict[str, Any] | None, *, model: str | None = None) -> dict[str, Any]:
    entry, fallback, _name = resolve_model_context(manifest, model=model)
    extra: dict[str, Any] = {}
    if isinstance(fallback, dict) and isinstance(fallback.get("extra"), dict):
        extra.update(fallback["extra"])
    if isinstance(entry, dict) and isinstance(entry.get("extra"), dict):
        extra.update(entry["extra"])
    return extra


def apply_sampling_payload(
    payload: dict[str, Any],
    settings: SamplingSettings,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    out = dict(payload)
    info = default_model_catalog().get(model or payload.get("model"))
    for key, value in settings.values.items():
        if key in ("max_tokens", "max_completion_tokens"):
            continue
        if info is not None:
            value = info.clamp(key, value)
        out[key] = value
    return out


def apply_output_token_limit(
    payload: dict[str, Any],
    *,
    max_tokens: int | None,
    max_completion_tokens: int | None,
) -> dict[str, Any]:
    """Set at most one of ``max_tokens`` / ``max_completion_tokens``; omit both when unset.

    An explicit ``max_completion_tokens`` wins, as does one already mapped
    by :func:`apply_reasoning_payload` or supplied through ``extra``.
    """
    out = dict(payload)
    if max_completion_tokens is not None:
        out["max_completion_tokens"] = int(max_completion_tokens)
    elif max_tokens is not None and "max_completion_tokens" not in out:
        out.setdefault("max_tokens", int(max_tokens))
    if "max_completion_tokens" in out:
        out.pop("max_tokens", None)
    return out


def merge_extra_body(*parts: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge extra maps; nested dict values (e.g. chat_template_kwargs) merge."""
    merged: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for key, value in part.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
    return merged

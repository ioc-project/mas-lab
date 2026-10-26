#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""History token budget from the model context window.

Used by the assembler plugin (``manage_history`` hint + payload trim) and by
``mas-ctl compile`` (fill omitted context-manager params). Not imported by
runtime: the kernel does not choose a history budget.

History max size = model ``context_window`` minus reserved completion space
(``models[].max_tokens``) and any explicit ``trimmer.reserve_tokens``.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from mas.runtime.spec.defaults import (
    DEFAULT_CONTEXT_RESERVE_TOKENS,
    DEFAULT_CONTEXT_SAFETY_MARGIN_RATIO,
    DEFAULT_HYSTERESIS_RATIO,
    DEFAULT_KEEP_TURNS,
    DEFAULT_MODEL_CONTEXT_WINDOW,
    DEFAULT_MODEL_MAX_TOKENS,
)

_log = logging.getLogger(__name__)
_WARNED_CONTEXT_MODELS: set[str] = set()


def _spec(manifest_or_spec: dict[str, Any] | None) -> dict[str, Any]:
    raw = manifest_or_spec or {}
    if "spec" in raw or str(raw.get("kind", "")).lower() == "agent":
        spec = raw.get("spec")
        return spec if isinstance(spec, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _typed_models(manifest_or_spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    models = _spec(manifest_or_spec).get("models") or []
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def primary_model_binding(manifest_or_spec: dict[str, Any] | None) -> dict[str, Any]:
    """First ``spec.models[]`` entry, preferring ``id: main``."""
    typed = _typed_models(manifest_or_spec)
    for model in typed:
        if str(model.get("id") or "main") == "main":
            return model
    return typed[0] if typed else {}


def model_context_window(
    manifest_or_spec: dict[str, Any] | None,
    *,
    resolved_model: str | None = None,
    context_window_override: int | None = None,
) -> int:
    """Resolve the input window from overrides, the effective model, or fallback."""
    if context_window_override is not None:
        try:
            value = int(context_window_override)
        except (TypeError, ValueError):
            value = 0
        if value >= 1:
            return value

    raw = primary_model_binding(manifest_or_spec).get("context_window")
    if raw is not None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value >= 1:
            return value

    binding = primary_model_binding(manifest_or_spec)
    model = str(resolved_model or binding.get("model") or "").strip()
    if model and model.lower() not in {"any", "default"}:
        from mas.runtime.engine.llm_model_catalog import default_model_catalog

        info = default_model_catalog().get(model)
        if info is not None and info.context_window:
            return info.context_window

    warning_key = model or "<unspecified>"
    if warning_key not in _WARNED_CONTEXT_MODELS:
        _WARNED_CONTEXT_MODELS.add(warning_key)
        _log.warning(
            "Unable to resolve context window for model %s; using fallback %d. "
            "Set spec.models[].context_window or infra generation.context_window.",
            model or "<unspecified>",
            DEFAULT_MODEL_CONTEXT_WINDOW,
        )
    return DEFAULT_MODEL_CONTEXT_WINDOW


def model_completion_tokens(
    manifest_or_spec: dict[str, Any] | None,
    *,
    completion_tokens_override: int | None = None,
) -> int:
    """Tokens reserved for the model completion (``models[].max_tokens``)."""
    if completion_tokens_override is not None:
        try:
            value = int(completion_tokens_override)
        except (TypeError, ValueError):
            value = 0
        if value >= 1:
            return value

    raw = primary_model_binding(manifest_or_spec).get("max_tokens")
    if raw is None:
        return DEFAULT_MODEL_MAX_TOKENS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MODEL_MAX_TOKENS
    return value if value >= 1 else DEFAULT_MODEL_MAX_TOKENS


def explicit_trimmer(manifest_or_spec: dict[str, Any] | None) -> dict[str, Any]:
    cm = _spec(manifest_or_spec).get("context_manager") or {}
    params = cm.get("params") if isinstance(cm, dict) else {}
    trimmer = params.get("trimmer") if isinstance(params, dict) else None
    return trimmer if isinstance(trimmer, dict) else {}


def _safety_margin_tokens(manifest_or_spec: dict[str, Any] | None, max_tokens: int) -> int:
    raw = explicit_trimmer(manifest_or_spec).get("safety_margin_ratio")
    try:
        ratio = float(raw) if raw is not None else DEFAULT_CONTEXT_SAFETY_MARGIN_RATIO
    except (TypeError, ValueError):
        ratio = DEFAULT_CONTEXT_SAFETY_MARGIN_RATIO
    ratio = min(0.5, max(0.0, ratio))
    return math.ceil(max_tokens * ratio)


def derived_trimmer_params(
    manifest_or_spec: dict[str, Any] | None,
    *,
    resolved_model: str | None = None,
    context_window_override: int | None = None,
    completion_tokens_override: int | None = None,
) -> tuple[int, int]:
    """Return ``(max_tokens, reserve_tokens)`` for assembly trim.

    Explicit ``params.trimmer`` wins; otherwise max_tokens is the model
    context window and reserve is the completion budget.
    """
    trimmer = explicit_trimmer(manifest_or_spec)
    raw_max = trimmer.get("max_tokens", trimmer.get("token_budget"))
    context_window = model_context_window(
        manifest_or_spec,
        resolved_model=resolved_model,
        context_window_override=context_window_override,
    )
    completion = model_completion_tokens(
        manifest_or_spec,
        completion_tokens_override=completion_tokens_override,
    )
    if raw_max is not None:
        try:
            max_tokens = int(raw_max)
        except (TypeError, ValueError):
            max_tokens = context_window
        if max_tokens < 1:
            max_tokens = context_window
    else:
        max_tokens = context_window
    raw_reserve = trimmer.get("reserve_tokens")
    if raw_reserve is not None:
        try:
            reserve = int(raw_reserve)
        except (TypeError, ValueError):
            reserve = completion
        reserve = max(0, reserve)
    else:
        reserve = completion if completion >= 1 else DEFAULT_CONTEXT_RESERVE_TOKENS
    return max_tokens, reserve


def history_token_budget(
    manifest_or_spec: dict[str, Any] | None,
    *,
    resolved_model: str | None = None,
    context_window_override: int | None = None,
    completion_tokens_override: int | None = None,
) -> int:
    """Tokens available for committed history (window minus completion reserve)."""
    max_tokens, reserve = derived_trimmer_params(
        manifest_or_spec,
        resolved_model=resolved_model,
        context_window_override=context_window_override,
        completion_tokens_override=completion_tokens_override,
    )
    margin = _safety_margin_tokens(manifest_or_spec, max_tokens)
    return max(1, max_tokens - reserve - margin)


def assembly_trimmer_params(
    manifest: dict | None,
    *,
    resolved_model: str | None = None,
    context_window_override: int | None = None,
    completion_tokens_override: int | None = None,
) -> tuple[int, int] | None:
    """Return ``(max_tokens, reserve_tokens)`` for the assembled payload cap.

    Uses explicit ``spec.context_manager.params.trimmer`` when set; otherwise
    the primary model's ``context_window`` minus completion ``max_tokens``.
    """
    max_tokens, reserve = derived_trimmer_params(
        manifest,
        resolved_model=resolved_model,
        context_window_override=context_window_override,
        completion_tokens_override=completion_tokens_override,
    )
    if max_tokens < 1:
        return None
    margin = _safety_margin_tokens(manifest, max_tokens)
    return max_tokens, max(0, reserve) + margin


def context_manager_history_budget_hint(
    manifest: dict | None,
    *,
    resolved_model: str | None = None,
    context_window_override: int | None = None,
    completion_tokens_override: int | None = None,
    fixed_context_tokens: int = 0,
) -> int:
    """Token hint for history: explicit threshold or the resolved model budget."""
    cm = _spec(manifest).get("context_manager") or {}
    cm_params = cm.get("params") if isinstance(cm, dict) else {}
    if not isinstance(cm_params, dict):
        cm_params = {}
    cm_type = _normalise_cm_type(cm) if isinstance(cm, dict) else ""
    try:
        ratio = min(
            1.0,
            max(0.0, float(cm_params.get("hysteresis_ratio", DEFAULT_HYSTERESIS_RATIO))),
        )
    except (TypeError, ValueError):
        ratio = DEFAULT_HYSTERESIS_RATIO
    hard_history_budget = max(
        1,
        history_token_budget(
            manifest,
            resolved_model=resolved_model,
            context_window_override=context_window_override,
            completion_tokens_override=completion_tokens_override,
        )
        - max(0, int(fixed_context_tokens)),
    )
    soft_summary_budget = (
        max(1, int(hard_history_budget / (1.0 + ratio)))
        if _is_summarising_type(cm_type)
        else hard_history_budget
    )
    threshold = cm_params.get("summary_threshold") if isinstance(cm_params, dict) else None
    if threshold is not None:
        try:
            value = int(threshold)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return min(value, soft_summary_budget)
    return soft_summary_budget


def _normalise_cm_type(cm: dict[str, Any]) -> str:
    raw = str(cm.get("type") or cm.get("ref") or "").strip().lower()
    return raw.replace("_", "-")


def _is_summarising_type(cm_type: str) -> bool:
    return "summaris" in cm_type


def _is_sliding_type(cm_type: str) -> bool:
    return "sliding" in cm_type


def fill_model_window_defaults(spec: dict[str, Any]) -> None:
    """Keep omitted windows automatic until the infra-resolved model is known.

    A model can be remapped by infra after compile. Materializing a generic
    fallback here would make it look like an explicit override at runtime.
    """
    _ = spec


def fill_context_manager_defaults(spec: dict[str, Any]) -> None:
    """Fill omitted ``spec.context_manager`` type and recency/trimmer params.

    ``mas-ctl compile`` calls this so the resolved agent YAML shows the history
    policy. The assembler plugin derives the same numbers when the keys are absent.
    """
    from mas.runtime.agent_defaults import default_context_manager_id
    from mas.runtime.spec.plugin_binding import normalize_plugin_binding

    fill_model_window_defaults(spec)

    cm = normalize_plugin_binding(spec.get("context_manager"), field="spec.context_manager")
    spec["context_manager"] = cm
    if not (cm.get("type") or cm.get("ref")):
        cm["type"] = default_context_manager_id()

    params = cm.get("params")
    if not isinstance(params, dict):
        params = {}
        cm["params"] = params

    cm_type = _normalise_cm_type(cm)
    keep = DEFAULT_KEEP_TURNS
    for key in ("keep_turns", "window_size", "max_turns"):
        raw = params.get(key)
        if raw is not None:
            try:
                keep = max(1, int(raw))
                break
            except (TypeError, ValueError):
                continue

    if _is_summarising_type(cm_type):
        from mas.runtime.registry import get_registry

        params.setdefault("keep_turns", keep)
        params.setdefault("hysteresis_ratio", DEFAULT_HYSTERESIS_RATIO)
        params.setdefault("summarizer", get_registry().default_for("summarizer") or "llm")
    elif _is_sliding_type(cm_type) or not cm_type:
        params.setdefault("keep_turns", keep)
        params.setdefault("window_size", keep)

    trimmer = params.get("trimmer")
    if not isinstance(trimmer, dict):
        trimmer = {}
        params["trimmer"] = trimmer
    binding = primary_model_binding({"spec": spec})
    if binding.get("context_window") is not None:
        trimmer.setdefault("max_tokens", int(binding["context_window"]))
    trimmer.setdefault("reserve_tokens", model_completion_tokens({"spec": spec}))
    trimmer.setdefault("safety_margin_ratio", DEFAULT_CONTEXT_SAFETY_MARGIN_RATIO)

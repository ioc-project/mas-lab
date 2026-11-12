#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Forward deprecated ``spec.control.retry`` / ``circuit_breaker`` onto the pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mas.runtime.deprecation import warn_deprecated_once
from mas.runtime.layers.compose import layer_call_kinds, layer_id
from mas.runtime.layers.defaults import drop_default_stamp, merge_default_retry


def pipeline_from_agent_spec(
    spec: Mapping[str, Any] | None,
    pipeline: list[dict[str, Any]] | None,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    """Default retry plus leftover ``spec.control`` steps for *kind*."""
    steps = merge_default_retry(list(pipeline or []), kind)
    control = spec.get("control") if isinstance(spec, Mapping) else None
    if not isinstance(control, Mapping):
        return steps
    retry_raw = control.get("retry")
    if isinstance(retry_raw, Mapping) and retry_raw:
        warn_deprecated_once(
            "spec.control.retry",
            "spec.control.retry is deprecated: list a retry layer on the infra pipeline",
        )
        slot = "llm" if kind == "llm" else "tools"
        params = retry_raw.get(slot)
        if isinstance(params, Mapping) and params:
            for step in steps:
                if layer_id(step) == "retry" and kind in layer_call_kinds(step):
                    from_defaults = drop_default_stamp(step)
                    named = dict(step.get("params") or {})
                    # the default step gives way to the deprecated spelling; a retry layer the pipeline
                    # names keeps its own params
                    step["params"] = {**named, **params} if from_defaults else {**params, **named}
                    break
            else:
                steps.insert(
                    0,
                    {"layer": "retry", "for": [kind], "params": dict(params)},
                )
    breaker = control.get("circuit_breaker")
    if isinstance(breaker, Mapping) and breaker and breaker.get("enabled") is not False:
        warn_deprecated_once(
            "spec.control.circuit_breaker",
            "spec.control.circuit_breaker is deprecated: list a circuit_breaker layer on the infra pipeline",
        )
        name = str(breaker.get("plugin") or "circuit_breaker")
        if not any(layer_id(step) == name and kind in layer_call_kinds(step) for step in steps):
            steps.append(
                {
                    "layer": name,
                    "for": [kind],
                    "per": "destination",
                    "params": {k: v for k, v in dict(breaker).items() if k != "plugin"},
                }
            )
    return steps


def resolve_call_pipeline(
    spec: Mapping[str, Any] | None,
    pipeline: list[dict[str, Any]] | None,
    *,
    kind: str,
    retry_overlay: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The pipeline one call kind runs under.

    Documented defaults, leftover ``spec.control.retry`` / ``circuit_breaker``, then
    the layers the pipeline names. ``retry_overlay`` fills omitted llm retry keys.
    A key set on a named layer is never overwritten.
    """
    steps = pipeline_from_agent_spec(spec, pipeline, kind=kind)
    return overlay_retry_params(steps, retry_overlay) if kind == "llm" and retry_overlay else steps


def overlay_retry_params(
    pipeline: list[dict[str, Any]] | None,
    overlay: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Fill omitted llm retry params. Keys a named layer (or spec.control) set stay."""
    steps = [dict(step) if isinstance(step, dict) else step for step in (pipeline or [])]
    extra = dict(overlay) if isinstance(overlay, Mapping) else {}
    applied = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        if applied or layer_id(step) != "retry" or "llm" not in layer_call_kinds(step):
            continue
        from_defaults = drop_default_stamp(step)
        if extra:
            params = dict(step.get("params") or {})
            for key, value in extra.items():
                if not from_defaults and key in params:
                    continue
                params[key] = value
            step["params"] = params
        applied = True
    return steps

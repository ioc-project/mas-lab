#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Forward deprecated ``spec.control.<key>`` onto the pipeline, for the layers that declare a ``control_key``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mas.runtime.deprecation import warn_deprecated_once
from mas.runtime.layers.compose import layer_call_kinds, layer_id
from mas.runtime.layers.defaults import drop_default_stamp, merge_default_steps

_SLOTS = {"llm": "llm", "tool": "tools"}


def pipeline_from_agent_spec(
    spec: Mapping[str, Any] | None,
    pipeline: list[dict[str, Any]] | None,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    """Default steps plus leftover ``spec.control.<key>`` steps for *kind*.

    A layer row names the ``spec.control`` key it answers to (``control_key``); the runtime knows no layer name.
    """
    from mas.runtime.layers.compose import layers_declaring

    steps = merge_default_steps(list(pipeline or []), kind)
    control = spec.get("control") if isinstance(spec, Mapping) else None
    if not isinstance(control, Mapping):
        return steps
    for name, key in layers_declaring("control_key"):
        raw = control.get(key)
        if not isinstance(raw, Mapping) or not raw:
            continue
        warn_deprecated_once(
            f"spec.control.{key}",
            f"spec.control.{key} is deprecated: list a {name} layer on the infra pipeline",
        )
        if set(_SLOTS.values()) & set(raw):
            _merge_slot(steps, name, kind, raw.get(_SLOTS.get(kind, "")))
        elif raw.get("enabled") is not False:
            layer = str(raw.get("plugin") or name)
            if not any(layer_id(step) == layer and kind in layer_call_kinds(step) for step in steps):
                steps.append(
                    {
                        "layer": layer,
                        "for": [kind],
                        "per": "destination",
                        "params": {k: v for k, v in dict(raw).items() if k != "plugin"},
                    }
                )
    return steps


def _merge_slot(steps: list[dict[str, Any]], name: str, kind: str, params: Any) -> None:
    """Fold a ``spec.control.<key>.<llm|tools>`` mapping into the layer's step for *kind*, or add the step."""
    if not isinstance(params, Mapping) or not params:
        return
    for step in steps:
        if layer_id(step) == name and kind in layer_call_kinds(step):
            from_defaults = drop_default_stamp(step)
            named = dict(step.get("params") or {})
            # a default step gives way to the deprecated spelling; a layer the pipeline names keeps its own params
            step["params"] = {**named, **params} if from_defaults else {**params, **named}
            return
    steps.insert(0, {"layer": name, "for": [kind], "params": dict(params)})


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
    from mas.runtime.layers.compose import layers_declaring

    overlaid = {name for name, _ in layers_declaring("retry_overlay")}
    steps = [dict(step) if isinstance(step, dict) else step for step in (pipeline or [])]
    extra = dict(overlay) if isinstance(overlay, Mapping) else {}
    applied = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        if applied or layer_id(step) not in overlaid or "llm" not in layer_call_kinds(step):
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

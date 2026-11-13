#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Default pipeline steps. A library row declares them; the runtime names none."""

from __future__ import annotations

from typing import Any

FROM_DEFAULTS = object()
_USER_STAMP = "_from_defaults"


def drop_default_stamp(step: dict[str, Any]) -> bool:
    """Pop the merge stamp. A YAML ``_from_defaults`` key never opts a step in."""
    from_defaults = bool(step.pop(FROM_DEFAULTS, False))
    step.pop(_USER_STAMP, None)
    return from_defaults


def default_pipeline_steps() -> tuple[dict[str, Any], ...]:
    """The steps layer rows declare as ``default_steps``: what a run gets when its pipeline names none of them."""
    from mas.runtime.layers.compose import layers_declaring

    steps: list[dict[str, Any]] = []
    for name, declared in layers_declaring("default_steps"):
        for step in declared if isinstance(declared, list) else []:
            if isinstance(step, dict):
                steps.append({"layer": name, **step})
    return tuple(steps)


def merge_default_steps(pipeline: list[dict[str, Any]] | None, kind: str) -> list[dict[str, Any]]:
    """Prepend the default steps for *kind* that the pipeline does not already name."""
    from mas.runtime.layers.compose import layer_call_kinds, layer_id

    steps = [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
    want = str(kind or "").strip().lower()
    extra = [dict(step) for step in default_pipeline_steps() if want in layer_call_kinds(step)]
    have = {layer_id(step) for step in steps if want in layer_call_kinds(step)}
    missing = []
    for step in extra:
        if layer_id(step) in have:
            continue
        stamped = dict(step)
        stamped[FROM_DEFAULTS] = True
        missing.append(stamped)
    return missing + steps

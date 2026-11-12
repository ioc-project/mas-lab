#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Documented default retry steps. Loaded from YAML, not from a hidden loop."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

_DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults.yaml"
FROM_DEFAULTS = object()
_USER_STAMP = "_from_defaults"


def drop_default_stamp(step: dict[str, Any]) -> bool:
    """Pop the merge stamp. A YAML ``_from_defaults`` key never opts a step in."""
    from_defaults = bool(step.pop(FROM_DEFAULTS, False))
    step.pop(_USER_STAMP, None)
    return from_defaults


@cache
def default_pipeline_steps() -> tuple[dict[str, Any], ...]:
    """Retry steps shipped next to this module. Empty if the file is gone (tests fail)."""
    import yaml

    raw = yaml.safe_load(_DEFAULTS_PATH.read_text(encoding="utf-8")) or {}
    steps = raw.get("pipeline") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return ()
    return tuple(dict(step) for step in steps if isinstance(step, dict))


def merge_default_retry(pipeline: list[dict[str, Any]] | None, kind: str) -> list[dict[str, Any]]:
    """Prepend the documented default retry for *kind* when the pipeline has none."""
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

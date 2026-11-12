#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Default retry must not be written into tutorial YAML or wrap-time-only steps."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.runtime.layers.compose import layer_id
from mas.runtime.layers.defaults import merge_default_retry

_REPO = Path(__file__).resolve().parents[3]
_TUTORIALS = _REPO / "docs" / "tutorials"


def test_wrap_time_retry_is_not_empty() -> None:
    wrapped = merge_default_retry([], "llm")
    assert any(layer_id(step) == "retry" for step in wrapped)


def test_shipped_tutorial_yaml_does_not_list_retry() -> None:
    listed = []
    for path in _TUTORIALS.rglob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        spec = raw.get("spec") if isinstance(raw.get("spec"), dict) else {}
        pipeline = spec.get("pipeline") or []
        if any(isinstance(step, dict) and layer_id(step) == "retry" for step in pipeline):
            listed.append(str(path.relative_to(_REPO)))
    assert listed == []

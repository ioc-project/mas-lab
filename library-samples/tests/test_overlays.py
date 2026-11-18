#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The sample overlays set design-pattern options where the pattern reads them."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_OVERLAYS = sorted((Path(__file__).resolve().parents[1] / "overlays").glob("*.yaml"))


def _design_patterns(node):
    if isinstance(node, dict):
        pattern = node.get("design_pattern")
        if isinstance(pattern, dict):
            yield pattern
        for value in node.values():
            yield from _design_patterns(value)
    elif isinstance(node, list):
        for item in node:
            yield from _design_patterns(item)


@pytest.mark.parametrize("path", _OVERLAYS, ids=lambda p: p.stem)
def test_a_design_pattern_overlay_passes_its_options_as_params(path: Path) -> None:
    """``config:`` is not a design-pattern key, so options under it were silently ignored."""
    for pattern in _design_patterns(yaml.safe_load(path.read_text(encoding="utf-8"))):
        assert "config" not in pattern, f"{path.name}: use params:, not config:"

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Each ``<pattern>-moderator`` overlay must select the design pattern its name says."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_OVERLAYS = Path(__file__).resolve().parents[1] / "overlays"
_NAMES = ["cot", "react", "reflection"]


@pytest.mark.parametrize("pattern", _NAMES)
def test_the_overlay_selects_the_pattern_it_is_named_after(pattern: str) -> None:
    doc = yaml.safe_load((_OVERLAYS / f"{pattern}-moderator.yaml").read_text())
    dp = doc["spec"]["patch"]["agents"]["moderator"]["design_pattern"]
    assert dp["type"] == pattern
    assert dp["params"]["max_steps"] == 40


@pytest.mark.parametrize("pattern", _NAMES)
def test_the_overlay_patch_becomes_the_agents_pattern_and_step_cap(pattern: str) -> None:
    """The patched ``design_pattern`` is what the runtime reads: the kernel picks the pattern and the cap."""
    from mas.runtime.spec.parser import parse_agent_spec

    doc = yaml.safe_load((_OVERLAYS / f"{pattern}-moderator.yaml").read_text())
    patch = doc["spec"]["patch"]["agents"]["moderator"]
    config, _obs = parse_agent_spec(patch, agent_id="moderator")
    assert config.max_auto_steps == 40
    assert config.pattern_plugin_id == pattern

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for _load_design_pattern_plugin (registry-backed design-pattern
resolution introduced by the registry refactor)."""

from __future__ import annotations

import pytest
from mas.runtime.kernel.orchestrator import _load_design_pattern_plugin
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin


def test_explicit_pattern_id_loads_plugin() -> None:
    plugin = _load_design_pattern_plugin("react")
    assert isinstance(plugin, DesignPatternPlugin)


def test_none_falls_back_to_registry_default() -> None:
    plugin = _load_design_pattern_plugin(None)
    assert isinstance(plugin, DesignPatternPlugin)


def test_unknown_pattern_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        _load_design_pattern_plugin("definitely-not-a-real-pattern")

#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the @plugin decorator (F15)."""

from mas.runtime.contracts.base import BasePlugin, plugin
from mas.runtime.contracts.memory_contract import MemoryContract
from mas.runtime.contracts.tool_contract import ToolContract

# ── Auto-inference tests ──────────────────────────────────────────────────


class _DummyToolForDecorator(ToolContract):
    def get_name(self):
        return "dummy"

    def get_description(self):
        return "A dummy tool for testing"

    def get_parameters_schema(self):
        return {}

    def execute(self, **kwargs):
        return {"ok": True}


@plugin
class DecoratedTool(_DummyToolForDecorator):
    pass


def test_plugin_auto_plugin_id():
    """@plugin should auto-generate snake_case plugin_id from class name."""
    assert DecoratedTool.plugin_id == "decorated@v1"


def test_plugin_auto_implements():
    """@plugin should auto-infer implements=['tool'] from ToolContract parent."""
    assert "tool" in DecoratedTool.implements


def test_plugin_defaults_requires_empty():
    """Without explicit requires, @plugin leaves it empty."""
    assert DecoratedTool.requires == []


def test_plugin_defaults_governed_by_empty():
    """Without explicit governed_by, @plugin leaves it empty."""
    assert DecoratedTool.governed_by == []


# ── Explicit overrides ───────────────────────────────────────────────────


@plugin(requires=["recorder"], governed_by=["budget"])
class OverriddenTool(_DummyToolForDecorator):
    pass


def test_plugin_explicit_requires():
    assert OverriddenTool.requires == ["recorder"]


def test_plugin_explicit_governed_by():
    assert OverriddenTool.governed_by == ["budget"]


def test_plugin_explicit_overrides_still_infer_implements():
    """Even with explicit requires/governed_by, implements is still inferred."""
    assert "tool" in OverriddenTool.implements


# ── Existing ClassVars not overwritten ───────────────────────────────────


@plugin
class PresetIdTool(_DummyToolForDecorator):
    plugin_id = "my_custom_id@v2"
    implements = ["tool", "custom"]


def test_plugin_preserves_existing_plugin_id():
    """@plugin should not overwrite an explicitly set plugin_id."""
    assert PresetIdTool.plugin_id == "my_custom_id@v2"


def test_plugin_preserves_existing_implements():
    """@plugin should not overwrite explicitly set implements."""
    assert PresetIdTool.implements == ["tool", "custom"]


# ── CamelCase to snake_case conversion ───────────────────────────────────


@plugin
class MyHTTPClientPlugin(BasePlugin):
    pass


def test_plugin_camelcase_conversion():
    """Complex CamelCase names are converted to snake_case."""
    # MyHTTPClientPlugin → my_http_client (with _plugin stripped)
    pid = MyHTTPClientPlugin.plugin_id
    assert pid is not None
    assert "@v1" in pid
    # Should not contain double underscores
    name = pid.split("@")[0]
    assert "__" not in name


@plugin
class SimplePlugin(BasePlugin):
    pass


def test_plugin_strips_plugin_suffix():
    """Trailing 'Plugin' suffix is stripped from the auto-generated ID."""
    assert SimplePlugin.plugin_id == "simple@v1"


@plugin
class DateTimeTool(_DummyToolForDecorator):
    pass


def test_plugin_strips_tool_suffix():
    """Trailing 'Tool' suffix is stripped from the auto-generated ID."""
    assert DateTimeTool.plugin_id == "date_time@v1"


# ── Multi-contract inference ─────────────────────────────────────────────


@plugin
class MultiContractPlugin(ToolContract, MemoryContract):
    def get_name(self):
        return "multi"

    def execute(self, **kwargs):
        return {}


def test_plugin_multi_contract_inference():
    """@plugin should infer implements from ALL contracts in the MRO."""
    impl = MultiContractPlugin.implements
    assert "tool" in impl
    assert "memory" in impl

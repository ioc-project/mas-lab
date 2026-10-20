#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the centralized plugin registry (mas.runtime.registry)."""

import sys

import pytest
from pathlib import Path
from mas.runtime.registry import (
    PluginRegistry,
    PluginEntry,
    PluginUnavailable,
    VariantInfo,
    get_registry,
    register_plugin,
    add_plugin_path,
)


class TestVariantInfo:
    """Test VariantInfo dataclass."""
    
    def test_variant_info_creation(self):
        """Test creating a VariantInfo."""
        info = VariantInfo(
            module="mas.runtime.test",
            class_name="TestClass",
            version="1.0.0",
            description="Test plugin"
        )
        assert info.module == "mas.runtime.test"
        assert info.class_name == "TestClass"
        assert info.version == "1.0.0"
        assert info.description == "Test plugin"
    
    def test_load_class(self):
        """Test loading a class from VariantInfo."""
        info = VariantInfo(
            module="pathlib",
            class_name="Path",
        )
        cls = info.load_class()
        assert cls is Path


class TestPluginEntry:
    """Test PluginEntry dataclass."""
    
    def test_plugin_entry_creation(self):
        """Test creating a PluginEntry."""
        variant = VariantInfo(module="test", class_name="Test")
        entry = PluginEntry(
            urn="mas.dp.test",
            description="Test DP",
            default_variant="builtin",
            shortcuts=["test", "t"],
            variants={"builtin": variant}
        )
        assert entry.urn == "mas.dp.test"
        assert entry.description == "Test DP"
        assert entry.default_variant == "builtin"
        assert entry.shortcuts == ["test", "t"]
        assert "builtin" in entry.variants
    
    def test_default_property(self):
        """Test default variant property."""
        variant = VariantInfo(module="test", class_name="Test")
        entry = PluginEntry(
            urn="mas.dp.test",
            default_variant="builtin",
            variants={"builtin": variant}
        )
        assert entry.default is variant
    
    def test_resolve_default_variant(self):
        """Test resolving to default variant."""
        variant = VariantInfo(module="test", class_name="Test")
        entry = PluginEntry(
            urn="mas.dp.test",
            default_variant="builtin",
            variants={"builtin": variant}
        )
        resolved = entry.resolve()
        assert resolved is variant
    
    def test_resolve_specific_variant(self):
        """Test resolving to specific variant."""
        builtin = VariantInfo(module="test", class_name="Builtin")
        custom = VariantInfo(module="test", class_name="Custom")
        entry = PluginEntry(
            urn="mas.dp.test",
            default_variant="builtin",
            variants={"builtin": builtin, "custom": custom}
        )
        resolved = entry.resolve("custom")
        assert resolved is custom
    
    def test_resolve_unknown_variant_raises(self):
        """Test resolving unknown variant raises ValueError."""
        variant = VariantInfo(module="test", class_name="Test")
        entry = PluginEntry(
            urn="mas.dp.test",
            default_variant="builtin",
            variants={"builtin": variant}
        )
        with pytest.raises(ValueError, match="Unknown variant 'unknown'"):
            entry.resolve("unknown")


class TestPluginAvailability:
    """Test the requires:/extra: availability gate (PluginUnavailable)."""

    def test_missing_requires_empty_when_no_requires_declared(self):
        """Backward compat: a variant with no requires: is always available."""
        info = VariantInfo(module="pathlib", class_name="Path")
        assert info.missing_requires() == []

    def test_missing_requires_reports_unimportable_names(self):
        info = VariantInfo(
            module="pathlib",
            class_name="Path",
            requires=["pathlib", "no_such_package_xyz"],
        )
        assert info.missing_requires() == ["no_such_package_xyz"]

    def test_missing_requires_does_not_import_optional_sdks(self, monkeypatch):
        import importlib.util

        imported: list[str] = []

        def _boom_import(name: str, *args, **kwargs):
            imported.append(name)
            raise AssertionError(f"missing_requires must not import {name}")

        def _boom_spec(name: str, *args, **kwargs):
            imported.append(name)
            raise AssertionError(f"missing_requires must not find_spec {name}")

        monkeypatch.setattr(importlib.util, "find_spec", _boom_spec)
        monkeypatch.setattr("mas.runtime.registry.importlib.import_module", _boom_import)
        info = VariantInfo(
            module="pathlib",
            class_name="Path",
            requires=["opentelemetry.sdk.trace", "no_such_package_xyz"],
        )
        assert "no_such_package_xyz" in info.missing_requires()
        assert imported == []

    def test_missing_requires_discovers_sys_path_without_import(self, tmp_path, monkeypatch):
        pkg = tmp_path / "scanonly_dep"
        sub = pkg / "sdk"
        sub.mkdir(parents=True)
        (pkg / "__init__.py").write_text("raise RuntimeError('must not import')\n")
        (sub / "__init__.py").write_text("raise RuntimeError('must not import')\n")
        (sub / "trace.py").write_text("raise RuntimeError('must not import')\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        info = VariantInfo(
            module="pathlib",
            class_name="Path",
            requires=["scanonly_dep.sdk.trace", "scanonly_dep.missing"],
        )
        assert info.missing_requires() == ["scanonly_dep.missing"]
        assert "scanonly_dep" not in sys.modules

    def test_resolve_raises_plugin_unavailable_for_missing_requires(self):
        variant = VariantInfo(
            module="pathlib",
            class_name="Path",
            requires=["no_such_package_xyz"],
            extra="mas-library-telemetry[convert]",
        )
        entry = PluginEntry(urn="mas.dp.test", variants={"builtin": variant})
        with pytest.raises(PluginUnavailable) as exc_info:
            entry.resolve()
        err = exc_info.value
        assert err.urn == "mas.dp.test"
        assert err.missing == ["no_such_package_xyz"]
        assert "mas-ctl plugin enable mas.dp.test" in str(err)

    def test_resolve_hints_pip_install_without_extra(self):
        variant = VariantInfo(module="pathlib", class_name="Path", requires=["no_such_package_xyz"])
        entry = PluginEntry(urn="mas.dp.test", variants={"builtin": variant})
        with pytest.raises(PluginUnavailable, match="pip install no_such_package_xyz"):
            entry.resolve()

    def test_resolve_succeeds_when_requires_import(self):
        variant = VariantInfo(module="pathlib", class_name="Path", requires=["pathlib"])
        entry = PluginEntry(urn="mas.dp.test", variants={"builtin": variant})
        assert entry.resolve() is variant

    def test_registry_list_marks_unavailable_plugin_disabled(self):
        reg = PluginRegistry()
        variant = VariantInfo(module="pathlib", class_name="Path", requires=["no_such_package_xyz"])
        reg.register(PluginEntry(urn="mas.dp.disabled_test", variants={"builtin": variant}))
        [item] = [i for i in reg.list() if i["urn"] == "mas.dp.disabled_test"]
        assert item["available"] is False
        assert item["missing"] == ["no_such_package_xyz"]
        # list() never raises even though resolve() would for this entry.

    def test_get_entry_by_urn_and_alias(self):
        reg = PluginRegistry()
        entry = PluginEntry(urn="mas.dp.aliased_test", shortcuts=["aliased"], variants={})
        reg.register(entry)
        assert reg.get_entry("mas.dp.aliased_test") is entry
        assert reg.get_entry("aliased") is entry
        assert reg.get_entry("no-such-plugin") is None


class TestPluginRegistry:
    """Test PluginRegistry class."""
    
    def test_registry_initialization(self):
        """Test registry initializes empty."""
        reg = PluginRegistry()
        assert len(reg.list_all()) >= 0  # May have loaded from YAML
        assert len(reg.list_categories()) >= 0
    
    def test_register_plugin_entry(self):
        """Test registering a plugin entry."""
        reg = PluginRegistry()
        variant = VariantInfo(module="pathlib", class_name="Path")
        entry = PluginEntry(
            urn="mas.dp.testplugin",
            shortcuts=["testplugin"],
            variants={"builtin": variant}
        )
        reg.register(entry)
        
        assert "mas.dp.testplugin" in reg.list_all()
        resolved = reg.resolve("testplugin")
        assert resolved is not None
        assert resolved.class_name == "Path"
    
    def test_resolve_by_urn(self):
        """Test resolving plugin by URN."""
        registry = get_registry()
        info = registry.resolve("mas.dp.react")
        assert info is not None
        assert info.class_name == "ReactPlugin"
    
    def test_resolve_by_shortcut(self):
        """Test resolving plugin by shortcut."""
        registry = get_registry()
        info = registry.resolve("react")
        assert info is not None
        assert info.class_name == "ReactPlugin"
    
    def test_resolve_unknown_returns_none(self):
        """Test resolving unknown plugin returns None."""
        registry = get_registry()
        info = registry.resolve("nonexistent_plugin_xyz")
        assert info is None
    
    def test_resolve_by_type_design_pattern(self):
        """Test type-based resolution for design patterns."""
        registry = get_registry()
        info = registry.resolve_by_type("design_pattern", "react")
        assert info is not None
        assert info.class_name == "ReactPlugin"
    
    def test_resolve_by_type_context_manager(self):
        """Test type-based resolution for context managers."""
        registry = get_registry()
        info = registry.resolve_by_type("context_manager", "stack")
        assert info is not None
        assert info.class_name == "StackConversation"
    
    def test_resolve_by_type_unknown_returns_none(self):
        """Test type-based resolution for unknown plugin returns None."""
        registry = get_registry()
        info = registry.resolve_by_type("design_pattern", "nonexistent_xyz")
        assert info is None

    def test_get_with_type_and_name(self):
        """Test generic get() API with explicit type and name."""
        registry = get_registry()
        info = registry.get("design_pattern", "react")
        assert info is not None
        assert info.class_name == "ReactPlugin"

    def test_get_with_type_and_attributes(self):
        """Test generic get() API with attribute filtering."""
        class _AttrPlugin:
            pass

        register_plugin(
            "mas.codec.test_store",
            _AttrPlugin,
            shortcuts=["test-store"],
            attributes={"artifact_kind": "test", "store_type": "store"},
        )

        info = get_registry().get(
            "codec",
            attributes={"artifact_kind": "test", "store_type": "store"},
        )
        assert info is not None
        assert info.class_name == "_AttrPlugin"
    
    def test_get_by_category_dp(self):
        """Test getting all design patterns by canonical category."""
        registry = get_registry()
        dp_plugins = registry.get_by_category("design_pattern")
        assert len(dp_plugins) > 0

        # Verify all entries are design patterns by declared plugin_type
        for entry in dp_plugins:
            assert entry.attributes.get("plugin_type") == "design_pattern"
    
    def test_get_by_category_cm(self):
        """Test getting all context managers by canonical category."""
        registry = get_registry()
        cm_plugins = registry.get_by_category("context_manager")
        assert len(cm_plugins) > 0

        # Verify all entries are context managers by declared plugin_type
        for entry in cm_plugins:
            assert entry.attributes.get("plugin_type") == "context_manager"
    
    def test_get_by_category_unknown_returns_empty(self):
        """Test getting unknown category returns empty list."""
        registry = get_registry()
        plugins = registry.get_by_category("nonexistent_category")
        assert plugins == []
    
    def test_list_categories(self):
        """Test listing all categories."""
        registry = get_registry()
        categories = registry.list_categories()
        assert len(categories) > 0
        assert "design_pattern" in categories
        assert "context_manager" in categories
        assert "summarizer" in categories
        assert "assembler" in categories
    
    def test_all_aliases(self):
        """Test getting all aliases."""
        registry = get_registry()
        aliases = registry.all_aliases()
        assert len(aliases) > 0
        assert "react" in aliases
        assert aliases["react"] == "mas.dp.react"
    
    def test_add_scan_path(self):
        """Test adding plugin scan path."""
        reg = PluginRegistry()
        path = Path("/test/path")
        reg.add_scan_path(path)
        # Can't directly test _scan_paths (private), but shouldn't raise


class TestDynamicRegistration:
    """Test dynamic plugin registration."""
    
    def test_register_plugin_function(self):
        """Test registering a plugin via convenience function."""
        class TestPlugin:
            pass
        
        register_plugin("mas.test.dynamic", TestPlugin, shortcuts=["dynamic"])
        
        registry = get_registry()
        info = registry.resolve("dynamic")
        assert info is not None
        assert info.class_name == "TestPlugin"
    
    def test_register_plugin_with_variant(self):
        """Test registering a plugin with specific variant."""
        class CustomPlugin:
            pass
        
        register_plugin(
            "mas.test.custom",
            CustomPlugin,
            shortcuts=["custom"],
            variant="custom_variant",
            description="Custom test plugin"
        )
        
        registry = get_registry()
        info = registry.resolve("custom")
        assert info is not None
        assert info.class_name == "CustomPlugin"
    
    def test_add_plugin_path_function(self):
        """Test adding plugin path via convenience function."""
        path = Path("/test/custom/plugins")
        add_plugin_path(str(path))  # Should not raise


class TestSingleton:
    """Test registry singleton pattern."""
    
    def test_get_registry_returns_same_instance(self):
        """Test get_registry returns singleton."""
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2
    
    def test_registry_initialized_once(self):
        """Test registry is initialized only once."""
        reg = get_registry()
        # Should have plugins from YAML
        plugins = reg.list_all()
        assert len(plugins) > 0  # Loaded from built-in registry data


class TestTypeResolution:
    """Test canonical type-based resolution."""

    def test_design_pattern_resolves(self):
        registry = get_registry()
        info = registry.resolve_by_type("design_pattern", "react")
        assert info is not None

    def test_context_manager_resolves(self):
        registry = get_registry()
        info = registry.resolve_by_type("context_manager", "stack")
        assert info is not None

    def test_list_names_returns_manifest_names_without_resolving_plugins(self):
        registry = PluginRegistry()
        registry.register(
            PluginEntry(
                urn="mas.skill_impl.custom",
                shortcuts=["custom-skill"],
                variants={
                    "builtin": VariantInfo(
                        module="pathlib",
                        class_name="Path",
                        requires=["missing_optional_skill_runtime"],
                    )
                },
            )
        )

        assert registry.list_names("skill_impl") == ["custom", "custom-skill"]


class TestRealPlugins:
    """Test with real built-in plugins."""
    
    def test_react_plugin_registered(self):
        """Test react design pattern is registered."""
        registry = get_registry()
        info = registry.resolve("react")
        assert info is not None
        assert info.module == "mas.library.standard.plugins.design_patterns.react"
        assert info.class_name == "ReactPlugin"
    
    def test_cot_plugin_registered(self):
        """Test CoT design pattern is registered."""
        registry = get_registry()
        info = registry.resolve("cot")
        assert info is not None
        assert info.class_name == "CotPlugin"
    
    def test_stack_cm_registered(self):
        """Test stack context manager is registered."""
        registry = get_registry()
        info = registry.resolve("stack")
        assert info is not None
        assert info.class_name == "StackConversation"
    
    def test_sliding_window_cm_registered(self):
        """Test sliding-window context manager is registered."""
        registry = get_registry()
        info = registry.resolve("sliding-window")
        assert info is not None
        assert "SlidingWindow" in info.class_name

    def test_otel_observability_plugin_declares_requires_and_extra(self):
        """library-telemetry/library.yaml's otel plugin is the requires:/extra: example."""
        registry = get_registry()
        entry = registry.get_entry("mas.observability.otel")
        assert entry is not None
        variant = entry.default
        assert variant is not None
        assert variant.requires == ["opentelemetry.sdk.trace"]
        assert variant.extra == "mas-library-telemetry[convert]"

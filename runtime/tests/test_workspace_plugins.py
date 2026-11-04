#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from mas.runtime.harness.catalog import SPEC_IDENTITY_TYPES, WORKSPACE_PLUGIN_TYPES
from mas.runtime.registry import PluginEntry, PluginRegistry, VariantInfo
from mas.runtime.workspace_plugins import (
    WORKSPACE_PLUGIN_CATALOG,
    WorkspacePluginError,
    apply_workspace_plugins,
    catalog_comment_block,
    parse_plugin_refs,
)

_REPO = Path(__file__).resolve().parents[2]


def _registry() -> PluginRegistry:
    reg = PluginRegistry()
    builtin = VariantInfo(module="pathlib", class_name="Path")
    for urn, plugin_type, *rest in (
        ("mas.runtime.debug_script", "runtime", ["debug_script", "gdb"]),
        ("mas.checkpoint_store.hybrid", "checkpoint_store", ["hybrid"]),
        ("mas.checkpoint_store.disk", "checkpoint_store", ["disk"]),
        ("mas.observability.otel", "observability", ["otel"]),
        ("mas.gov.backtrack_on_error", "governance", ["backtrack_on_error"]),
        ("mas.control_protocol.rpc", "control_protocol", ["rpc"]),
        ("mas.dp.react", "design_pattern", ["react"]),
    ):
        shortcuts = rest[0] if rest else []
        reg.register(
            PluginEntry(
                urn=urn,
                shortcuts=list(shortcuts),
                attributes={"plugin_type": plugin_type},
                variants={"builtin": builtin},
            )
        )
    return reg


def test_parse_plugin_refs_empty_when_commented_or_false() -> None:
    assert parse_plugin_refs(None) == []
    assert parse_plugin_refs([]) == []
    assert parse_plugin_refs({"mas.runtime.debug_script": False}) == []
    assert parse_plugin_refs(["mas.runtime.debug_script", "mas.checkpoint_store.hybrid"]) == [
        "mas.runtime.debug_script",
        "mas.checkpoint_store.hybrid",
    ]


def test_apply_runtime_plugins_not_spec_slots() -> None:
    manifest = {"spec": {"models": [{"model": "gpt-4o-mini"}], "observability": ["native"]}}
    apply_workspace_plugins(
        manifest,
        [
            "mas.runtime.debug_script",
            "mas.checkpoint_store.hybrid",
            "mas.control_protocol.rpc",
        ],
        registry=_registry(),
    )
    spec = manifest["spec"]
    assert spec.get("debug") == {}
    assert "governance" not in spec
    assert spec["observability"] == ["native"]
    assert spec["checkpoint"]["storage"]["kind"] == "hybrid"
    assert spec["models"] == [{"model": "gpt-4o-mini"}]


def test_otel_and_governance_are_spec_slots() -> None:
    with pytest.raises(WorkspacePluginError, match="spec slot"):
        apply_workspace_plugins({"spec": {}}, ["mas.observability.otel"], registry=_registry())
    with pytest.raises(WorkspacePluginError, match="spec slot"):
        apply_workspace_plugins({"spec": {}}, ["mas.gov.backtrack_on_error"], registry=_registry())
    with pytest.raises(WorkspacePluginError, match="spec slot"):
        apply_workspace_plugins({"spec": {}}, ["mas.dp.react"], registry=_registry())


def test_spec_storage_and_debug_config_win() -> None:
    manifest = {
        "spec": {
            "debug": {"script_file": "./debug.gdb"},
            "checkpoint": {"mode": "every_turn", "storage": {"kind": "disk"}},
        }
    }
    apply_workspace_plugins(
        manifest,
        ["debug_script", "hybrid"],
        registry=_registry(),
    )
    spec = manifest["spec"]
    assert spec["debug"] == {"script_file": "./debug.gdb"}
    assert spec["checkpoint"]["storage"]["kind"] == "disk"


def test_unknown_plugin_is_rejected() -> None:
    with pytest.raises(WorkspacePluginError, match="unknown workspace plugin"):
        apply_workspace_plugins({"spec": {}}, ["mas.runtime.not_a_plugin"], registry=_registry())


def test_commented_init_template_enables_nothing() -> None:
    import yaml

    paths = [
        _REPO / "lab" / "components" / "core" / "src" / "mas" / "lab" / "templates" / "init" / "config.yaml",
        _REPO / "library-samples" / "sample-workspace" / "config.yaml",
    ]
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert parse_plugin_refs(data.get("plugins")) == []
        text = path.read_text(encoding="utf-8")
        for urn, _note in WORKSPACE_PLUGIN_CATALOG:
            assert urn in text
            assert f"#   - {urn}" in text
        assert "mas.observability.otel" not in text
        assert "mas.gov.backtrack_on_error" not in text


def test_catalog_comment_block_lists_runtime_overlays_only() -> None:
    block = catalog_comment_block()
    assert "Uncomment" in block or "uncomment" in block
    for urn, _note in WORKSPACE_PLUGIN_CATALOG:
        assert urn in block
    assert "mas.dp.react" not in block
    assert "mas.observability.otel" not in block


def test_config_and_lab_schemas_accept_overlay_plugin_lists() -> None:
    pytest.importorskip("jsonschema")
    from jsonschema import Draft7Validator
    from mas.ctl.validate.schemas import load_schema

    config_errors = list(
        Draft7Validator(load_schema("config")).iter_errors(
            {"plugins": ["mas.runtime.debug_script", "mas.checkpoint_store.hybrid"]}
        )
    )
    assert config_errors == []
    lab_errors = list(
        Draft7Validator(load_schema("lab_config")).iter_errors(
            {
                "lab": {
                    "name": "control",
                    "enable_plugins": ["mas.runtime.debug_script"],
                }
            }
        )
    )
    assert lab_errors == []


def test_workspace_plugin_types_exclude_spec_identity() -> None:
    assert not (WORKSPACE_PLUGIN_TYPES & SPEC_IDENTITY_TYPES)
    assert "checkpoint_store" in WORKSPACE_PLUGIN_TYPES
    assert "runtime" in WORKSPACE_PLUGIN_TYPES
    assert "governance" in SPEC_IDENTITY_TYPES
    assert "observability" in SPEC_IDENTITY_TYPES
    assert "design_pattern" in SPEC_IDENTITY_TYPES

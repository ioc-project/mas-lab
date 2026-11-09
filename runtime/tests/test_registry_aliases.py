#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for runtime alias loading and overrides."""

from __future__ import annotations

from mas.runtime.registry import PluginEntry, PluginRegistry, VariantInfo
from mas.runtime.registry.aliases import (
    _load_alias_manifest,
    _load_alias_mapping,
    load_aliases,
    load_config_aliases,
    load_default_aliases,
    validate_alias_manifest,
)
from mas.runtime.registry.bootstrap import (
    load_registry,
    register_manifest_data,
    register_manifest_file,
)
from mas.runtime.workspace_config import RuntimeWorkspaceConfig


class BuildablePlugin:
    def __init__(self, **params: object) -> None:
        self.params = params


def test_default_aliases_loaded_from_package_data() -> None:
    aliases = load_default_aliases()

    assert aliases["react"] == "mas.dp.react"
    assert aliases["planner"] == "mas.dp.react"
    assert aliases["memory-semantic"] == "mas.mem.semantic"


def test_workspace_aliases_override_package_defaults(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        """aliases:
  react: mas.dp.cot
  custom_alias: mas.dp.react
""",
        encoding="utf-8",
    )

    config = RuntimeWorkspaceConfig.load(start=workspace)
    registry = load_registry(config)

    assert registry.urn_for("react") == "mas.dp.cot"
    assert registry.urn_for("custom_alias") == "mas.dp.react"
    assert registry.resolve("react").class_name == "CotPlugin"


def test_bootstrap_manifest_helpers_cover_fixpoint_and_file_loading(tmp_path) -> None:
    registry = PluginRegistry()
    manifest = {
        "types": ["codec"],
        "plugins": [
            {
                "type": "codec",
                "name": "sample",
                "module": "pathlib",
                "class": "Path",
                "shortcuts": ["sample-codec"],
                "provides_types": ["tool"],
            },
            {
                "type": "tool",
                "name": "helper",
                "module": "pathlib",
                "class": "Path",
            },
        ],
        "aliases": {"sample": "mas.codec.sample"},
        "defaults": {"codec": "sample"},
        "runtime_spec_keys": ["codec", "tool"],
    }

    register_manifest_data(registry, manifest)

    assert registry.default_for("codec") == "sample"
    assert "codec" in registry.runtime_spec_keys()
    assert "tool" in registry.runtime_spec_keys()
    assert registry.resolve("sample-codec") is not None
    assert registry.resolve("sample").class_name == "Path"
    assert registry.resolve_by_type("tool", "helper") is not None

    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        """types:
  - codec
plugins:
  - type: codec
    name: file_sample
    module: pathlib
    class: Path
aliases:
  file_sample: mas.codec.file_sample
""",
        encoding="utf-8",
    )

    file_registry = PluginRegistry()
    register_manifest_file(file_registry, manifest_file)
    assert file_registry.resolve("file_sample") is not None


def test_registry_create_uses_defaults_and_manifest_bindings() -> None:
    registry = PluginRegistry()
    registry.register(
        PluginEntry(
            urn="mas.codec.buildable",
            shortcuts=["buildable"],
            variants={
                "builtin": VariantInfo(module=__name__, class_name="BuildablePlugin"),
            },
            attributes={"plugin_type": "codec"},
        )
    )
    registry.set_default("codec", "buildable")

    default_instance = registry.create("codec")
    manifest_instance = registry.create(
        "codec",
        manifest={"spec": {"codec": {"ref": "buildable", "params": {"alpha": 1}}}},
    )

    assert default_instance.__class__.__name__ == "BuildablePlugin"
    assert manifest_instance.params == {"alpha": 1}


def test_alias_helpers_cover_nested_and_invalid_sources(tmp_path) -> None:
    assert _load_alias_mapping(None) == {}
    assert _load_alias_manifest({"aliases": {"x": "mas.dp.react"}}) == {"x": "mas.dp.react"}
    assert _load_alias_manifest({"spec": {"aliases": {"y": "mas.dp.cot"}}}) == {"y": "mas.dp.cot"}

    assert validate_alias_manifest({"react": "mas.dp.react"}, name="override") == {"react": "mas.dp.react"}

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        """aliases:
    override: mas.dp.cot
""",
        encoding="utf-8",
    )

    config = RuntimeWorkspaceConfig.load(start=workspace)
    assert load_config_aliases(config) == {"override": "mas.dp.cot"}
    assert load_aliases(config)["override"] == "mas.dp.cot"


def test_validate_alias_manifest_rejects_invalid_value() -> None:
    try:
        validate_alias_manifest({"react": 123}, name="bad")
    except ValueError as exc:
        assert "invalid alias manifest" in str(exc)
    else:
        raise AssertionError("expected validation error")

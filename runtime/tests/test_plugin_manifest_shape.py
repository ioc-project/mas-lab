#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plugin manifest shape: plugins: is a list, never a type-keyed mapping."""

from __future__ import annotations

import pytest
from mas.runtime.registry import PluginRegistry
from mas.runtime.registry.bootstrap import register_manifest_data


def _library_envelope(**extra):
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Library",
        "name": "mas-library-x",
        "version": "0.1.0",
    }
    doc.update(extra)
    return doc


def test_schema_rejects_plugins_keyed_by_type() -> None:
    from mas.ctl.validate import validate_data

    result = validate_data(
        _library_envelope(
            types=["artifact"],
            plugins={
                "artifact": [
                    {
                        "name": "plot",
                        "module": "pathlib",
                        "class": "Path",
                    }
                ]
            },
        ),
        kind="library",
        strict=True,
        resolve_refs=False,
    )
    assert not result.ok
    messages = " ".join(i.message for i in result.issues if i.level == "error")
    assert "array" in messages


def test_schema_rejects_plugin_entry_without_type() -> None:
    from mas.ctl.validate import validate_data

    result = validate_data(
        _library_envelope(
            types=["artifact"],
            plugins=[
                {
                    "name": "plot",
                    "module": "pathlib",
                    "class": "Path",
                }
            ],
        ),
        kind="library",
        strict=True,
        resolve_refs=False,
    )
    assert not result.ok
    messages = " ".join(i.message for i in result.issues if i.level == "error")
    assert "type" in messages


def test_schema_accepts_flat_plugin_list() -> None:
    from mas.ctl.validate import validate_data

    result = validate_data(
        _library_envelope(
            types=["artifact"],
            plugins=[
                {
                    "type": "artifact",
                    "name": "plot",
                    "module": "pathlib",
                    "class": "Path",
                }
            ],
        ),
        kind="library",
        strict=True,
        resolve_refs=False,
    )
    assert result.ok, result.issues


def test_bootstrap_rejects_plugins_keyed_by_type() -> None:
    registry = PluginRegistry()
    with pytest.raises(ValueError, match="must be a list"):
        register_manifest_data(
            registry,
            {
                "types": ["artifact"],
                "plugins": {
                    "artifact": [
                        {
                            "name": "plot",
                            "module": "pathlib",
                            "class": "Path",
                        }
                    ]
                },
            },
        )


def test_bootstrap_registers_flat_plugin_list() -> None:
    registry = PluginRegistry()
    register_manifest_data(
        registry,
        {
            "types": ["artifact"],
            "plugins": [
                {
                    "type": "artifact",
                    "name": "plot",
                    "module": "pathlib",
                    "class": "Path",
                }
            ],
        },
    )
    assert registry.resolve("mas.artifact.plot") is not None

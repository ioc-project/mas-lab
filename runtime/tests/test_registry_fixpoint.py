#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.runtime.registry import (
    PluginRegistry,
    VariantInfo,
)
from mas.runtime.registry.bootstrap import (
    _ManifestPluginCandidate,
    _register_candidates_fixpoint,
    register_manifest_data,
)


def _v() -> dict[str, VariantInfo]:
    return {"builtin": VariantInfo(module="pathlib", class_name="Path")}


def test_fixpoint_registers_plugins_after_new_type_is_provided() -> None:
    registry = PluginRegistry()
    known_types = {"design_pattern"}
    candidates = [
        _ManifestPluginCandidate(
            plugin_type="design_pattern",
            urn="mas.dp.provider",
            default_variant="builtin",
            variants=_v(),
            provides_types={"codec"},
        ),
        _ManifestPluginCandidate(
            plugin_type="codec",
            urn="mas.codec.consumer",
            default_variant="builtin",
            variants=_v(),
        ),
    ]

    _register_candidates_fixpoint(registry, known_types, candidates)

    assert registry.resolve("mas.dp.provider") is not None
    assert registry.resolve("mas.codec.consumer") is not None


def test_fixpoint_supports_dynamic_non_core_type() -> None:
    registry = PluginRegistry()
    known_types = {"design_pattern"}
    candidates = [
        _ManifestPluginCandidate(
            plugin_type="design_pattern",
            urn="mas.dp.observability_provider",
            default_variant="builtin",
            variants=_v(),
            provides_types={"observability"},
        ),
        _ManifestPluginCandidate(
            plugin_type="observability",
            urn="mas.observability.sample",
            default_variant="builtin",
            variants=_v(),
            shortcuts=["obs-sample"],
        ),
    ]

    _register_candidates_fixpoint(registry, known_types, candidates)

    assert registry.resolve_by_type("observability", "sample") is not None
    assert registry.resolve("obs-sample") is not None


# ---------------------------------------------------------------------------
# End-to-end through register_manifest_data() -- the real production entry
# point (register_manifest_file() just adds a YAML read on top of this).
# These exist because _register_candidates_fixpoint() alone can't catch a
# regression in _parse_generic_manifest()'s own known-type seeding: an
# earlier version of that function auto-added every candidate's own `type:`
# to known_types while parsing, which made the fixpoint gate (and therefore
# `provides_types`) a no-op in practice -- a typo'd `type:` would silently
# become a brand new "known" category instead of failing.
# ---------------------------------------------------------------------------


def test_manifest_data_rejects_type_not_declared_or_provided() -> None:
    """A plugin whose type is neither builtin, listed in `types:`, nor
    unlocked by another candidate's `provides_types` must fail loudly."""
    registry = PluginRegistry()
    manifest = {
        "plugins": [
            {
                "type": "totally_unknown_type",
                "name": "orphan",
                "module": "pathlib",
                "class": "Path",
            }
        ]
    }
    with pytest.raises(ValueError, match="Unresolved plugin manifest entries"):
        register_manifest_data(registry, manifest)


def test_manifest_data_type_must_be_declared_not_just_present() -> None:
    """Merely appearing in `plugins:` must NOT be enough to make a type
    'known' -- it needs an explicit `types:` entry, a builtin type, or a
    provides_types unlock. Regression test for the known-types auto-seed bug."""
    registry = PluginRegistry()
    manifest = {
        # No top-level `types:` and no provides_types anywhere -- nothing
        # ever legitimizes "widget", so this must be rejected even though
        # a candidate declares `type: widget` right here in `plugins:`.
        "plugins": [
            {
                "type": "widget",
                "name": "gizmo",
                "module": "pathlib",
                "class": "Path",
            }
        ]
    }
    with pytest.raises(ValueError, match="Unresolved plugin manifest entries"):
        register_manifest_data(registry, manifest)


def test_manifest_data_provides_types_unlocks_sibling_candidate() -> None:
    """A plugin manifest where one entry's provides_types unlocks another
    entry's otherwise-undeclared type, resolved end-to-end through the real
    manifest parser (not just the low-level fixpoint helper)."""
    registry = PluginRegistry()
    manifest = {
        "types": ["design_pattern"],
        "plugins": [
            {
                "type": "design_pattern",
                "name": "widget_provider",
                "urn": "mas.dp.widget_provider",
                "module": "pathlib",
                "class": "Path",
                "provides_types": ["widget"],
            },
            {
                "type": "widget",
                "name": "gizmo",
                "urn": "mas.widget.gizmo",
                "module": "pathlib",
                "class": "Path",
            },
        ],
    }

    register_manifest_data(registry, manifest)

    assert registry.resolve("mas.dp.widget_provider") is not None
    assert registry.resolve("mas.widget.gizmo") is not None


def test_manifest_data_explicit_types_list_is_sufficient() -> None:
    """A manifest can pre-declare `types:` up front instead of relying on
    provides_types -- both are valid ways to make a type "known"."""
    registry = PluginRegistry()
    manifest = {
        "types": ["widget"],
        "plugins": [
            {
                "type": "widget",
                "name": "gizmo",
                "urn": "mas.widget.gizmo2",
                "module": "pathlib",
                "class": "Path",
            },
        ],
    }

    register_manifest_data(registry, manifest)

    assert registry.resolve("mas.widget.gizmo2") is not None

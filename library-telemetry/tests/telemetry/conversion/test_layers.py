#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ExportLayers filtering logic."""

from __future__ import annotations

from mas.library.telemetry.conversion.layers import (
    ExportLayers,
    layer_for_kind,
    parse_export_layers,
    should_export_event,
)


def test_default_layers_enabled() -> None:
    layers = ExportLayers()
    assert layers.structure is True
    assert layers.execution is True
    assert layers.semantic is True
    assert layers.provenance is True
    assert layers.governance is False


def test_enabled_returns_false_for_unknown_layer() -> None:
    layers = ExportLayers()
    assert layers.enabled("nonexistent") is True


def test_parse_export_layers_empty_cfg() -> None:
    layers = parse_export_layers({})
    assert layers == ExportLayers()


def test_parse_export_layers_disable_semantic() -> None:
    layers = parse_export_layers({"semantic": False})
    assert layers.semantic is False
    assert layers.structure is True


def test_parse_export_layers_enable_governance() -> None:
    layers = parse_export_layers({"governance": True})
    assert layers.governance is True


def test_parse_export_layers_nested_export_layers_key() -> None:
    layers = parse_export_layers({"export_layers": {"provenance": True}})
    assert layers.provenance is True


def test_parse_export_layers_alias_structural() -> None:
    layers = parse_export_layers({"structural": False})
    assert layers.structure is False


def test_parse_export_layers_none() -> None:
    assert parse_export_layers(None) == ExportLayers()


def test_layer_for_kind_execution_start() -> None:
    assert layer_for_kind("execution_start") == "execution"


def test_layer_for_kind_mas_call_start() -> None:
    assert layer_for_kind("mas_call_start") == "structure"


def test_layer_for_kind_unknown() -> None:
    assert layer_for_kind("completely_unknown_kind") is None


def test_should_export_execution_event_by_default() -> None:
    layers = ExportLayers()
    assert should_export_event({"kind": "execution_start"}, layers) is True


def test_should_export_structure_event_by_default() -> None:
    layers = ExportLayers()
    assert should_export_event({"kind": "mas_call_start"}, layers) is True


def test_governance_suppressed_by_default() -> None:
    layers = ExportLayers()
    assert layer_for_kind("governance_checked") == "governance"
    assert should_export_event({"kind": "governance_checked"}, layers) is False


def test_governance_passes_when_enabled() -> None:
    layers = ExportLayers(governance=True)
    assert should_export_event({"kind": "governance_checked"}, layers) is True


def test_provenance_enabled_by_default() -> None:
    layers = ExportLayers()
    from mas.library.telemetry.conversion.envelope import (
        BLOCK_TO_EXPORT_LAYER,
        KIND_ENVELOPE,
    )

    provenance_kinds = [k for k, v in KIND_ENVELOPE.items() if BLOCK_TO_EXPORT_LAYER.get(v[0]) == "provenance"]
    assert provenance_kinds
    ev = {"kind": provenance_kinds[0]}
    assert should_export_event(ev, layers) is True


def test_provenance_suppressed_when_disabled() -> None:
    layers = ExportLayers(provenance=False)
    from mas.library.telemetry.conversion.envelope import (
        BLOCK_TO_EXPORT_LAYER,
        KIND_ENVELOPE,
    )

    provenance_kinds = [k for k, v in KIND_ENVELOPE.items() if BLOCK_TO_EXPORT_LAYER.get(v[0]) == "provenance"]
    assert provenance_kinds
    ev = {"kind": provenance_kinds[0]}
    assert should_export_event(ev, layers) is False


def test_provenance_passes_when_enabled() -> None:
    layers = ExportLayers(provenance=True)
    from mas.library.telemetry.conversion.envelope import (
        BLOCK_TO_EXPORT_LAYER,
        KIND_ENVELOPE,
    )

    provenance_kinds = [k for k, v in KIND_ENVELOPE.items() if BLOCK_TO_EXPORT_LAYER.get(v[0]) == "provenance"]
    assert provenance_kinds
    ev = {"kind": provenance_kinds[0]}
    assert should_export_event(ev, layers) is True


def test_unknown_kind_passes_through() -> None:
    layers = ExportLayers(governance=False, provenance=False)
    ev = {"kind": "totally_unknown"}
    assert should_export_event(ev, layers) is True


def test_layer_field_on_event_overrides_kind_lookup() -> None:
    layers = ExportLayers(governance=False)
    ev = {"kind": "mas_call_start", "layer": "governance"}
    assert should_export_event(ev, layers) is False

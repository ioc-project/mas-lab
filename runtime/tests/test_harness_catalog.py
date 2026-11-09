#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import pytest
from mas.runtime.harness.catalog import (
    CyclicHarnessError,
    HarnessCatalog,
    HarnessComposition,
    IllegalHarnessLeafError,
    UnknownBoundarySlotError,
    assert_boundary_slot,
    default_catalog,
)
from mas.runtime.registry import PluginRegistry


def test_builtin_harness_dag_closes_on_kernel_ops() -> None:
    catalog = default_catalog()
    assert "evolution" in catalog
    assert "detective" in catalog.depends("evolution")
    leaves = catalog.uses_closed("evolution")
    assert "snapshot" in leaves
    assert "spec_delta" in leaves
    assert "pause" in leaves
    assert "httpx" not in leaves


def test_illegal_leaf_is_a_fourth_path() -> None:
    catalog = default_catalog()
    with pytest.raises(IllegalHarnessLeafError, match="fourth path"):
        catalog.register(HarnessComposition(name="sneaky", uses=("httpx",), description="nope"))


def test_cycle_is_rejected() -> None:
    catalog = HarnessCatalog()
    catalog.register(HarnessComposition(name="a", uses=("step",), requires=("b",)))
    with pytest.raises((CyclicHarnessError, IllegalHarnessLeafError, KeyError)):
        catalog.register(HarnessComposition(name="b", uses=("step",), requires=("a",)))
        catalog.depends("a")


def test_related_state_sandbox_are_library_not_boundary() -> None:
    from mas.runtime.harness.catalog import LIBRARY_TYPES, classify_plugin_type

    assert "related_state" in LIBRARY_TYPES
    assert "execute_sandbox" in LIBRARY_TYPES
    assert "circuit_breaker" in LIBRARY_TYPES
    assert "infra_middleware" in LIBRARY_TYPES
    assert "hitl_responder" in LIBRARY_TYPES
    assert "control_protocol" in LIBRARY_TYPES
    assert "checkpoint_store" in LIBRARY_TYPES
    assert "eval_metric" in LIBRARY_TYPES
    assert classify_plugin_type("related_state") == "library"
    assert classify_plugin_type("execute_sandbox") == "library"
    assert classify_plugin_type("circuit_breaker") == "library"
    assert classify_plugin_type("infra_middleware") == "library"
    assert classify_plugin_type("hitl_responder") == "library"
    assert classify_plugin_type("control_protocol") == "library"
    assert classify_plugin_type("checkpoint_store") == "library"
    with pytest.raises(UnknownBoundarySlotError):
        assert_boundary_slot("related_state")
    registry = PluginRegistry()
    registry.register_type("related_state")
    with pytest.raises(UnknownBoundarySlotError):
        registry.register_type("execute_sandbox", layer="boundary")
    with pytest.raises(UnknownBoundarySlotError):
        registry.register_type("circuit_breaker", layer="boundary")


def test_workspace_plugin_types_are_overlays_not_spec_identity() -> None:
    from mas.runtime.harness.catalog import SPEC_IDENTITY_TYPES, WORKSPACE_PLUGIN_TYPES

    assert "runtime" in WORKSPACE_PLUGIN_TYPES
    assert "checkpoint_store" in WORKSPACE_PLUGIN_TYPES
    assert "governance" in SPEC_IDENTITY_TYPES
    assert "observability" in SPEC_IDENTITY_TYPES
    assert "design_pattern" in SPEC_IDENTITY_TYPES
    assert not (WORKSPACE_PLUGIN_TYPES & SPEC_IDENTITY_TYPES)


def test_new_boundary_slot_is_rejected() -> None:
    with pytest.raises(UnknownBoundarySlotError, match="15th hook|closed"):
        assert_boundary_slot("pre_tool_use")
    registry = PluginRegistry()
    with pytest.raises(UnknownBoundarySlotError):
        registry.register_type("pre_tool_use", layer="boundary")
    registry.register_type("step")


def test_compact_is_a_context_slot_not_a_kernel_op() -> None:
    from mas.runtime.harness.catalog import BOUNDARY_SLOTS, KERNEL_OPS

    assert "compact" not in KERNEL_OPS
    assert "context_manager" in BOUNDARY_SLOTS
    assert "summarizer" in BOUNDARY_SLOTS

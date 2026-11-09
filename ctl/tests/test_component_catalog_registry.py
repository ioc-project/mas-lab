#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import pytest
from mas.ctl.compose.framework_registry import (
    get_framework_adapter,
    list_registered_adapters,
    register_framework_adapter,
)
from mas.ctl.compose.placement_registry import (
    get_placement_backend,
    list_registered_backends,
)
from mas.ctl.registry.catalog import (
    UnknownComponentError,
    get_component,
    get_framework,
    get_placement,
    validate_component_id,
    validate_framework_id,
    validate_placement_id,
)


def test_framework_and_placement_catalogs_are_authoritative() -> None:
    assert validate_framework_id("native") == "native"
    assert validate_framework_id("langgraph") == "langgraph"
    assert get_framework("crewai").status == "planned"
    with pytest.raises(UnknownComponentError, match="not available"):
        validate_framework_id("crewai")
    with pytest.raises(UnknownComponentError, match="not available"):
        get_framework_adapter("crewai")

    assert validate_placement_id("local-inproc") == "local-inproc"
    assert get_placement("docker").status == "planned"
    with pytest.raises(UnknownComponentError, match="not available"):
        validate_placement_id("docker")


def test_generic_catalog_accessors_match_typed_wrappers() -> None:
    assert get_component("framework", "native").id == get_framework("native").id
    assert validate_component_id("framework", "native") == "native"
    with pytest.raises(UnknownComponentError, match="not available"):
        validate_component_id("framework", "crewai")


def test_framework_and_placement_registries_seed_from_catalog() -> None:
    assert list_registered_adapters() == ["langgraph", "native"]
    assert get_framework_adapter("native").adapter_id == "native"
    assert list_registered_backends() == ["local-inproc"]
    assert get_placement_backend("local-inproc").name == "local-inproc"


def test_programmatic_framework_registration_still_overrides_registry() -> None:
    class CustomAdapter:
        adapter_id = "custom"

        def wrap(self, instance, bind, agent_id):
            return instance

    register_framework_adapter("custom", CustomAdapter())
    try:
        assert get_framework_adapter("custom").adapter_id == "custom"
    finally:
        from mas.ctl.compose import framework_registry

        framework_registry._ADAPTERS.pop("custom", None)

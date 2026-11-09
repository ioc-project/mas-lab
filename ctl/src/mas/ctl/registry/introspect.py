#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Runtime and ctl component registry introspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RegistryEntry:
    id: str
    layer: str
    description: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def list_design_patterns() -> list[RegistryEntry]:
    """Design patterns from the runtime registry (``spec.design_pattern``)."""
    from mas.runtime.registry import get_registry

    return [
        RegistryEntry(
            id=item["urn"],
            layer="runtime.design_pattern",
            description=item.get("description") or "",
            meta={"shortcuts": item.get("shortcuts", [])},
        )
        for item in get_registry().list("design_pattern")
    ]


def list_kernel_backends() -> list[RegistryEntry]:
    from mas.ctl.registry.catalog import list_runtimes

    return [
        RegistryEntry(
            id=e.id,
            layer=e.layer,
            description=e.description,
            meta={"status": e.status, "module": e.module or ""},
        )
        for e in list_runtimes()
    ]


def list_framework_adapters() -> list[RegistryEntry]:
    from mas.ctl.compose.framework_registry import list_registered_adapters

    return [RegistryEntry(id=k, layer="ctl.framework_adapter") for k in list_registered_adapters()]


def list_placement_backends() -> list[RegistryEntry]:
    from mas.ctl.compose.placement_registry import list_registered_backends as list_pb

    return [RegistryEntry(id=k, layer="ctl.placement") for k in list_pb()]


def list_compose_steps() -> list[RegistryEntry]:
    return [
        RegistryEntry(id="compose_application", layer="ctl.compose", description="MAS + overlays"),
        RegistryEntry(id="resolve_infra", layer="ctl.compose", description="infra_refs → endpoints"),
        RegistryEntry(id="compose_placement", layer="ctl.compose", description="deployment → plan"),
        RegistryEntry(id="compose_effective_bind", layer="ctl.compose", description="→ EffectiveBind"),
        RegistryEntry(id="materialize", layer="ctl.placement", description="plan → running instances"),
    ]


def list_schema_kinds() -> list[RegistryEntry]:
    from mas.ctl.validate.schemas import list_schema_kinds as kinds

    return [RegistryEntry(id=k, layer="ctl.schema", description="JSON Schema") for k in kinds()]


def list_runtime_machines() -> list[RegistryEntry]:
    return [
        RegistryEntry(id="M_lifecycle", layer="runtime.machine"),
        RegistryEntry(id="M_model", layer="runtime.machine"),
        RegistryEntry(id="M_tool", layer="runtime.machine"),
        RegistryEntry(id="M_ctx", layer="runtime.machine"),
        RegistryEntry(id="M_memory", layer="runtime.machine"),
        RegistryEntry(id="M_session", layer="runtime.machine"),
        RegistryEntry(id="M_transport", layer="runtime.machine"),
        RegistryEntry(id="M_gov", layer="runtime.boundary"),
        RegistryEntry(id="M_obs", layer="runtime.boundary"),
    ]


def list_all_components() -> list[RegistryEntry]:
    out: list[RegistryEntry] = []
    out.extend(list_runtime_machines())
    out.extend(list_design_patterns())
    out.extend(list_kernel_backends())
    out.extend(list_framework_adapters())
    out.extend(list_placement_backends())
    out.extend(list_compose_steps())
    out.extend(list_schema_kinds())
    return out


def query_registry(layer: str | None = None, id_prefix: str | None = None) -> list[RegistryEntry]:
    items = list_all_components()
    if layer:
        items = [e for e in items if e.layer == layer or e.layer.startswith(layer)]
    if id_prefix:
        items = [e for e in items if e.id.startswith(id_prefix)]
    return items

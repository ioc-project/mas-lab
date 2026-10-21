<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Thin waist: one registry per job

This page explains how the runtime turns a name in a manifest into code. Read it before you add a plugin type or a lookup table. Other terms are in the [glossary](../glossary.md).

A *plugin* is a unit of behaviour that a manifest selects by name. The *runtime* is the Python package that executes agents (`mas-runtime`).

## The problem

A manifest names behaviour, such as `governance: hitl`. Something must turn that name into code. If two tables map the same names, they drift apart.

The *thin waist* is the small, fixed set of lookup tables that every layer uses. Each name resolves in exactly one place.

```yaml
spec:
  governance: hitl      # resolved by PluginRegistry.resolve_by_type("governance", "hitl")
```

## PluginRegistry

`PluginRegistry` (`mas.runtime.registry`) maps a `name` to an implementation. It reads the manifest `spec.*` keys. Plugin types include `design_pattern`, `governance`, `llm_provider`, and `skill_impl`.

- Every plugin type loads through `PluginRegistry.resolve_by_type`.
- Skills use `skill_impl` rows in `library.yaml`.
- `SkillPluginRegistry` (library-skills) is a facade over `resolve_by_type("skill_impl", ...)`. It is not a second table.

*Boundary slots* are the plugin types that the kernel envelope calls at fixed points. See [Contracts](contracts.md). They form a closed set. `register_type(..., layer="boundary")` raises `UnknownBoundarySlotError` for a type outside that set.

Library types are not envelope slots. Examples: `related_state`, `execute_sandbox`, `circuit_breaker`, `skill_*`, and lab steps.

## Component catalog

The *embedder* is the program that hosts the runtime. It chooses the infra: runtime, placement, and framework. These resolve from [`component-registry.yaml`](../schemas/component-registry.yaml).

- Framework adapters and placement backends start empty and fill from the catalog.
- Unknown ids fail in `validate_component_id` and `get_framework_adapter`.
- Docker and Kubernetes placement stay `status: planned` until an availability plugin is declared.

## Control and snapshots

Pause, navigate, spec revision, and snapshot storage are `ControlContract` and `SnapshotTree` APIs. They are not plugin-type enums. See [Snapshots vs checkpoints](snapshots.md).

- The JSON-lines control wire is a library `control_protocol` plugin.
- A2A (Agent2Agent, the protocol for remote agents) session attach is `agent_expose`. The A2A `contextId` is the session id.
- A user turn on that session is `ControlContract.send_message` (A2A `message/send`).
- `AgentCommContract` sends outbound to a peer after delegation. It is not inbound user input.

## Why one registry per job

A third table that maps the same names would duplicate the first two. One registry per job keeps a name from resolving two ways.

## See also

- [Contracts](contracts.md): the boundary slots.
- [Snapshots vs checkpoints](snapshots.md)
- [Schema index](schemas.md)
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)

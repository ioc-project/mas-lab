<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent defaults

`mas-runtime` ships package-wide defaults for values a manifest can omit: the design pattern, the context manager
and the assembler. This page is for users who want to override those defaults and contributors who add one.

Override defaults in the workspace `config.yaml`:

```yaml
# config.yaml
defaults:
  model: gpt-4.1-mini
  design_pattern: cot
  context_manager: stack
  assembler: assembler
```

The `defaults:` block is validated against
[src/mas/runtime/defaults.schema.yaml](../src/mas/runtime/defaults.schema.yaml) and against the root
`config.schema.yaml`, which declares the same keys.

## Resolution order

1. Package defaults from [src/mas/runtime/defaults.yaml](../src/mas/runtime/defaults.yaml).
2. Workspace or user overrides from the `defaults:` block of `config.yaml`.

Overrides win per key. A workspace can override only `design_pattern` and still inherit the package's other keys.

## The model default is a workspace pin

`defaults.model` is not a live fallback.

- Compile records `model: any` when the Agent, MAS or experiment omits a pin.
- Named `experiment.models` slots fill matching `any` ids first.
- The `defaults.model` in the local `config.yaml` can resolve the remaining `any` at engine time.
- The package `defaults.yaml` `model` is not used as a substitute. If nothing explicit remains, engine
  construction raises instead of silently running `gpt-4o-mini`.

Pin a model in the Agent or MAS spec, `experiment.models.main`, workspace `config.yaml`, `--model`, or
`MAS_CTL_MODEL`. The value is exposed through `mas.runtime.agent_defaults.default_model()` and
`resolve_default_model(workspace=None)` for the UI and catalog. `model` is not a registry plugin type.

## How the other keys are used

`design_pattern`, `context_manager` and `assembler` are registry spec keys. On startup,
`bootstrap.load_registry()` reads `defaults.yaml` (merged with any `config.yaml` override) through
`mas.runtime.registry.defaults.load_defaults()` and calls `PluginRegistry.set_default(spec_key, plugin_id)` for
each. `default_pattern_plugin_id()`, `default_context_manager_id()` and `default_assembler_id()` read the values
back with `PluginRegistry.default_for(spec_key)`. That registry is the single source of truth for what an omitted
`spec.design_pattern` resolves to.

A plugin manifest (`library.yaml`, or a split-out `*.plugins.yaml`) can also declare its own `defaults:` block for
a plugin type it introduces; see [plugin-registry-manifests.md](plugin-registry-manifests.md). That block is scoped
to that manifest's types. This page covers the runtime-wide defaults.

## Notes for contributors

- Keep default values in package data (`defaults.yaml`), not in Python constants or registry bootstrap code.
- A new package-wide default key goes into both the `spec` properties of `defaults.schema.yaml` and the
  `defaults` properties of `config.schema.yaml`.
- Call `PluginRegistry.set_default()` in `bootstrap.load_registry()` only for keys that are also registry plugin
  types. Other keys (like `model`) get a plain accessor.
- The public API of `agent_defaults.py` is `default_model()`, `resolve_default_model(workspace=None)`,
  `default_pattern_plugin_id()`, `default_context_manager_id()` and `default_assembler_id()`.

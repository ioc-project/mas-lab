<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin aliases and discovery

`mas-runtime` resolves plugins by canonical URN (a unique identifier such as `mas.dp.react`). Manifests and CLI inputs may use shorter aliases instead.
This page is for manifest authors and contributors who add or override aliases. How plugins get registered is in [plugin-registry-manifests.md](plugin-registry-manifests.md).

Add a local alias in `config.yaml`:

```yaml
aliases:
  quick: mas.dp.cot
```

## Roles

| Component | Role |
| --- | --- |
| `mas-runtime` | Owns the runtime registry, default alias manifest, validation, and lookup. |
| `mas-library-standard` | Ships canonical built-in plugin implementations and the default alias data used by the runtime. |
| `config.yaml` | Workspace or user override file for aliases and other runtime configuration. |

## Naming

| Term | Example | Meaning |
| --- | --- | --- |
| Plugin name | `react` | Human-friendly alias used in manifests and CLI inputs. |
| URN | `mas.dp.react` | Canonical runtime identifier. |
| Alias manifest key | `react@v1` | Alternate alias that resolves to the same URN. |

The runtime registry normalises aliases to canonical URNs before lookup. If more than one plugin matches the same name, attribute filters are applied across all candidates.

## Discovery order

The runtime loads aliases from two sources:

1. Package defaults from [src/mas/runtime/aliases.yaml](../src/mas/runtime/aliases.yaml).
2. Workspace or user overrides from `config.yaml`.

Overrides win on key conflict, so local alias changes need no runtime code patch.

## Configuration

The example above uses a top-level `aliases:` mapping in `config.yaml`. More than one alias may point at a URN:

```yaml
aliases:
  quick: mas.dp.cot
  custom_alias: mas.dp.react
```

The runtime validates this mapping against [src/mas/runtime/aliases.schema.yaml](../src/mas/runtime/aliases.schema.yaml).

## Notes for contributors

- Keep alias defaults in package data, not in registry code.
- Prefer canonical URNs in new manifests and docs, and add aliases only for compatibility or ergonomics.
- When adding or changing alias keys, update the manifest, schema, and tests together.
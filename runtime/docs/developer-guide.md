<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-runtime Developer Guide

This guide is for developers who extend `mas-runtime` with plugins, tools and overlays without breaking contract and hook semantics.

`mas-runtime` is a library with no CLI of its own. `mas-ctl` wraps the builder and runtime APIs and must not own core orchestration logic.

## Extension points

Plugins belong **in a library** (`library.yaml`), not in a lab root and not
in a third top-level plugins folder. Discovery order:
[library-discovery.md](../../docs/library-discovery.md). User model:
[labs-and-libraries.md](../../docs/labs-and-libraries.md).

### Plugins

- Implement plugin interfaces using runtime contract expectations.
- Keep plugin responsibilities narrow (governance, memory, observability, tools, transport).
- Follow the naming conventions in [naming-standards.md](naming-standards.md).

### Tools

- Implement `ToolContract` with explicit input schema.
- Prefer deterministic side effects and idempotent external calls.
- Emit structured errors for policy and retry layers.

### Overlays and flavours

- Overlays compose behavior (reasoning or policy) without mutating base manifests.
- Flavours separate environment concerns from agent definitions.

## Dev workflow

1. Implement feature in `src/`.
2. Register/export in package entrypoints.
3. Add unit tests in `tests/` for happy path + failure path.
4. Validate with a `mas-ctl chat` smoke test.

## Testing guidance

- Contract tests for plugin hook order and payload integrity
- Tool tests for schema validation and error handling
- Integration tests for manifest+flavour loading
- Regression tests for trace and artifact shape

## API and contract references

- [dev/contracts/index.md](dev/contracts/index.md)
- [dev/contracts/taxonomy.md](dev/contracts/taxonomy.md)
- [dev/schemas/agent-manifest.md](dev/schemas/agent-manifest.md)
- [dev/schemas/tool-definition.md](dev/schemas/tool-definition.md)
- [dev/api-reference/mas-runtime-api.md](dev/api-reference/mas-runtime-api.md)

## Quality bar

- No hardcoded secrets
- No undocumented config keys
- Predictable behavior under retries/timeouts
- Backward-compatibility strategy documented when changing public contracts

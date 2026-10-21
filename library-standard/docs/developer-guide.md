<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-standard Developer Guide

This guide is for contributors who add plugins or tools to `mas-library-standard`. Each plugin or tool needs an implementation, a test and (for plugins) a card and manifest example; the steps below list them.

## Module map

- Plugins: `src/mas/library/standard/plugins/`
- Design patterns: `src/mas/library/standard/plugins/design_patterns/`
- Overlays: `src/mas/library/standard/overlays/` ([index](../src/mas/library/standard/overlays/README.md))
- Examples: `examples/<category>/` ([index](../examples/README.md); not sample apps)
- Tools: `src/mas/library/standard/tools/`
- Production tools: `src/mas/library/standard/tools/standard/`
- Tests: `tests/`

## Naming conventions

Use stable prefixes by capability:

- `dp_*`: design pattern
- `sk_*`: skill
- `ctx_*`: context
- `obs_*`: observability
- `llm_*`: model/provider
- `tool_server_*`: tool-server integration
- `tool_*`: tool wiring
- `tp_*`: transport
- `gov_*`: governance

## Add a plugin

1. Implement plugin in `plugins/`.
2. Keep one clear responsibility per plugin. Governance plugins are chain
   rules: pass (ALLOW) when they do not apply; BLOCK exits the chain with
   that error. Observability plugins are a sequence and always all run.
3. Document config keys and defaults. Add a plugin card next to the
   implementation and, for overlays, a row in
   [overlays/README.md](../src/mas/library/standard/overlays/README.md).
4. Add unit tests for success and failure paths. When the plugin exists to stop a specific failure mode, add a small Agent or MAS under `examples/<category>/` (see `examples/governance/undeclared-tool/` and `examples/context/summarizer-override/`). Real use-case MAS belong in `library-samples/apps/`, not here.

## Add a tool

1. Implement tool class in `tools/`.
2. Add companion semantic tool contract when required.
3. Export in `tools/__init__.py`.
4. Add tests for schema validation and runtime behavior.

## Compatibility checklist

- Public module path remains stable
- Export list updated for discoverability
- Manifest examples updated when new plugin is added
- Tests cover hooks/order-sensitive behavior

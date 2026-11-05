<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Runtime user guide

`mas-runtime` is the single-agent execution kernel of MAS Lab (MAS: multi-agent system). It runs one agent from a YAML manifest, with plugins, governance and design patterns. This guide is for teams that run and configure agents; you run them through `mas-ctl`.

## Install

`mas-runtime` is a library; run agents through `mas-ctl`. From a repository checkout (the PyPI upload is still pending):

```bash
uv sync --all-packages         # installs mas-ctl and the runtime library
```

## Quickstart

1. Create an `agent.yaml` manifest (`kind: Agent`) with a model and plugin declarations. See [docs/manifests/agent.md](../../docs/manifests/agent.md).
2. Run the agent interactively, or send a single prompt:

```bash
mas-ctl chat agent.yaml                       # interactive
mas-ctl chat agent.yaml -q "What is 2+2?"     # one scripted turn
```

3. Link the agents to infra with a flavour (its `bind` section; see [flavour.md](../../docs/manifests/flavour.md#bind)):

```bash
mas-ctl chat agent.yaml --flavour local
```

`--flavour` takes a bundled name (`local`) or a path to a Flavour file.

## Core concepts

- Contract: a Python interface the kernel calls (tool, context, memory, control, recorder, ...); see [contracts-reference.md](contracts-reference.md).
- Hook and chokepoint: a pre or post phase around LLM calls, tool calls and state updates; see [mealy-envelope.md](mealy-envelope.md).
- Plugin: an extension declared in a manifest and discovered from a library.
- Overlay: a partial manifest merged over a base manifest, used for reasoning patterns and variations.

## Common tasks

### Add a tool

- Write a `ToolContract` class and a `kind: Tool` manifest, then reference it from the agent's `spec.tools`. See [dev/schemas/tool-definition.md](dev/schemas/tool-definition.md).
- Document every input field; the schema is validated before the tool runs.

### Enable memory

- Use memory plugins from `library-standard` or a dedicated memory library.
- Set the persistence path and backend through flavour or plugin config.

### Apply reasoning pattern

- Add a design-pattern overlay (for example CoT or ReAct); see [design-patterns.md](design-patterns.md).
- Test behavior with controlled prompts before production rollout.

## Recommended docs

- [index.md](index.md)
- [contracts-reference.md](contracts-reference.md)
- [plugin-and-tool-authoring.md](plugin-and-tool-authoring.md)
- [dev/contracts/mealy-hooks-and-closure.md](dev/contracts/mealy-hooks-and-closure.md)
- [plugin-flavours.md](plugin-flavours.md)
- [design-patterns.md](design-patterns.md)

## Troubleshooting

- Missing plugin: verify the module path and that the providing library is installed (`mas-ctl plugin doctor`).
- Missing secret: check that the environment variable exists and the manifest references it by name.
- Unexpected tool calls: inspect the governance policy and the trace output (`--trace` on `mas-ctl chat`).

## Production checklist

- Flavour `bind` links each agent to the infra rows it should use (or none, for the in-process default)
- Governance policy enabled and tested
- Telemetry spans emitted and export path validated
- Integration tests cover failure paths and retries

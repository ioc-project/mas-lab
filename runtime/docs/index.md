<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-runtime documentation

`mas-runtime` is the embeddable single-agent kernel library of MAS Lab (MAS: multi-agent system).
These pages are for users who configure agents and for developers who extend the kernel with plugins.
The library has no CLI of its own (`mas-runtime run-agent` was removed); interactive and scripted runs go through `mas-ctl`.

Quick start, from the repository root:

```bash
uv sync --package mas-ctl                    # installs mas-ctl and mas-runtime
mas-ctl chat agent.yaml -q "What is 2+2?"    # one scripted turn
```

The PyPI upload is pending, so install from a checkout. Tutorials are under
[`docs/tutorials/`](../../docs/tutorials/index.md); runnable sample apps are in
[`library-samples/apps/`](../../library-samples/apps/).

## I want to...

| Task | Start here |
| --- | --- |
| Run an agent for the first time | [user-guide.md](user-guide.md), [Tutorial 01](../../docs/tutorials/01-building-an-agent/README.md) |
| Run a multi-agent manifest | `mas-ctl run-mas <manifest>`, [Tutorial 02](../../docs/tutorials/02-creating-a-mas/README.md) |
| Embed the kernel in my own host | [dev/api-reference/mas-runtime-api.md](dev/api-reference/mas-runtime-api.md) |
| Understand how the kernel relates to agent frameworks, tools and observability | [architecture-instrumentation.md](architecture-instrumentation.md) |
| Write a plugin or tool | [plugin-and-tool-authoring.md](plugin-and-tool-authoring.md), then [contracts-reference.md](contracts-reference.md) |
| Enable CoT, ReAct or Reflection | [design-patterns.md](design-patterns.md) |
| Configure dev versus prod settings | [plugin-flavours.md](plugin-flavours.md) |
| Understand plugin names, URNs and aliases | [plugin-aliases.md](plugin-aliases.md) |
| Understand how plugin types are registered | [plugin-registry-manifests.md](plugin-registry-manifests.md) |
| Change the default design pattern or context manager | [agent-defaults.md](agent-defaults.md) |
| Choose an agent, tool or resource identity scheme | [naming-standards.md](naming-standards.md) |

## Frequently asked questions

**Do I need LangChain or LangGraph?**
No. The default path is declarative manifests plus `mas-ctl chat` or `SessionController`.

**How do I add custom governance logic?**
Register governance plugins on the envelope chokepoints (sigma 2 and sigma 6), or implement `ToolContract` guards. `BudgetTracker` enforces budgets.
See [production-path.md](production-path.md) and [governance-policy-engine.md](governance-policy-engine.md).

**What is the difference between the remote agent transport and the kernel?**
The remote agent transport (A2A) coordinates agents across processes.
`RuntimeKernel` plus `SessionController` execute one agent as a Mealy product with contracts and plugins.

**Can I use several frameworks side by side?**
Plugins work through contracts, not framework APIs. Only the `native` framework adapter is supported in this release;
see [architecture-instrumentation.md](architecture-instrumentation.md).

## Reference for developers

| Document | Content |
| --- | --- |
| [architecture-instrumentation.md](architecture-instrumentation.md) | Kernel versus framework loops, adapters, tools, observability |
| [contracts-reference.md](contracts-reference.md) | Contract families, call paths, reading guide |
| [dev/contracts/index.md](dev/contracts/index.md) | Contract inventory with methods and links |
| [dev/contracts/taxonomy.md](dev/contracts/taxonomy.md) | Contract families and how to pick one |
| [dev/contracts/design-patterns.md](dev/contracts/design-patterns.md) | The design-pattern contract |
| [dev/contracts/mealy-hooks-and-closure.md](dev/contracts/mealy-hooks-and-closure.md) | Chokepoints, hooks, closure |
| [dev/contracts/messaging-and-orchestration.md](dev/contracts/messaging-and-orchestration.md) | Sensors, messaging, delegation, workflow |
| [dev/contracts/execution-control-and-observability.md](dev/contracts/execution-control-and-observability.md) | Control and recorder |
| [dev/contracts/model-and-tools.md](dev/contracts/model-and-tools.md) | Tool, prompt, memory, model contracts |
| [mealy-envelope.md](mealy-envelope.md) | Envelope hot path |
| [automaton-product-model.md](automaton-product-model.md) | The full agent product model |
| [trajectory-schema.md](trajectory-schema.md) | Span format and trace hierarchy |
| [context-segmentation.md](context-segmentation.md) | Context assembly and provenance |
| [semantic-protocols.md](semantic-protocols.md) | URNs and governance scopes |
| [dev/schemas/tool-definition.md](dev/schemas/tool-definition.md) | Tool class and manifest |
| [dev/schemas/agent-manifest.md](dev/schemas/agent-manifest.md), [dev/schemas/mas-topology.md](dev/schemas/mas-topology.md) | Pointers to the manifest references |
| [dev/api-reference/mas-runtime-api.md](dev/api-reference/mas-runtime-api.md) | Kernel, builder and session API |
| [dev/cli/cli-reference.md](dev/cli/cli-reference.md) | Pointer to the CLI references |

For `mas-lab` (benchmarks, pipelines) see [docs/libraries.md](../../docs/libraries.md).

## Contributing a page

1. Keep pages user-facing, integration-focused or developer reference.
2. Add the page to this index and fix cross-references.
3. Check that relative links resolve.
4. Make code examples executable and tested.

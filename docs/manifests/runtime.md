<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Runtime manifests (`mas-runtime`)

This page indexes the manifests that **mas-runtime** and **mas-ctl** consume.
It also documents the two bundle kinds that have no page of their own: `ToolBundle` and `PromptBundle`.
Use it to find the reference page and schema for a manifest kind.
The authoritative JSON Schemas are in [`docs/schemas/runtime/`](https://github.com/outshift-open/mas-lab/tree/main/docs/schemas/runtime).

`mas-ctl validate` checks agents, MAS, overlays, workflows, flavours, tools, bundles, and infra.
`mas-lab validate` checks experiments, pipelines, and lab configs.

This minimal PromptBundle shows the shape of a runtime manifest:

```yaml
apiVersion: mas/v1
kind: PromptBundle
metadata:
  name: support-prompts
spec:
  entries:
    greeter:
      type: system
      text: You are a concise support agent.
```

## Manifest index

| Manifest | Schema file | Reference page |
| ---------- | ------------- | ---------------- |
| Agent | `agent.schema.yaml` | [agent.md](agent.md), [plugin-bindings.md](plugin-bindings.md), [context-assembly.md](context-assembly.md) |
| MAS | `mas.schema.yaml` | [mas.md](mas.md) |
| Overlay | `overlay.schema.yaml` | [overlay.md](overlay.md) |
| Workflow topology | `workflow.schema.yaml` | [mas.md: Standalone workflow manifest](mas.md#standalone-workflow-manifest-kind-workflow) |
| Flavour | `flavour.schema.yaml` | [flavour.md](flavour.md) |
| Infrastructure | `infra.schema.yaml` | [infra.md](infra.md) |
| RuntimeEngine | `fragments/runtime-engine.schema.yaml` | [runtime-engine.md](runtime-engine.md) |
| Tool | `tool.schema.yaml` | [tool.md](tool.md) |
| ToolBundle | `tool_bundle.schema.yaml` | [ToolBundle](#toolbundle) |
| PromptBundle | `prompt_bundle.schema.yaml` | [PromptBundle](#promptbundle) |
| Workspace (`config.yaml`) | `config.schema.yaml` (in `docs/schemas/`) | [user-config.md](../user-config.md) |

The controller serves schemas at `GET /api/schemas/<id>`.
The ids are `agent`, `mas`, `overlay`, `infra`, `workflow`, `flavour`, `tool`, `tool-bundle`, and `prompt-bundle`.

## Tool family

| Kind | Purpose |
| ------ | --------- |
| `Tool` | Single tool definition (parameters, implementation ref) |
| `ToolBundle` | Bundle of tool entries |
| `PromptBundle` | Named prompt entries (system prompts, few-shot examples, intents) |

Agents reference tools by **semantic name** or by `tools[].ref` to a `kind: Tool` manifest ([tool.md](tool.md)).
Infra `ToolServerRegistry` maps remote endpoints ([infra.md](infra.md#toolserverregistry)).
Invocation goes through [`ToolContract.call_tool`](../references/tool-contract.md).

## PromptBundle

`kind: PromptBundle` uses [`prompt_bundle.schema.yaml`](../schemas/runtime/prompt_bundle.schema.yaml).
It requires `metadata.name` and `spec.entries`, a map from entry key to entry.
Each entry requires `type` (`system`, `few-shot`, or `intent`) and may contain `text`, `items`, or `tags`.
The schema does not require `text` or `items` conditionally on `type`.
The example at the top of this page is a complete PromptBundle.

### Not used yet

`mas-ctl validate` checks PromptBundle documents, but the runtime does not read them when it builds an agent.
`spec.context.<key>` takes inline text or a `{ref: path}` to a file, not a bundle entry.

## ToolBundle

`kind: ToolBundle` uses [`tool_bundle.schema.yaml`](../schemas/runtime/tool_bundle.schema.yaml).
It requires `metadata.name` and `spec.tools`, a map from tool id to entry.
Each entry requires `description`.
Optional entry fields are `module_path`, `class_name`, `input_schema`, `output_schema`, `tags`, `idempotent`, and `timeout_seconds`.
Entries also accept the advertise hints of a Tool manifest: `title`, `read_only`, `destructive`, `open_world`, `icons`, `task_support`, and `meta`.

```yaml
apiVersion: mas/v1
kind: ToolBundle
metadata:
  name: calc-tools
spec:
  tools:
    calc:
      description: Evaluate an arithmetic expression.
      module_path: ./calc_tool.py
      class_name: CalcTool
```

### Not used yet

`mas-ctl validate` checks ToolBundle documents, but the local tool provider does not resolve `bundle://<bundle>/<tool>` refs in this release.
Reference `kind: Tool` files from `spec.tools[].ref` instead ([tool.md](tool.md)).

## Separation rules (config hygiene)

`mas-ctl validate` (separation checks run with schema validation) and `mas-lab check-config` enforce these rules.

The rules define **semantic ownership**, not a required file layout.
Where a field's schema allows it, you can declare the same resource inline, reference it by a filesystem or library path, or select it by a catalog id.
These forms resolve to the same data and keep the same owner.
Examples:

- `MAS.spec.agency.agents[]` accepts inline Agent documents or `ref`s.
- `Agent.spec.tools[]` accepts semantic names, Tool or ToolBundle refs, or inline implementations.
- `experiment.application` accepts a manifest path or a registered app id.

The forms are equivalent in ownership and resolved intent, but not interchangeable in syntax.
See [writing-manifests.md](writing-manifests.md) for the forms each field accepts.

| Concern | Belongs in |
| --------- | ------------ |
| Model id, temperature | `Agent.spec.models` |
| API base, API keys | `infra/v1` `LLMProxy` (via workspace `infra_refs` or CLI `--infra-ref`) |
| Remote tool URL / headers / pagination | `infra/v1` `ToolServerRegistry` |
| Tool advertise contract (description, parameters, protocol hints) | `kind: Tool` or ToolBundle entry; agents bind it through `spec.tools[]` |
| Topology, delegation graph | `MAS.spec.workflow` |
| Protocol / OTel defaults | `Flavour` |
| Scenario-specific behaviour | `Overlay` |

## Contracts and plugins

Formal **contracts** (tool invocation, delegation, governance) live in `mas-runtime`.
Manifests declare agent and MAS behaviour through these fields:

- `spec.design_pattern`: how the agent picks its next step each turn. A `DesignPatternPlugin` from the plugin registry implements it, and the kernel runs it as a Mealy machine (a state machine whose output depends on state and input). Peer delegation (`delegate_to_*` tool calls) runs through this same contract. The agent has no separate delegation-transport plugin binding.
- `spec.governance[]`, `spec.observability[]`, `spec.control`, and `spec.context_sources`: schema-declared governance, observation, control, and context-source bindings. See [governance.md](governance.md), [reliability.md](../references/reliability.md), and [plugin-bindings.md](plugin-bindings.md).
- `MAS.spec.workflow`: topology (`entry`, `nodes`, `delegates_to`, `dispatch`). A standalone `kind: Workflow` has a separate `workflow/v1` schema with `edges`.

The current Agent and MAS schemas have no `spec.plugins[]`, `spec.workflow.type`, or `spec.workflow.plugin` field.
See the topology, workflow, and routing section in [mas.md](mas.md#topology-workflow-and-routing).

Authoring detail: [agent.md](agent.md#delegation), [mas.md](mas.md), and the [contracts reference](../references/contracts.md).

## See also

- [agent.md](agent.md)
- [governance.md](governance.md)
- [reliability.md](../references/reliability.md)
- [mas.md](mas.md)
- [infra.md](infra.md)
- [tool.md](tool.md)
- [ToolContract](../references/tool-contract.md)
- [ToolServerRegistry](../references/tool-server-registry.md)

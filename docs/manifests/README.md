<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Manifest reference

MAS Lab reads YAML **manifests**: declarative files that describe agents, multi-agent systems, experiments, datasets, overlays, and pipelines.

This folder documents each YAML kind (`kind: Agent`, `MAS`, `Overlay`, and so on). It also documents fields that have no kind of their own, such as `spec.governance`, which lives next to Agent.
Runtime behaviour that spans several fields (retries, circuit breaker, logging) lives under [references/](../references/index.md). Start at [reliability.md](../references/reliability.md).

New to the vocabulary? Read [glossary.md](../glossary.md) first (scenario, overlay, pipeline, run, flavour).

Hands-on path:

1. [Tutorial 1](../tutorials/01-building-an-agent/README.md): build an agent.
2. [Tutorial 2](../tutorials/02-creating-a-mas/README.md): build a multi-agent system (MAS).
3. [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md): run experiments.

---

## How manifests fit together

```text
experiment.yaml          <- what to run (scenarios x dataset x n_runs + pipelines)
    |
    +-- application     -> mas.yaml / registered app
    +-- scenarios[]     -> overlay stacks, one per variant
    +-- dataset         -> Dataset items (inputs + expectations)
    +-- run/item/scenario/post -> pipeline steps (metrics, plots)

mas.yaml                 <- team topology, workflow, transport
    +-- agents/*.yaml    <- design pattern, tools, skills, observability
          +-- tools/*.tool.yaml

lab-config.yaml   <- lab folder metadata (local libraries, plugins)
config.yaml       <- project defaults (flavour, infra_refs, .env path)
```

| Layer | Manifest kinds | Reference |
|-------|----------------|-----------|
| **Agent** | `Agent` | [agent.md](agent.md) · [governance.md](governance.md) · [checkpoint.md](checkpoint.md) · [subagents.md](subagents.md) |
| **Agent internals** (fields, not kinds) | `spec.models[].reasoning`, `spec.assembler`, `spec.context_manager`, plugin slots | [llm-reasoning.md](llm-reasoning.md) · [context-assembly.md](context-assembly.md) · [summarization.md](summarization.md) · [plugin-bindings.md](plugin-bindings.md) |
| **Tools** | `Tool`, `ToolBundle`; runtime-owned system tools | [tool.md](tool.md) · [system-tools.md](system-tools.md) |
| **MAS** | `MAS`, `Workflow` | [mas.md](mas.md) |
| **Override** | `Overlay` | [overlay.md](overlay.md) |
| **Environment** | `Flavour`; infra kinds (`InfraBundle`, `LLMProxy`, `InfraMiddleware`, …) | [flavour.md](flavour.md) · [infra.md](infra.md) · [llm-cache.md](llm-cache.md) |
| **Engine tuning** | `RuntimeEngine` | [runtime-engine.md](runtime-engine.md) · [execution.md](execution.md) (removed agent `spec.execution`) |
| **Model data** | LLM model catalog (shipped data file, not a kind) | [llm-model-catalog.md](llm-model-catalog.md) |
| **Experiment** | `experiment:` root | [experiment.md](experiment.md) |
| **Inputs** | `Dataset` (`apiVersion: lab/v1`) | [dataset.md](dataset.md) · [dataset-migration.md](dataset-migration.md) |
| **Processing** | `pipeline:` root / `kind: Pipeline` | [pipeline.md](pipeline.md) |
| **Lab folder** | `lab:` root (`lab-config.yaml`) | [lab.md](lab.md) |

The runtime manifests (`Agent`, `MAS`, overlays, infra) are documented under [runtime.md](runtime.md).
Failure handling is in [reliability.md](../references/reliability.md).
To reference one manifest from another (paths, `LIBRARY:` prefixes, versions, file names), see [writing-manifests.md](writing-manifests.md).

---

## Resolution: `ref` vs `id`

| Form | Example | Meaning |
|------|---------|---------|
| **File path** | `ref: ./agents/broker.yaml` | Relative to the referring manifest. |
| **Catalog name** | `dataset.name: trip-planner-benchmark` | Resolved under the lab or library. |
| **Library ref** | `standard:openai` | Bundled infra from `library-standard`. |
| **CLI override** | `--infra-ref`, `-o overlay.yaml`, `--override` | One-shot for `mas-ctl` / `mas-lab` ([overrides](../cli/overrides.md)). |

---

## Schema files

The YAML schemas live under [`docs/schemas/`](https://github.com/outshift-open/mas-lab/tree/main/docs/schemas) (overview: [schemas.md](../references/schemas.md)).
The bench UI and the validators compile them at runtime.
`mas-ctl validate` checks any kind. `mas-lab validate` checks experiment, pipeline, lab config, and Dataset files.

| Area | Schema directory |
|------|------------------|
| Lab (experiment, dataset, pipeline) | `docs/schemas/lab/` |
| Runtime (agent, mas, overlay, infra) | `docs/schemas/runtime/` |
| Workspace | [config.schema.yaml](../schemas/config.schema.yaml) |

---

## Application binding (tutorials vs paper labs)

An experiment names the MAS under test in `experiment.application`.
The plural `applications:` form is deprecated. It is still accepted while you migrate.

| Style | Example | When to use |
| --- | --- | --- |
| **Inline manifest** | `application: {manifest: ./mas.yaml, configs_dir: ./overlays}` | Tutorials, self-contained experiments |
| **Registered app** | `application: {app: trip-planner, configs_dir: ./overlays}` | Paper labs, shared apps under `library-samples/apps/` |

Scenarios reference overlay **ids** from `configs_dir` (tutorial `cot`, lab `pattern-cot`).
Point the dataset at a file with `path: ./dataset.yaml` (tutorial), or at a catalogued benchmark with `name` plus `locator: samples`.

For workflow overlays versus routing overlays, see [Topology, workflow, and routing](mas.md#topology-workflow-and-routing).

---

## See also

- [user-config.md](../user-config.md): XDG user config and project `config.yaml`
- [cli/observability.md](../cli/observability.md): `events.jsonl` and CLI flags
- [paper/index.md](../paper/index.md): sample labs that ship with the repo

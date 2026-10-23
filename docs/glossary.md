<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Glossary (manifests & experiments)

This page defines the terms you meet in YAML files and CLI output. Look a term
up here when another page uses it without explanation.

| Term | Meaning |
|------|---------|
| **Scope** | A place the controller can list catalog items from: a **library** (`library:<name>`) or a **lab** (`lab:<name>`). The UI never builds a server path. |
| **Catalog item** | One catalog entry of a kind (application, agent, overlay, pipeline, experiment, dataset, tool, skill, scenario). Distinct from a dataset **item** below. |
| **Item id** | `<name>` or `<name>@<version>`. Opaque to the UI: it comes from the backend and is sent back. Never derived from `metadata.name`. |
| **Ref** | The string another manifest uses to point at a catalog item (`samples:calc@v1`). The backend returns `item.ref`; the UI never composes one. |
| **Artifact id** | Opaque id of a file the backend serves (a run output, a trace, a plot). Never a path. |
| **Manifest** | A YAML file that declares one thing: an agent (`agent.yaml`), a multi-agent team (`mas.yaml`), an experiment (`experiment.yaml`), an overlay, a flavour, and so on. |
| **MAS** | Multi-agent system: several agents plus a workflow that says who talks to whom. Declared in a `mas.yaml` manifest. |
| **Agent** | One LLM-driven actor with tools, skills, and plugins. Declared in an `agent.yaml` manifest. |
| **Overlay** | A small YAML file (`kind: Overlay`) merged on top of a manifest. CLI: `-o path/to/overlay.yaml`. It changes one setting (tools, memory, governance) without copying the whole manifest. |
| **Flavour** | Deployment preset (`kind: Flavour`). It sets how the system is deployed: observability export, agent-to-agent protocol, and tool exposure. It does **not** hold the model endpoint. Default: `local`. See [flavour manifest](manifests/flavour.md). |
| **Infra (infrastructure bundle)** | `apiVersion: infra/v1` files that say *where* things are: LLM endpoint and model catalogue (`LLMProxy`, `LLMLocal`), tool servers, application endpoints, LLM cache. Referenced with `--infra-ref` or `infra_refs:` in `config.yaml` (e.g. `standard:openai`, a bundled infra). See [infra manifest](manifests/infra.md). |
| **Lab (definition)** | A folder `*.lab/` (under `labs/`) with `experiment.yaml`, datasets, overlays, and optional custom pipeline steps. It is where you run experiments. It is not a library. See [labs-and-libraries.md](labs-and-libraries.md). |
| **Library** | A folder with `library.yaml` that holds reusable apps, tools, plugins, and code. In a `name:path` reference, `name` is a library name. A library may live inside a lab. |
| **Package** | An installable Python package (`mas-runtime`, `mas-ctl`, `mas-lab`, …). See [packages reference](packages-reference.md) and the [package map](libraries.md). |
| **Component** | One of the three lab sub-wheels under `lab/components/` (`mas-lab-core`, `mas-lab-bench`, `mas-lab-controller`). Distinct from the internal `mas.lab.components` runtime modules inside the `mas-lab` package. See [Repo structure](what-is/repo.md#lab-labs-and-components). |
| **Experiment** | The top level of the hierarchy. The `experiment:` block (usually in `experiment.yaml`) names the MAS under test (`application:`), the scenarios, the dataset, the number of runs, and the level hooks that build results. |
| **Application (in an experiment)** | The `application:` block of an experiment: the MAS or agent under test (`app:` catalog id or `manifest:` path, plus `configs_dir:` for overlays). Not to be confused with the infra `kind: Application`, which declares named service endpoints. |
| **Model slots** | `experiment.models` (and `lab.models`): named LLM pins matching Agent/MAS `spec.models[].id`, plus `judge`. Scalar `experiment.model` = `models.main`. `any` inherits the next layer; local `config.yaml` `defaults.model` fills remaining `any` at engine time. |
| **Scenario** | One column of the experiment matrix: a named setup (`id`) with layered overlays (`logic`, `control`, `infra`). Declared under `scenarios:`. |
| **Item** | One case of the dataset (`item:` hooks, CLI `--item`). Older material calls it `test:`. |
| **Dataset** | The input items (prompts, expected fields) the experiment runs over. Referenced as `dataset:` in `experiment.yaml`. |
| **Run** | One execution of the agent or MAS for a given scenario, item, and repeat index. It writes `traces/events.jsonl`. |
| **`n_runs`** | How many times to repeat each (scenario, item) for variance. Declared under `run.n_runs`. |
| **Benchmark** | `mas-lab benchmark run` — executes all runs, then runs level hooks. |
| **Pipeline** | Ordered **pipeline steps** that read run artifacts and write CSV/PNG under `results/`. A pipeline is not a separate key of the experiment: it is declared as steps in the level hooks (inline, or by reference to a pipeline file). |
| **Level hooks** | `run.post` / `item.post` / `scenario.post` / experiment `post:` in `experiment.yaml` — the places where pipeline steps are attached, one per level of the hierarchy experiment → scenario → item → run. |
| **Pipeline step** | One unit in a level hook (e.g. `extract_trace_stats`, `plotnine`). Declared with `name`, `type`, `config`. |
| **Hierarchy levels** | The experiment hierarchy is *experiment → scenario → item → run*. Older material may say *test* for *item*, or *application* for the experiment level; use the names above. |
| **`events.jsonl`** | The machine-readable trace of a run: one JSON object per line (model calls, tool calls, governance, routing). |
| **Exchange log** | Human-readable transcript on stderr during `mas-ctl chat` (`--trace` = summary + timestamps; `--trace full` for the payload dump). Persist with `mas_ctl.trace` in `config.yaml`. Not used for experiment scoring. |
| **Trace cache** | Store of completed run traces. A run with identical inputs reuses the stored trace instead of calling the model again. |
| **SCOPE** | System for Cognitive Observability Provenance and Explainability. Open-source platform whose `norm` normalizer the optional knowledge-graph pipelines can reuse. Docs: [ioc-project.github.io/scope](https://ioc-project.github.io/scope/). See [Tutorial 9](tutorials/09-kg-scope/README.md). |
| **Observability** | Settings that enable `events.jsonl` (manifest `spec.observability`, overlay, or `--events`). |
| **Plugin** | A runtime or pipeline extension registered by a library: a design pattern, memory store, tool provider, or pipeline step. See [labs-and-libraries.md](labs-and-libraries.md#local-plugins). |
| **Contract** | The declared inputs, outputs, and rules of a boundary such as a tool call, a delegation, or a model request. The runtime checks them. See [tool contract](references/tool-contract.md). |
| **MCP / A2A** | Model Context Protocol (remote tools) and Agent2Agent (remote agents). Both are infra adapters. See [Open standards](standards/index.md). |
| **HITL** | Human in the loop: a person approves a tool call before it runs. See [Tutorial 14](tutorials/14-governance-hitl/README.md). |
| **MCE** | Metrics Computation Engine, an open-source set of metrics that score a session with an LLM judge. See [Tutorial 10](tutorials/10-evaluation-metrics/README.md). |
| **Skill** | A reusable instruction pack for an agent: a procedure, reference files and optional scripts, in a `SKILL.md` folder. See [Tutorial 6](tutorials/06-agent-skills/README.md). |
| **Trace** | The record of one run: the ordered events of the agent, its tools and its delegations. MAS Lab stores it as `events.jsonl`. |
| **Span** | One timed step in a trace, with a start, an end and a parent. OpenTelemetry (OTel) is the standard format for spans. See [Tutorial 8](tutorials/08-telemetry/README.md). |
| **Knowledge graph (KG)** | A trace turned into typed nodes (calls, tools, agents) and edges (who called what). You query it to ask structural questions. See [Tutorial 9](tutorials/09-kg-scope/README.md). |
| **Judge** | A model that scores an answer against a metric, for example answer relevancy. Set it with `experiment.models.judge`. See [Tutorial 10](tutorials/10-evaluation-metrics/README.md). |
| **Session** | One conversation with an agent, with its own history and state. A session can be saved, forked and resumed. |
| **Checkpoint** | A saved copy of a session at a point in time. You can fork it, resume it or go back to it. See [Tutorial 11](tutorials/11-sessions-and-recovery/README.md). |
| **Governance** | Rules that decide whether an action may run: budgets, approvals, allowed tools. Written as an overlay, not in the application. See the [governance manifest](manifests/governance.md). |
| **Context window** | The maximum amount of text a model can read in one request. When the history gets too long, MAS Lab trims or summarises it (compaction). |
| **Kernel** | The small state machine inside `mas-runtime` that checks each contract and runs the plugins. See [kernel operations](references/kernel-primitives.md). |

Runtime internals: see the [`runtime/docs/`](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/index.md) tree in the repository.

Hub: [references/index.md](references/index.md) · Run traces:
[cli/observability.md](cli/observability.md).

Next: [Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md).

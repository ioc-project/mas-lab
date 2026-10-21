<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Architecture

This page explains how MAS Lab goes from a declaration to a result. It names the
five stages, the three layers you can vary, the packages and the user interfaces.

## Shape of the system

MAS Lab is a five-stage pipeline, driven by an experiment control plane. Each stage
hands the next one a well-defined artifact: manifests, a runnable configuration,
events, run artifacts, results. **Nothing is passed implicitly.**

## The five stages

| Stage | Owner | Input → Output |
|---|---|---|
| **1. Specify** | YAML manifests | Declarations → a complete specification |
| **2. Compose** | `mas-ctl` | Specification → a runnable configuration (compose, session, placement) |
| **3. Execute** | `mas-runtime` | Configuration → a running MAS, with contracts enforced at boundaries |
| **4. Observe** | Contract boundaries | Execution → `traces/events.jsonl` |
| **5. Analyse** | `mas-lab-bench` | Events → results, via a pipeline DAG |

### Stage 1 — Specify

Everything is a YAML manifest. There is no imperative glue.

| Manifest | Declares | Reference |
|---|---|---|
| **Agent** | One actor: models, tools, skills, memory, governance, observability | [agent](manifests/agent.md) |
| **MAS** | A team: which agents exist, who delegates to whom | [mas](manifests/mas.md) |
| **Overlay** | A patch merged onto any of the above, used to vary one knob | [overlay](manifests/overlay.md) |
| **Flavour** | A deployment preset: observability export, transport, tool policy | [flavour](manifests/flavour.md) |
| **Infra** | Resources resolved at execution time: model endpoints, tool servers, secrets mapping | [infra](manifests/infra.md) |
| **Experiment** | Scenarios, dataset, repeats and analysis hooks | [experiment](manifests/experiment.md) |
| **Dataset** · **Pipeline** | The items an experiment iterates over; the analysis steps | [dataset](manifests/dataset.md) · [pipeline](manifests/pipeline.md) |

An experiment compares **scenarios**. A scenario is a stack of overlays in three
separate layers. Keeping the layers apart is what makes a result trustworthy:

| Layer | Varies | Typical content |
|---|---|---|
| **Logic** | What the system *is* | Design pattern, topology, memory, skills, tools |
| **Control** | What policy applies | Governance, budgets, approvals, guardrails |
| **Infra** | What it runs on | Model endpoint, tool servers, runtime profile |

Three more things stay fixed, unless one of them is the factor you study:

- the **flavour**: a deployment preset for monitoring export, transport and tool policy
- the **infra bundle**: chosen with `--infra-ref` or in the workspace configuration
- the **emulation mode**: live, mocked or replayed model and tools

The rule of thumb, from the
[experiment manifest](manifests/experiment.md#design-vs-schedule): a field that
changes what a result *means* is part of the design. A field that only changes how
fast or safely you collect the same results is part of the schedule.

!!! warning "Constraints on the variation layers"
    Do not put a model endpoint in a logic or control overlay, or an API URL in a
    flavour. Model URLs belong in infra manifests and in the agent's `models` block.
    If the model or infrastructure **is** the factor you study, vary it in the
    scenario's `infra` layer.

    Mixing the layers hides which change caused a difference in the results.

### Stage 2 — Compose

`mas-ctl` turns the specification into something you can run. It loads a manifest,
applies the overlays, fills in default values and binds the result to an infra
bundle.

Overlays apply in a fixed order: first the files you pass with `-o` (you can repeat
it), then the `--override` patches from the command line. The infra bundle comes
from `--infra-ref` or from the workspace configuration, never from the agent, MAS
or overlay manifests.

| Command | What it produces |
|---|---|
| `mas-ctl validate` | A check of the manifest's structure and references. It needs no model |
| `mas-ctl compile` | The **resolved spec**: overlays merged and defaults filled in |
| `mas-ctl compose` | The final bindings and where each agent runs (the **placement**) |
| `mas-ctl run-mas` · `chat` | A running session, using the same wiring |

The full configuration is resolved before the run starts. A run therefore depends
only on its manifests, which is what makes it reproducible.

See the [`mas-ctl` reference](cli/mas-ctl.md) and [`compile`](cli/compile.md).

### Stage 3 — Execute

`mas-runtime` runs the agents. Its core is a small state machine (a **Mealy
kernel**) that checks a **contract** each time an agent calls a tool, delegates
to another agent or sends a request to a model. Plugins extend it in three
categories:

| Plugin category | Purpose |
|---|---|
| **Observability** | Records an event at each contract boundary |
| **Governance** | Applies policy, as an overlay |
| **Infrastructure** | Handles placement and resources |

Because every call crosses a contract, you can test and monitor at those points.
Governance rules, such as budgets, approvals, delegation rules and tool access,
also apply there. They live in an overlay, not in the application.

See the [runtime](references/runtime.md), the [contracts](references/contracts.md),
the [kernel primitives](references/kernel-primitives.md) and the
[reliability notes](references/reliability.md).

### Stage 4 — Observe

Events come from the contract boundaries, so coverage is built in. Nobody has to
remember to add logging to a code path.

A run produces three outputs. Do not confuse them:

| Output | What it is | Used for |
|---|---|---|
| **`events.jsonl`** | One JSON object per line: model calls, tool calls, governance, routing | Everything scored or plotted |
| **Exchange log** | A readable transcript of agent, model and tool messages, on stderr | Debugging by hand only |
| **OpenTelemetry spans** | An optional second export | External collectors and knowledge graphs |

When an experiment claims that governance fired or that a tool was called a given
number of times, check `events.jsonl`. Never check the exchange log.

See the [observability reference](cli/observability.md) and
[Tutorial 8 — Telemetry](tutorials/08-telemetry/README.md).

### Stage 5 — Analyse

`mas-lab-bench` reads the events and builds the results. You declare the analysis in
the same experiment manifest, as a list of **pipeline steps**. Steps extract trace
statistics, score answers with an evaluation metric, compute confidence intervals
or draw plots. The output is CSV files and figures in the experiment's `results/`
folder, so a lab needs no separate plotting scripts.

Finished runs are stored in a **trace cache**. If you rerun an experiment whose
inputs have not changed, the reports refresh without any new model calls.

```bash
mas-lab benchmark run experiment.yaml --progress
mas-lab benchmark show last plots
```

See the [experiment manifest](manifests/experiment.md), the
[pipeline manifest](manifests/pipeline.md),
[Tutorial 3](tutorials/03-experiments-and-analysis/README.md) and
[Tutorial 10 — Evaluation metrics](tutorials/10-evaluation-metrics/README.md).

## The lab control plane

`mas-lab benchmark run` first executes all runs, then runs the analysis steps. The
work is organised in four nested levels. Each level can have steps that run when
that level completes:

| Level | YAML key | Scope |
|---|---|---|
| **Experiment** | `post:` | The whole experiment, for example aggregating metrics |
| **Scenario** | `scenario:` | One overlay stack |
| **Item** | `item:` | One dataset item, across all its runs |
| **Run** | `run:` | One execution of one scenario on one item. It writes `traces/events.jsonl` |

`n_runs` repeats each scenario and item pair. Models are not deterministic, so one
run proves nothing. Repeating gives you the **variance**, instead of assuming it.

The experiment's `design` chooses which scenario and item pairs to run:

| Mode | Cells run |
|---|---|
| `cartesian` | Every scenario against every item |
| `coupled` | Only the listed (scenario, items) pairs |
| `one_factor` | Sweep one axis, scenarios or items, while the other is pinned |

Use `one_factor` to vary one thing at a time. Set `design.max_executions` to stop a
plan that is larger than you intended. Set `dataset.limit` for a quick smoke run.

Analysis steps live in these level hooks. An experiment has no separate `pipeline:`
key. You can still run a standalone pipeline file with `mas-lab benchmark pipeline run`.

## Where the guarantees come from

No single layer gives all the guarantees. They add up across the three.

| | Source | What it covers |
|---|---|---|
| **By design** | The specification and the runtime | Declared roles, allowed tools, policy checks, controlled execution |
| **By validation** | Labs | Building blocks and combinations tested before release |
| **By observation** | Traces and guardrails | What only appears under real workloads, delays and policies |

Four properties build on each other:

1. **Testable**: contracts are fixed points you can write assertions against.
2. **Reproducible**: the configuration is fully resolved before the run, so a run
   depends only on its manifests.
3. **Observable**: events come from the contract boundaries, so nothing depends on
   someone remembering to log.
4. **Governable**: policy is an overlay outside the application, so you can change,
   audit and vary it on its own.

!!! note "The experimental claim"
    A difference you observe is caused by the variable you changed, not by
    something else that varied in the stack.

    This holds only while the variation layers stay separate. That is why the
    rules above (no model endpoint in a logic or control overlay, no API URL in a
    flavour) are requirements, not style.

## Packages

Each layer depends only on the layers above it in this table. There are no cycles.

| Layer | Package | Role |
|---|---|---|
| **Runtime** | `mas-runtime` | The kernel, contracts, plugins and design patterns. It is a library with no command line |
| **Orchestration** | `mas-ctl` | Builds and runs sessions: `chat`, `run-mas`, `validate`, `compile`, `compose` |
| **Lab** | `mas-lab-core` | Shared contracts and telemetry types |
| | `mas-lab-bench` | Runs benchmarks and analysis pipelines |
| | `mas-lab-controller` | A background service with an HTTP API, used by the web UI |
| | `mas-lab` | Installs the three above and provides the `mas-lab` command |
| **Libraries** | `mas-library-standard` | Flavours, overlays, infra bundles, built-in plugins |
| | `mas-library-eval` | Evaluation metrics consumed by pipeline steps |
| | `mas-library-lab` | Shared types and backend code for the lab and its UI |
| | `mas-library-samples` | Reference applications, datasets, tutorial fixtures |
| | `mas-library-telemetry` | Converts and replays traces, exports to OpenTelemetry |
| | `mas-library-kg` | Turns traces into a knowledge graph |
| | `mas-library-skills` | The skill catalogue and the tools that read skills |
| | `mas-library-ioa` | MCP client/server bridge |

A *lab* is an experiment folder. A *library* is a folder with a `library.yaml`
holding reusable applications, tools and plugins, referenced as `name:path`. The
two are deliberately different things.

See the [package map](libraries.md), the [packages reference](packages-reference.md)
and [labs vs libraries](labs-and-libraries.md).

## Surfaces

| Surface | What it is for | Docs |
|---|---|---|
| **`mas-ctl`** | Run, validate, compile and compose agents and MAS; chat | [CLI](cli/mas-ctl.md) |
| **`mas-lab`** | Workspace bootstrap, benchmarks, pipelines, telemetry, plots | [CLI overview](cli/index.md) |
| **Terminal UI** | Interactive session in the terminal | [TUI](ctl/tui.md) |
| **Web UI** | A browser studio: browse and validate manifests, launch benchmarks, inspect runs | [Web UI](ui/index.md) |
| **Live dashboard** | Topology, native events and chat for a running MAS or a finished run | [Live dashboard](ui/live.md) |
| **Controller HTTP API** | The REST backend both web surfaces talk to | [Web UI](ui/index.md) |

Next: [Labs](labs.md).

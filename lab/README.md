<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab

> Batch experiments and figures for multi-agent systems.

**Start:** [docs/index.md](docs/index.md) · [labs-quickstart.md](docs/labs-quickstart.md) ·
[glossary.md](../docs/glossary.md) · [cli/observability.md](../docs/cli/observability.md).

`mas-lab` runs **benchmark** **experiments** via `mas-ctl`, writes **`events.jsonl`**
per **run**, and runs level hooks (`run` / `item` / `scenario` / `post`) to produce CSV/PNG results.

Stack position:

```text
mas-runtime  →  single-agent execution
mas-ctl      →  multi-agent orchestration
mas-lab      →  evaluation / benchmarks / pipelines
```

Dependencies: [`mas-runtime`](../runtime/) and [`mas-ctl`](../ctl/).

---

## Components

| Component | Package | What it provides |
| --------- | ------- | ---------------- |
| [core](components/core/README.md) | `mas-lab-core` | Validation, config hygiene, export, telemetry |
| [bench](components/bench/README.md) | `mas-lab-bench` | Benchmarks, pipelines, plots |
| [controller](components/controller/README.md) | `mas-lab-controller` | HTTP API and UI backend |

---

## CLI commands

| Command | Component | Intent |
| ------- | --------- | ------ |
| [`check`](components/core/README.md#check) | core | Validate a MAS specification |
| [`benchmark`](components/bench/README.md#benchmark-run) | bench | Run experiments + level hooks |
| [`plot`](components/bench/README.md) | bench | Trajectory / communication plots |
| [`telemetry`](components/core/README.md#telemetry) | core | Inspect or push traces |

Full tables: [components/bench/README.md](components/bench/README.md).

---

## Tutorials

Workspace tutorials (recommended path):

| # | Tutorial | Topic |
|---|----------|-------|
| 0 | [Environment setup](../docs/tutorials/00-environment-setup/) | Install, API keys |
| 1 | [Building an agent](../docs/tutorials/01-building-an-agent/) | Single agent, `mas-ctl chat` |
| 2 | [Creating a MAS](../docs/tutorials/02-creating-a-mas/) | Topology, delegation |
| 3 | [Experiments & analysis](../docs/tutorials/03-experiments-and-analysis/) | `benchmark run`, pipelines |

Index: [docs/tutorials/index.md](../docs/tutorials/index.md).

---

## Documentation

### User

| Document | Description |
| -------- | ----------- |
| [docs/user-guide.md](docs/user-guide.md) | Package overview |
| [docs/labs-quickstart.md](docs/labs-quickstart.md) | Run any `.lab` experiment |
| [docs/labs-going-further.md](docs/labs-going-further.md) | Custom steps and scenarios |

### Benchmark & pipeline

| Document | Description |
| -------- | ----------- |
| [docs/benchmark.md](docs/benchmark.md) | Benchmark CLI and two-phase model |
| [docs/pipeline.md](docs/pipeline.md) | Pipeline YAML |
| [docs/pipeline-steps.md](docs/pipeline-steps.md) | Step type catalog |
| [docs/pipeline-processors.md](docs/pipeline-processors.md) | Trajectory processors |
| [docs/designing-experiments.md](docs/designing-experiments.md) | Scenario matrices, smoke runs |
| [docs/multi-scenario-format.md](docs/multi-scenario-format.md) | `scenarios:` × dataset × runs |
| [docs/benchmark-state-architecture.md](docs/benchmark-state-architecture.md) | Locks, cache, resume |
| [../library-eval/README.md](../library-eval/README.md) | MCE metrics and `mas-lab eval` |
| [docs/contracts.md](docs/contracts.md) | `mas-lab check` |
| [docs/replay-equivalence-checklist.md](docs/replay-equivalence-checklist.md) | Trace equivalence |

Pipeline YAML and fingerprints: [docs/pipeline.md](docs/pipeline.md).
---

## Examples

| Path | Description |
| ---- | ----------- |
| [../labs/](../labs/) | Paper §5 reproduction labs |
| [../library-samples/](../library-samples/) | Sample overlays and apps |

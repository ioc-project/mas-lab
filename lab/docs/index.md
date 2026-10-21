<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab documentation

This section covers `mas-lab` benchmarks, pipelines, and validation. Read it to run a lab or write your own pipeline steps.

Smallest working command: `mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress`.

**Start:** [labs-quickstart.md](labs-quickstart.md) · **Terms:**
[glossary.md](../../docs/glossary.md) · **Site docs:** [docs/index.md](../../docs/index.md).

## Guides

| Page | Contents |
|------|----------|
| [labs-quickstart.md](labs-quickstart.md) | Run a **lab**; level-hook figures |
| [labs-going-further.md](labs-going-further.md) | Custom **pipeline steps**, **scenarios** |
| [labs-and-libraries.md](../../docs/labs-and-libraries.md) | Lab vs library vs local plugin |
| [benchmark.md](benchmark.md) | `mas-lab benchmark` |
| [pipeline.md](pipeline.md) | **Pipeline** YAML |
| [pipeline-steps.md](pipeline-steps.md) | Built-in step types |
| [pipeline-processors.md](pipeline-processors.md) | Processors for `type: processor` |
| [designing-experiments.md](designing-experiments.md) | **Scenario** matrices |
| [multi-scenario-format.md](multi-scenario-format.md) | **Scenarios** × **dataset** × **runs** |
| [benchmark-state-architecture.md](benchmark-state-architecture.md) | **Trace cache**, locks |
| [user-guide.md](user-guide.md) | Package overview |

## Cross-cutting

| Topic | Page |
|-------|------|
| **`events.jsonl`**, **observability** flags | [cli/observability.md](../../docs/cli/observability.md) |
| Paper **experiments** | [paper/index.md](../../docs/paper/index.md) |
| Evaluation metrics | [library-eval/README.md](../../library-eval/README.md) |

## Internals

| Page | Contents |
|------|----------|
| [pipeline.md](pipeline.md) | Executor DAG, fingerprints, `depends_on` |
| [bench README](../components/bench/README.md) | Full benchmark CLI |

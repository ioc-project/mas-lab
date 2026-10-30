<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Benchmark guide

This page explains `mas-lab benchmark`. Read it before you run or re-run an experiment.

`mas-lab benchmark` runs an **experiment**. It executes every **scenario** on every **dataset** item, once per **run**. Then it runs the level hooks declared in `experiment.yaml`. A level hook is a pipeline step attached to one level: run, item, scenario, or experiment.

Smallest working command:

```bash
mas-lab benchmark run experiment.yaml --progress
```

Flags: [cli-reference.md](cli-reference.md) and [components/bench/README.md](../components/bench/README.md). Terms: [glossary.md](../../docs/glossary.md).

## Two phases, one command

| Phase | Command | Produces |
|-------|---------|----------|
| **Execution** | `mas-lab benchmark run experiment.yaml` | Per-**run** `events.jsonl` |
| **Pipeline** | Automatic after execution | Per-level artifacts (`run/…/metrics.json`, `run/…/data.csv`, gathered up through `scenario/data.csv` to `data.csv` and any figures at the experiment level) |

A re-run skips completed **runs** unless you pass `--force`. It re-executes **pipeline steps** when their fingerprints change.

## Essential commands

```bash
mas-lab benchmark run experiment.yaml --progress    # execution + level hooks
mas-lab benchmark run experiment.yaml --dry-run       # JSON Schema validate + execution plan
mas-lab benchmark show last                         # inspect the latest run
mas-lab benchmark pipeline run pipeline.yaml -o DIR # pipeline only
```

## Output location

The output directory comes from `experiment.name` and [user-config](../../docs/user-config.md). The default (after `mas-lab config`) is `$XDG_DATA_HOME/mas/labs/<experiment-name>/`.

Override it with `-o /path/to/output`.

## Paper reproduction

```bash
task reproduce
```

Per-lab commands: [labs-quickstart.md](labs-quickstart.md).

## More

| Topic | Document |
| ------- | ---------- |
| Pipeline YAML | [pipeline.md](pipeline.md) |
| Pipeline step types | [pipeline-steps.md](pipeline-steps.md) |
| Run state, locks, fingerprints | [benchmark-state-architecture.md](benchmark-state-architecture.md) |
| Reproducibility | [Experiments and analysis](../../docs/tutorials/03-experiments-and-analysis/README.md) |
| Export, publish, and import a run | [sharing-benchmarks.md](sharing-benchmarks.md) |
| Paper labs | [paper/index.md](../../docs/paper/index.md) |

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab user guide

This page lists the main `mas-lab` commands. Read it for a first overview of the package.

`mas-lab` runs benchmark **experiments**, validates **manifests**, and runs **pipeline steps** on **`events.jsonl`** logs.

Smallest working command:

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
```

Workspace install: [docs/user-guide.md](../../docs/user-guide.md). Terms:
[glossary.md](../../docs/glossary.md).

## Commands

| Command | What it does |
|---------|--------------|
| `mas-lab benchmark run experiment.yaml` | All **scenarios** × **dataset** × **runs** + level hooks |
| `mas-lab benchmark show last` | Latest `output_dir` |
| `mas-lab check …` | Validate **agent** / **MAS** manifest |
| `mas-lab telemetry show …` | Print **`events.jsonl`** |

## Workflow

Run an experiment, then open its plots:

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
mas-lab benchmark show last plots
```

## Docs

| Page | Contents |
|------|----------|
| [labs-quickstart.md](labs-quickstart.md) | First **lab** |
| [benchmark.md](benchmark.md) | Benchmark CLI |
| [pipeline.md](pipeline.md) | **Pipeline** YAML |
| [index.md](index.md) | Full list |
| [cli/observability.md](../../docs/cli/observability.md) | **Observability** |
| [paper/index.md](../../docs/paper/index.md) | Paper **experiments** |

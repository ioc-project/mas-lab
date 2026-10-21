<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lab quickstart

This page runs your first lab. Read it if you have cloned the repo and want results.

A **lab** is a folder ending in `.lab` with an **experiment** manifest (`experiment.yaml`). The manifest names the **scenarios** (setups to compare) and **dataset** items (inputs) to run. It also names the **pipeline** steps that turn **runs** (one execution of one scenario on one item) into tables and figures.

Full term list: [glossary.md](../../docs/glossary.md). Default paths:
[`docs/user-config.md`](../../docs/user-config.md). Lab vs library:
[labs-and-libraries.md](../../docs/labs-and-libraries.md).

## What one command does

```bash
export PATH="$PWD/.venv/bin:$PATH"
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
```

`mas-lab benchmark run` submits the work to the MAS Lab controller daemon and streams its output. The daemon starts automatically. The command has two phases:

1. **Execution.** For each scenario, each dataset item, and each repeat (`n_runs`), the runtime executes the **agent** or **MAS**. It writes **`events.jsonl`** under `traces/`. The **trace cache** skips identical work.

2. **Level hooks.** The `run:`, `item:`, `scenario:`, and experiment `post:` lists in the same `experiment.yaml` run automatically. There is no `pipeline:` key.

Paper figures come only from this command (phase 1 plus level hooks). Do not use standalone plotting scripts.

## What you need installed

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- An OpenAI-compatible API (`OPENAI_API_KEY`, optional `OPENAI_API_BASE`)
- The repo cloned. From the root, run `uv sync --all-packages`.

Install walkthrough: [Tutorial 0](../../docs/tutorials/00-environment-setup/README.md).

## Where output goes

By default results go under the labs root, in a folder named after
`experiment.name` (`mas-lab config` prints the effective paths):

```text
$XDG_DATA_HOME/mas/labs/<experiment-name>/results/
  trace_stats.csv
  mealy_stats.csv
  fig-*.png
  metadata.json
```

Paths: [user-config](../../docs/user-config.md).

## Inspect results

```bash
mas-lab benchmark show last
mas-lab benchmark show last plots
mas-lab benchmark list --limit 5
```

## Smoke runs (fewer scenarios / runs)

```bash
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml --dry-run

mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml \
  --limit-scenarios 2 --max-runs 1 --progress
```

## Re-run without new LLM calls

Re-run the same command. It skips cached **runs**. **Pipeline steps** refresh when their inputs or step code change.

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
```

Pipeline only (logs already on disk):

```bash
mas-lab benchmark pipeline run labs/design-space.lab/01-design-patterns/pipeline-figure.yaml \
  -o ${XDG_DATA_HOME:-$HOME/.local/share}/mas/labs/lab1-exp1.1-design-patterns-qa
```

## Paper experiments

| Paper § | Command |
|---------|---------|
| §5.1a design patterns | `mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml` |
| §5.1b topologies | `mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml` |
| §5.2 lifecycle | `mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml` |
| §5.3 extensions | `mas-lab benchmark run labs/extensions.lab/experiment.yaml` |
| All | `task reproduce` |

Details: [paper/index.md](../../docs/paper/index.md).

## Observability in benchmarks

Benchmarks enable **`events.jsonl`** through the **flavour** and **overlays** in the experiment manifest. They do not use `mas-ctl --events`. See [cli/observability.md](../../docs/cli/observability.md).

## Next

- [labs-going-further.md](labs-going-further.md) — custom pipeline steps and scenarios
- [Tutorial 3](../../docs/tutorials/03-experiments-and-analysis/)
- [benchmark.md](benchmark.md) — benchmark CLI reference

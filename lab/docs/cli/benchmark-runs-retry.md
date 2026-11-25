<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-runs-retry(1)

## NAME

`mas-lab benchmark runs retry` — Choose the runs another round should run

## SYNOPSIS

```text
mas-lab benchmark runs retry [OPTIONS] EXPERIMENT_YAML
```

## DESCRIPTION

Asks the experiment's retrier which runs of a plan have no complete result in a merged results tree, and writes the next round's plan, shards and matrix. A run whose job was lost always runs again.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT_YAML` | Positional argument. |
| `--plan` | — |
| `--results` | Merged results tree of the round that ran. |
| `-o`, `--output` | — |
| `-x`, `--experiment-overlay` | Experiment overlay, as for `benchmark run` (repeatable). Plan and run must use the same ones. |
| `--override` | Experiment override. |
| `--max-runs` | Override n_runs from YAML. |
| `--max-shards` | Most shards to emit (a GitHub Actions matrix holds 256 jobs). |
| `--shard-size` | Runs per shard for --shard-by chunk. |
| `--shard-by` | What one CI job runs. |
| `--shards-dir` | Write shard-N.json and matrix.json here (default: next to the plan file). |
| `--scheduler` | Registered scheduler id; overrides schedule.plugin. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark runs`

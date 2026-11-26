<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-runs-plan(1)

## NAME

`mas-lab benchmark runs plan` — Generate and schedule runs; write plan and shards

## SYNOPSIS

```text
mas-lab benchmark runs plan [OPTIONS] EXPERIMENT_YAML
```

## DESCRIPTION

Runs the experiment's scenario generator and scheduler and writes `plan.json`, one `shard-N.json` per CI job and `matrix.json` (a GitHub Actions matrix). Nothing is executed.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT_YAML` | Positional argument. |
| `-o`, `--output` | Where to write the full plan. |
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

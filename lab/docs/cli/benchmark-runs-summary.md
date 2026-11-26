<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-runs-summary(1)

## NAME

`mas-lab benchmark runs summary` — Run the analyzer over a results tree

## SYNOPSIS

```text
mas-lab benchmark runs summary [OPTIONS] EXPERIMENT_YAML
```

## DESCRIPTION

Runs the experiment's analyzer over a merged results tree and prints its report. With `--fail-on-incomplete` it exits 1 when a planned run is incomplete.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT_YAML` | Positional argument. |
| `--plan` | — |
| `--results` | — |
| `--fail-on-incomplete` | Exit 1 when any planned run is incomplete. |
| `-x`, `--experiment-overlay` | Experiment overlay, as for `benchmark run` (repeatable). Plan and run must use the same ones. |
| `--override` | Experiment override. |
| `--max-runs` | Override n_runs from YAML. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark runs`

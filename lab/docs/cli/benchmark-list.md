<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-list(1)

## NAME

`mas-lab benchmark list` — List benchmark runs

## SYNOPSIS

```text
mas-lab benchmark list [OPTIONS]
```

## DESCRIPTION

List benchmark runs.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--status` | Filter by status (running, completed, failed, …). |
| `--experiment` | Filter to runs matching this experiment.yaml path. |
| `--limit` | Maximum number of runs to show. |
| `--format` | Output format. |
| `-o`, `--output-dir` | Benchmarks root directory. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab benchmark migrate`
- `mas-lab --help`

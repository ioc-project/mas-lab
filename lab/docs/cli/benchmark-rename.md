<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-rename(1)

## NAME

`mas-lab benchmark rename` — Rename a benchmark

## SYNOPSIS

```text
mas-lab benchmark rename [OPTIONS]
```

## DESCRIPTION

Rename a benchmark.

## OPTIONS

| Flag | Meaning |
|------|------|
| `BENCHMARK_ID` | Positional argument. |
| `NEW_SLUG` | Positional argument. |
| `--dry-run` | Show what would be done without touching the filesystem. |
| `-o`, `--output-dir` | Override the benchmarks root directory. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark migrate`
- `mas-lab --help`

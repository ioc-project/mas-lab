<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-show(1)

## NAME

`mas-lab benchmark show` — Show a benchmark run

## SYNOPSIS

```text
mas-lab benchmark show [OPTIONS]
```

## DESCRIPTION

Show a benchmark run.

## OPTIONS

| Flag | Meaning |
|------|------|
| `TARGET` | Positional argument. |
| `WHAT` | Positional argument. |
| `-r`, `--recursive` | Show a lab or experiment directory as a hierarchical tree. TARGET must be a path to a lab directory or experiment YAML. |
| `-o`, `--output-dir` | — |
| `--trace-cache` | Override trace-cache directory used for cache coverage display (CLI > MAS_TRACE_CACHE > $XDG_CACHE_HOME/mas/traces). |
| `--format` | — |
| `-v`, `--verbose` | -v/-vv: reserved  -vvv: show pipeline DAG with artifact lineage |
| `-d`, `--depth` | Limit tree depth (requires -r).  exp: experiment headers only  scenario: + scenarios with counts (default)  item: + expanded item list  run: + per-run status |
| `--scenario` | Filter tree to a specific scenario ID (requires --recursive). |
| `--item` | Filter tree to a specific dataset item ID (requires --recursive). |
| `--run` | Filter tree to a specific run index (requires --recursive). |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab benchmark migrate`
- `mas-lab --help`

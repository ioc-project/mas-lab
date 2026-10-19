<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-pipe-run(1)

## NAME

`mas-lab pipe run` — Execute an inline or YAML processor pipeline

## SYNOPSIS

```text
mas-lab pipe run [OPTIONS]
```

## DESCRIPTION

Execute an inline or YAML processor pipeline.

## OPTIONS

| Flag | Meaning |
|------|------|
| `PIPELINE_TOKENS` | Positional argument. |
| `--file`, `-f` | Load pipeline from a YAML file instead of inline tokens. |
| `--dry-run` | Print the execution plan without running anything. |
| `--strict` | Abort on artifact type mismatches between steps. |
| `--verbose`, `-v` | Print per-step progress to stderr. |
| `--import` | Import a Python module before resolving processors (registers plugins). |

## SEE ALSO

- `mas-lab pipe`
- `mas-lab pipe list`
- `mas-lab pipe schema`
- `mas-lab pipe validate`
- `mas-lab --help`

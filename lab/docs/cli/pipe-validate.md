<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-pipe-validate(1)

## NAME

`mas-lab pipe validate` — Validate a processor pipeline

## SYNOPSIS

```text
mas-lab pipe validate [OPTIONS]
```

## DESCRIPTION

Validate a processor pipeline.

## OPTIONS

| Flag | Meaning |
|------|------|
| `PIPELINE_TOKENS` | Positional argument. |
| `--file`, `-f` | Validate a YAML pipeline file. |
| `--strict` | Also check artifact type compatibility between steps. |

## SEE ALSO

- `mas-lab pipe`
- `mas-lab pipe run`
- `mas-lab pipe list`
- `mas-lab pipe schema`
- `mas-lab --help`

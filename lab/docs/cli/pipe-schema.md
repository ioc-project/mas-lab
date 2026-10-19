<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-pipe-schema(1)

## NAME

`mas-lab pipe schema` — Export element registry as JSON schema

## SYNOPSIS

```text
mas-lab pipe schema [OPTIONS]
```

## DESCRIPTION

Export element registry as JSON schema.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--element` | Emit schema for one element only. |
| `--output`, `-o` | Write JSON to FILE instead of stdout. |
| `--format` | Output format. |
| `--import` | Import a Python module to register additional processors. |

## SEE ALSO

- `mas-lab pipe`
- `mas-lab pipe run`
- `mas-lab pipe list`
- `mas-lab pipe validate`
- `mas-lab --help`

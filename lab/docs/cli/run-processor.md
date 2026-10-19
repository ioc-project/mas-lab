<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-run-processor(1)

## NAME

`mas-lab run processor` — Run one processor

## SYNOPSIS

```text
mas-lab run processor [OPTIONS]
```

## DESCRIPTION

Run one processor.

## OPTIONS

| Flag | Meaning |
|------|------|
| `PROCESSOR` | Positional argument. |
| `BINDINGS` | Positional argument. |
| `--list` | List all registered processors and exit. |
| `--import` | Import a Python module before resolving the processor. |
| `--verbose`, `-v` | Show full tracebacks on errors. |

## SEE ALSO

- `mas-lab run`
- `mas-lab run pipeline`
- `mas-lab run pipeline step`
- `mas-lab --help`

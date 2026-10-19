<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot-trajectory(1)

## NAME

`mas-lab plot trajectory` — Delegation-flow diagram

## SYNOPSIS

```text
mas-lab plot trajectory [OPTIONS]
```

## DESCRIPTION

Delegation-flow diagram.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--format` | Output format. |
| `--no-prompts` | Omit task/prompt text from diagram edges. |
| `--output`, `-o` | Write output to FILE (default: <source-dir>/trajectory.<ext>). |
| `--highlight` | Highlight a delegation (amber + ⚠️).  Repeat to flag multiple.  Each value is a correlation-id prefix (e.g. 'f19445b6') or a 1-based delegation index (e.g. '2'). |

## SEE ALSO

- `mas-lab plot`
- `mas-lab plot multilevel-trajectory`
- `mas-lab plot communication-flow`
- `mas-lab plot list`
- `mas-lab --help`

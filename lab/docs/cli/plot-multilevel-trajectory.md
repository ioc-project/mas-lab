<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot-multilevel-trajectory(1)

## NAME

`mas-lab plot multilevel-trajectory` — Multilevel swimlane diagram

## SYNOPSIS

```text
mas-lab plot multilevel-trajectory [OPTIONS]
```

## DESCRIPTION

Multilevel swimlane from `events.jsonl` or a lab shorthand. Pipeline `kg:native-plots` runs the same plotter per run.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--format` | Output format. |
| `--title` | Diagram title. |
| `--width-mode` | Column-width strategy. |
| `--no-user-actors` | Suppress person-icon user nodes. |
| `--output`, `-o` | Write output to FILE (default: <source-dir>/multilevel-trajectory.<ext>). |

## PIPELINE

`kg:native-plots`

## SEE ALSO

- `mas-lab plot`
- `mas-lab plot trajectory`
- `mas-lab plot communication-flow`
- `mas-lab plot list`
- `mas-lab --help`

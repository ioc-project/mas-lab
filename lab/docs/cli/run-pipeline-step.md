<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-run-pipeline-step(1)

## NAME

`mas-lab run pipeline step` — Run named pipeline steps on existing output

## SYNOPSIS

```text
mas-lab run pipeline step [OPTIONS]
```

## DESCRIPTION

Run named pipeline steps on existing output.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT` | Positional argument. |
| `STEP...` | Positional argument. |
| `--output-dir`, `-o` | Override the output directory (default: last run for this experiment). |
| `--force`, `-f` | Re-run even if step outputs are already cached. |
| `--set` | Override a step config value.  Format: step_name.key=value |

## SEE ALSO

- `mas-lab run`
- `mas-lab run processor`
- `mas-lab run pipeline`
- `mas-lab --help`

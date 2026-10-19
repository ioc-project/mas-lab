<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-clean(1)

## NAME

`mas-lab benchmark clean` — Remove benchmark run data

## SYNOPSIS

```text
mas-lab benchmark clean [OPTIONS]
```

## DESCRIPTION

Remove benchmark run data.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT_YAML` | Positional argument. |
| `--scenario` | Scenario ID(s) to clean. Repeatable. If omitted, ALL scenarios are cleaned. |
| `--dry-run` | Print what would be deleted without deleting anything. |
| `--keep-traces` | Delete only the scenario run dirs; keep the global trace-cache entries. |
| `-o`, `--output-dir` | Override the output dir directly (use when experiment.yaml is unavailable; pass '-' as EXPERIMENT_YAML). |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`

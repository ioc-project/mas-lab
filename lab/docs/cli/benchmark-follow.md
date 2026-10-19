<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-follow(1)

## NAME

`mas-lab benchmark follow` — Follow progress of an active run

## SYNOPSIS

```text
mas-lab benchmark follow [OPTIONS]
```

## DESCRIPTION

Follow progress of an active run.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT_YAML` | Positional argument. |
| `--interval` | Polling interval in seconds. |
| `--once` | Print a single progress snapshot and exit immediately. |
| `-o`, `--output-dir` | Override the experiment output directory. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`

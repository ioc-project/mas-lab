<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-update(1)

## NAME

`mas-lab benchmark update` — Update benchmark metadata

## SYNOPSIS

```text
mas-lab benchmark update [OPTIONS]
```

## DESCRIPTION

Update benchmark metadata.

## OPTIONS

| Flag | Meaning |
|------|------|
| `BENCHMARK_ID` | Positional argument. |
| `--name` | Set benchmark name. |
| `--tags` | Set tags (comma-separated, replaces all). |
| `--add-tag` | Add a single tag. |
| `--remove-tag` | Remove a single tag. |
| `-o`, `--output-dir` | — |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark rename`
- `mas-lab benchmark migrate`
- `mas-lab --help`

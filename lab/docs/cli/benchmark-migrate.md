<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-migrate(1)

## NAME

`mas-lab benchmark migrate` — Migrate benchmark output layout

## SYNOPSIS

```text
mas-lab benchmark migrate [OPTIONS]
```

## DESCRIPTION

Migrate benchmark output layout.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE_DIR` | Positional argument. |
| `TARGET_DIR` | Positional argument. |
| `--no-symlink` | Do not replace SOURCE_DIR with a symlink after copying. |
| `--dry-run` | Show what would be done without touching the filesystem. |
| `--mas-data-root` | Override the labs_root() root used for auto-derived targets (default: labs_root()). |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`

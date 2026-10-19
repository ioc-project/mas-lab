<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-import(1)

## NAME

`mas-lab benchmark import` — Import a benchmark archive

## SYNOPSIS

```text
mas-lab benchmark import [OPTIONS]
```

## DESCRIPTION

Import a benchmark archive.

## OPTIONS

| Flag | Meaning |
|------|------|
| `TARBALL` | Positional argument. |
| `--output-dir` | Directory to restore the benchmark output into (default: labs_root()/benchmarks/<benchmark_id>). |
| `--trace-cache` | Directory to restore trace-cache entries into (default: trace_cache() or MAS_TRACE_CACHE). |
| `--dry-run` | Show what would be extracted without touching the filesystem. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-export(1)

## NAME

`mas-lab benchmark export` — Export a benchmark archive

## SYNOPSIS

```text
mas-lab benchmark export [OPTIONS]
```

## DESCRIPTION

Export a benchmark archive.

## OPTIONS

| Flag | Meaning |
|------|------|
| `BENCHMARK_ID` | Positional argument. |
| `-o`, `--output` | Output .tar.gz path (default: <benchmark_id>.tar.gz in cwd). |
| `--no-trace-cache` | Exclude trace-cache entries from the archive (smaller, not re-runnable). |
| `--output-dir` | Benchmark runs root (default: auto-detect). |
| `--trace-cache` | Trace-cache directory to read from (default: $XDG_CACHE_HOME/mas/traces). |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-export(1)

## NAME

`mas-lab benchmark export` — Export a benchmark archive

## SYNOPSIS

```text
mas-lab benchmark export [OPTIONS] BENCHMARK_ID
```

## DESCRIPTION

Pack a benchmark run into a portable `.tar.gz`. `BENCHMARK_ID` accepts a
benchmark id, a short id, an experiment name, or `last`. The archive contains:

- `MANIFEST.json`: import metadata. Every path in it is relative to the
  archive; no local absolute path is stored.
- `<run_dir>/`: the benchmark output (`metadata.yaml`, `results.csv`, per-run
  artifacts).
- `trace-cache/<hash>/`: the trace-cache entries that `results.csv` references
  (omitted with `--no-trace-cache`).
- `source/`: a minimal bundle of the local experiment inputs (experiment YAML,
  referenced dataset YAMLs, referenced app tree).

The `source/` bundle is an allowlist, not a copy of the tree. A file is
included only if it sits under one of `agents`, `apps`, `configs`, `data`,
`datasets`, `experiments`, `overlays`, `pipelines`, `prompts`, `skills`,
`tools` and has one of the suffixes `.yaml .yml .json .toml .csv .md .txt
.py .sh .jinja .j2`. Directories such as `.git`, `.venv`, `node_modules` and
cache folders are skipped, along with `.env*` files, SSH keys, and
certificate or key files (`.pem .key .crt .cer .csr .p12 .pfx .jks .der`).
If a sensitive file still reaches the bundle, the export fails and writes no
archive.

See [Sharing benchmarks](../sharing-benchmarks.md).

## OPTIONS

| Flag | Meaning |
| ------ | ------ |
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

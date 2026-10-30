<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-import(1)

## NAME

`mas-lab benchmark import` — Import a benchmark archive

## SYNOPSIS

```text
mas-lab benchmark import [OPTIONS] TARBALL
```

## DESCRIPTION

Restore an archive made by `benchmark export`. The benchmark output goes to
`--output-dir`, trace-cache entries go to `--trace-cache`, and `metadata.yaml`
is patched so the run points at the restored paths. A bundled `source/` tree
is restored under `<output_dir>/_repro/source`, and `experiment_yaml_path`
points there.

The archive is treated as untrusted input. Import skips and logs any member
with an absolute path or a `..` component, or whose resolved destination or
link target falls outside its extraction root. A `MANIFEST.json` path that
resolves outside its root aborts the import.

See [Sharing benchmarks](../sharing-benchmarks.md).

## OPTIONS

| Flag | Meaning |
| ------ | ------ |
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

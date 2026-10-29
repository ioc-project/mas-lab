<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-import-hf(1)

## NAME

`mas-lab benchmark import-hf` — Download a benchmark archive from a Hugging Face dataset repo

## SYNOPSIS

```text
mas-lab benchmark import-hf [OPTIONS] PATH_IN_REPO
```

## DESCRIPTION

Download a benchmark archive from a Hugging Face dataset repo and restore it locally.
Pass `--token` or `--token-file`, or set `HF_TOKEN` (ctl reads the
environment). `--dry-run` still uses `HF_TOKEN` when it is set, and
does not require a token when it is not.

## OPTIONS

| Flag | Meaning |
|------|------|
| `PATH_IN_REPO` | Positional path within the dataset repo. |
| `--repo-id` | Hugging Face dataset repo id, e.g. `<user-or-org>/<repo>`. |
| `--revision` | Branch or immutable commit SHA to pin the import to. |
| `--sha256` | Expected SHA-256 digest for validation before import. |
| `--output-dir` | Directory to restore the benchmark output into. |
| `--trace-cache` | Directory to restore trace-cache entries into. |
| `--dry-run` | Show the download/import plan without writing outputs. |
| `--token` | Hugging Face access token. Overrides `--token-file` and `HF_TOKEN`. |
| `--token-file` | Read the access token from a file. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark export`
- `mas-lab benchmark import`
- `mas-lab --help`

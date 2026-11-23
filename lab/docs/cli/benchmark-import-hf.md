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

Download a benchmark archive from a Hugging Face dataset repo and restore it
locally through the same flow as `benchmark import`.
Pass `--token` or `--token-file`, or store the token as secret `hf`
(`mas-ctl secrets set hf`). `HF_TOKEN` is not read; `mas-ctl secrets import-env`
copies it into the store as `hf`. `--dry-run` uses a token when one resolves
and does not require one when none does.

Pin the import for a reproducible result: `--revision` takes a commit SHA
(as printed by `publish-hf`), and `--sha256` checks the downloaded tarball
before anything is extracted. The value may carry a `sha256:` prefix. A
mismatch aborts the import. If `--sha256` is omitted the digest is not
checked, and without `--revision` the repo's default branch is used.

## OPTIONS

| Flag | Meaning |
| ------ | ------ |
| `PATH_IN_REPO` | Positional path within the dataset repo. |
| `--repo-id` | Hugging Face dataset repo id, e.g. `<user-or-org>/<repo>`. |
| `--revision` | Branch or immutable commit SHA to pin the import to. |
| `--sha256` | Expected SHA-256 digest for validation before import. |
| `--output-dir` | Directory to restore the benchmark output into. |
| `--trace-cache` | Directory to restore trace-cache entries into. |
| `--dry-run` | Show the download/import plan without writing outputs. |
| `--token` | Hugging Face access token. Overrides `--token-file` and secret `hf`. |
| `--token-file` | Read the access token from a file. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark export`
- `mas-lab benchmark import`
- `mas-lab --help`

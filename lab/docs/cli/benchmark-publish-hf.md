<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-publish-hf(1)

## NAME

`mas-lab benchmark publish-hf` — Upload a benchmark archive to a Hugging Face dataset repo

## SYNOPSIS

```text
mas-lab benchmark publish-hf [OPTIONS] TARBALL
```

## DESCRIPTION

Upload a benchmark archive tarball (from `benchmark export`) to a Hugging Face
**dataset** repo, unchanged. The repo is created if it does not exist.
Pass `--token` or `--token-file`, or set `HF_TOKEN` (ctl reads the
environment).

By default the repo is private and the upload opens a review PR instead of
writing to the default branch. The command prints the resulting commit SHA;
pass it to `benchmark import-hf --revision` to pin an import to that exact
revision. The default remote path is
`benchmarks/<experiment>/<short_id>/bundle.tar.gz`.

The command does not print a digest of the tarball. Compute one with
`shasum -a 256 RUN.tar.gz` and share it with the archive.

## OPTIONS

| Flag | Meaning |
| ------ | ------ |
| `TARBALL` | Positional tarball to publish. |
| `--repo-id` | Hugging Face dataset repo id, e.g. `<user-or-org>/<repo>`. |
| `--path-in-repo` | Destination path inside the dataset repo. |
| `--visibility` | Repo visibility: `private` or `public` (default: `private`). |
| `--revision` | Branch or commit SHA to target (default: `main`). |
| `--create-pr / --no-create-pr` | Create a reviewable PR-style revision instead of writing directly to the default branch (default: enabled). |
| `--token` | Hugging Face access token. Overrides `--token-file` and `HF_TOKEN`. |
| `--token-file` | Read the access token from a file. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark export`
- `mas-lab benchmark import`
- `mas-lab --help`

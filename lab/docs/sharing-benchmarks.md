<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Sharing benchmarks

A benchmark run can be packed into one `.tar.gz`, published to a Hugging Face
dataset repo, and restored on another machine. Commands:
[cli-reference.md](cli-reference.md#portable-benchmark-archives).

```bash
mas-lab benchmark export last -o run.tar.gz
mas-lab benchmark publish-hf run.tar.gz --repo-id ORG/REPO
mas-lab benchmark import-hf PATH_IN_REPO --repo-id ORG/REPO --revision SHA --sha256 DIGEST
```

## Archive layout

```text
MANIFEST.json            import metadata, archive-relative paths only
<run_dir>/               benchmark output: metadata.yaml, results.csv, plots, per-run artifacts
trace-cache/<hash>/      trace-cache entries referenced by results.csv
source/                  minimal experiment inputs needed to understand or re-run
```

`--no-trace-cache` leaves out `trace-cache/`. Such an archive is smaller but
cannot be re-run from cache.

Raw traces often live outside the run directory, in the content-addressed
trace cache (`$XDG_CACHE_HOME/mas/traces`, or `MAS_TRACE_CACHE`). The run
directory then holds links into that cache. Export copies the referenced
entries into the archive; if the cache was cleaned before export, they are
missing, and only a re-run recreates them.

## What the `source/` bundle contains

The bundle is an allowlist. A file is included only when all of these hold:

- It is reachable from the experiment YAML (referenced datasets and app tree).
- When the experiment sits in a workspace, its first path component is one of
  `agents`, `apps`, `configs`, `data`, `datasets`, `experiments`, `overlays`,
  `pipelines`, `prompts`, `skills`, `tools`.
- Its suffix is one of `.yaml`, `.yml`, `.json`, `.toml`, `.csv`, `.md`,
  `.txt`, `.py`, `.sh`, `.jinja`, `.j2`.
- No directory on the path is `.git`, `.venv`, `node_modules`, `__pycache__`,
  or a tool cache (`.pytest_cache`, `.mypy_cache`, `.ruff_cache`).
- It is not a sensitive file: `.env*`, `id_rsa`, `id_dsa`, `id_ecdsa`,
  `known_hosts`, `authorized_keys`, certificate or key suffixes (`.pem`,
  `.key`, `.crt`, `.cer`, `.csr`, `.p12`, `.pfx`, `.jks`, `.der`), or a name
  containing both `private` and `key`.

Excluded directories are pruned during the walk, so nothing below them is
visited. If a sensitive path is found in the bundle anyway, export stops with
an error and writes no archive.

`MANIFEST.json` records only archive-relative paths. Local absolute paths are
not stored.

## Publishing

`publish-hf` uploads the tarball unchanged to a dataset repo.

- The token comes from `--token`, `--token-file`, or `HF_TOKEN`, in that
  order.
- A repo it creates is **private** by default (`--visibility public` changes
  that).
- The upload opens a **review PR** by default (`--no-create-pr` commits
  directly to `--revision`, default `main`).
- The default remote path is `benchmarks/<experiment>/<short_id>/bundle.tar.gz`.
- The command prints the resulting commit SHA.

Before making a repo public, review the archive. The allowlist and the
sensitive-file check lower the risk; they do not read file contents. Prompts,
datasets, and traces may still hold data you do not want to publish.

## Restoring

`import-hf` downloads the file and runs the same restore as `benchmark import`.

For a reproducible import:

```bash
shasum -a 256 run.tar.gz        # author, before or after upload
mas-lab benchmark import-hf PATH_IN_REPO --repo-id ORG/REPO \
  --revision <commit-sha> --sha256 <digest>
```

- `--revision` pins the download to one commit. Without it, the repo's
  default branch is used, which can change.
- `--sha256` checks the downloaded tarball before extraction and aborts on a
  mismatch. Without it, no digest is checked.
- `--dry-run` previews the import and writes no outputs.

## Trust model

An archive is untrusted input, even one you exported yourself earlier.
`import` and `import-hf`:

- skip any member whose name is absolute, contains `..`, or resolves outside
  its extraction root;
- skip any link whose target resolves outside its extraction root;
- abort when a path read from `MANIFEST.json` resolves outside its root;
- restore bundled sources under `<output_dir>/_repro/source` and never
  overwrite files elsewhere.

Importing does not execute anything from the archive. Re-running a restored
experiment does run its bundled Python, shell, and YAML pipeline steps, so read
them first when the archive comes from someone else.

## See also

- [cli-reference.md](cli-reference.md#portable-benchmark-archives): flags
- [benchmark.md](benchmark.md): benchmark guide
- [user-config.md](../../docs/user-config.md): trace cache and paths

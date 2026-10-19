<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-validate(1)

## NAME

`mas-lab validate` — Validate YAML manifests

## SYNOPSIS

```text
mas-lab validate [OPTIONS]
```

## DESCRIPTION

Validate YAML manifests.

## OPTIONS

| Flag | Meaning |
|------|------|
| `MANIFEST...` | Positional argument. |
| `--kind`, `-k` | Manifest kind (auto-detected from top-level key when omitted). |
| `--base-dir`, `-b` | Base directory for resolving relative-path references (defaults to the manifest's own directory). |
| `--strict`, `--no-strict` | Error on violations (--strict, default) or warn only (--no-strict). |
| `--resolve-refs`, `--no-resolve-refs` | Check that referenced files exist on disk (--resolve-refs, default). Use --no-resolve-refs for template manifests or CI without a full repo checkout. |

## SEE ALSO

- `mas-lab`
- `mas-lab init`
- `mas-lab config`
- `mas-lab check`
- `mas-lab check-config`
- `mas-lab serve`
- `mas-lab --help`

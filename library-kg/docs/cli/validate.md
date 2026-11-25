<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-kg-validate(1)

## NAME

`mas-lab kg validate` — Validate a KG document

## SYNOPSIS

```text
mas-lab kg validate [OPTIONS]
```

## DESCRIPTION

Validate `kg.json` (structural + optional SHACL).

## OPTIONS

| Flag | Meaning |
|------|------|
| `KG_PATH` | Positional argument. |
| `--ontology-path` | — |
| `--strict` | — |
| `--warning-verbosity` | Control warning detail level in validate output. |
| `--enable-extension-layer` | Enable optional extension-layer validation checks. |
| `--list-checks` | List available checks and show which ones run by default. |
| `--check` | Run only this check. Repeat to run a subset. |
| `--skip-check` | Disable one default check. Repeat to skip multiple checks. |

## PIPELINE

`kg:native-to-kg`

## SEE ALSO

- `mas-lab kg`
- `mas-lab kg normalize`
- `mas-lab kg verify`
- `mas-lab kg otel-to-events`
- `mas-lab kg otel-to-kg`
- `mas-lab kg push`
- `mas-lab --help`

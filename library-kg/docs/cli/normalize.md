<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-kg-normalize(1)

## NAME

`mas-lab kg normalize` — Native events or OTel spans → kg.json

## SYNOPSIS

```text
mas-lab kg normalize [OPTIONS]
```

## DESCRIPTION

Build a KG document from native `events.jsonl` (or `--otel` spans). Same steps as `kg:native-to-kg`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EVENTS_PATH` | Positional argument. |
| `--otel` | — |
| `--run-id` | — |
| `--session-id` | — |
| `--split-session-id`, `--no-split-session-id` | — |
| `--app-name` | Override Session.appName in normalized output. |
| `--output-dir` | — |
| `--output`, `-o` | — |
| `--ontology-path` | — |
| `--dry-run` | — |
| `--categories` | Comma-separated opt-in categories added to the core output: capabilities, annotations, worker, thinking, or all (default: none). |
| `--all-attributes`, `--no-all-attributes` | Keep native-only node fields. Default keeps SCOPE attributes only. |

## PIPELINE

`kg:native-to-kg`

## SEE ALSO

- `mas-lab kg`
- `mas-lab kg validate`
- `mas-lab kg verify`
- `mas-lab kg otel-to-events`
- `mas-lab kg otel-to-kg`
- `mas-lab kg push`
- `mas-lab --help`

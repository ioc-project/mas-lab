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

See `mas-lab kg normalize --help` for the current flag list (Click help is the source of truth).

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

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-kg-otel-to-events(1)

## NAME

`mas-lab kg otel-to-events` — OTel spans → clickhouse-shaped JSONL

## SYNOPSIS

```text
mas-lab kg otel-to-events [OPTIONS]
```

## DESCRIPTION

Rewrite OTel spans to clickhouse-shaped JSONL (first stage of otel-to-kg).

## OPTIONS

See `mas-lab kg otel-to-events --help` for the current flag list (Click help is the source of truth).

## PIPELINE

`kg:otel-to-kg`

## SEE ALSO

- `mas-lab kg`
- `mas-lab kg normalize`
- `mas-lab kg validate`
- `mas-lab kg verify`
- `mas-lab kg otel-to-kg`
- `mas-lab kg push`
- `mas-lab --help`

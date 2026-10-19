<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-dump(1)

## NAME

`mas-lab telemetry dump` — Read spans back from ClickHouse

## SYNOPSIS

```text
mas-lab telemetry dump [OPTIONS]
```

## DESCRIPTION

Fetch a session (or trace) from ClickHouse `otel_traces` to JSONL. Requires the `[clickhouse]` extra. Same `clickhouse_dump` step as `telemetry:dump-session`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SESSION_ID` | Positional argument. |
| `--by` | Query key: 'session' → ClickHouse session_id column, 'run' → SpanAttributes['mas.run.id'], 'trace' → TraceId column (hex, with or without 0x prefix). |
| `--app-name` | Optional ServiceName filter. |
| `-o`, `--output` | Output file path for otel_sdk_spans.jsonl. |
| `--host` | ClickHouse host (overrides config/env). |
| `--port` | ClickHouse HTTP port. |
| `--user` | ClickHouse user. |
| `--password` | ClickHouse password. |
| `--database` | ClickHouse database. |
| `--table` | OTel traces table. |
| `--json-output` | Print summary as JSON. |

## PIPELINE

`telemetry:dump-session`

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry convert`
- `mas-lab telemetry verify`
- `mas-lab telemetry inspect`
- `mas-lab telemetry push`
- `mas-lab telemetry compare`
- `mas-lab --help`

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-sessions-list(1)

## NAME

`mas-lab telemetry sessions list` — List native telemetry sessions

## SYNOPSIS

```text
mas-lab telemetry sessions list [OPTIONS]
```

## DESCRIPTION

List sessions in the native telemetry / ClickHouse store.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--app-name` | Filter by ServiceName. |
| `--attr-key` | SpanAttribute key holding the session ID (e.g. 'session.id'). |
| `--limit` | Max rows to return. |
| `--host` | ClickHouse host. |
| `--port` | ClickHouse HTTP port. |
| `--user` | ClickHouse user. |
| `--password` | ClickHouse password. |
| `--database` | ClickHouse database. |
| `--table` | OTel traces table. |
| `--json-output` | Print result as JSON. |

## SEE ALSO

- `mas-lab telemetry sessions`
- `mas-lab telemetry traces list`

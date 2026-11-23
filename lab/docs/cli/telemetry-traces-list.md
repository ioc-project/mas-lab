<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-traces-list(1)

## NAME

`mas-lab telemetry traces list` — List native telemetry traces

## SYNOPSIS

```text
mas-lab telemetry traces list [OPTIONS]
```

## DESCRIPTION

List traces in the native telemetry / ClickHouse store.

The ClickHouse password is the `password` ref of the `clickhouse` user connection
(for example `password: {secret: clickhouse}`); store its value with `mas-ctl secrets set clickhouse`.
With none declared, the password is empty.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--app-name` | Filter by ServiceName. |
| `--limit` | Max rows to return. |
| `--host` | ClickHouse host. |
| `--port` | ClickHouse HTTP port. |
| `--user` | ClickHouse user. |
| `--database` | ClickHouse database. |
| `--table` | OTel traces table. |
| `--json-output` | Print result as JSON. |

## SEE ALSO

- `mas-lab telemetry traces`
- `mas-lab telemetry sessions list`

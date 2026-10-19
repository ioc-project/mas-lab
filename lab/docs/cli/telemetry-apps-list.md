<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-apps-list(1)

## NAME

`mas-lab telemetry apps list` — List apps in ClickHouse otel_traces

## SYNOPSIS

```text
mas-lab telemetry apps list [OPTIONS]
```

## DESCRIPTION

List ServiceName × application_id pairs from the OTel ClickHouse table.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--host` | ClickHouse host (overrides config/env). |
| `--port` | ClickHouse HTTP port (default 8123). |
| `--user` | ClickHouse user (overrides config/env). |
| `--password` | ClickHouse password (overrides config/env). |
| `--database` | ClickHouse database (overrides config/env). |
| `--table` | OTel traces table name. |
| `--start-service` | Start the 'clickhouse' service via mas-lab before querying. |
| `--json-output` | Print result as JSON list. |

## SEE ALSO

- `mas-lab telemetry apps`
- `mas-lab telemetry list-apps`
- `mas-lab telemetry`

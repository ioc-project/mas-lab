<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-verify(1)

## NAME

`mas-lab telemetry verify` — Structural + SpanSpec verification

## SYNOPSIS

```text
mas-lab telemetry verify [OPTIONS]
```

## DESCRIPTION

Run structural, JSON-schema, and SpanSpec checks on a spans file. Same `verify_otel` step as `telemetry:native-to-otel-json`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SPANS_FILE` | Positional argument. |
| `--fail`, `--no-fail` | Exit with code 1 if any check fails. |
| `--json-output` | Print report as JSON. |

## PIPELINE

`telemetry:native-to-otel-json`

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry convert`
- `mas-lab telemetry inspect`
- `mas-lab telemetry push`
- `mas-lab telemetry dump`
- `mas-lab telemetry compare`
- `mas-lab --help`

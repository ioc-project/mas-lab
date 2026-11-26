<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-compare(1)

## NAME

`mas-lab telemetry compare` — Structural parity of two span files

## SYNOPSIS

```text
mas-lab telemetry compare [OPTIONS]
```

## DESCRIPTION

Structural parity of a reference spans file vs a candidate. Same `compare_otel_spans` step as `telemetry:otel-span-parity`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `REFERENCE` | Positional argument. |
| `CANDIDATE` | Positional argument. |
| `--strict`, `--no-strict` | — |

## PIPELINE

`telemetry:otel-span-parity`

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry convert`
- `mas-lab telemetry verify`
- `mas-lab telemetry inspect`
- `mas-lab telemetry push`
- `mas-lab telemetry dump`
- `mas-lab --help`

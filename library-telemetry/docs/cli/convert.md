<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-convert(1)

## NAME

`mas-lab telemetry convert` — events.jsonl → otel_sdk_spans.jsonl

## SYNOPSIS

```text
mas-lab telemetry convert [OPTIONS]
```

## DESCRIPTION

Replay `events.jsonl` through the library converter to OTel SDK JSONL. Same work as pipeline `events_to_otel` + `verify_otel`.

## OPTIONS

See `mas-lab telemetry convert --help` for the current flag list (Click help is the source of truth).

## PIPELINE

`telemetry:native-to-otel-json`

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry verify`
- `mas-lab telemetry inspect`
- `mas-lab telemetry push`
- `mas-lab telemetry dump`
- `mas-lab telemetry compare`
- `mas-lab --help`

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

| Flag | Meaning |
|------|------|
| `EVENTS_PATH` | Positional argument. |
| `--output`, `-o` | — |
| `--service-name` | MAS name when --app-name is unset and events do not carry one. |
| `--app-name` | MAS name (application_id and service.name). Required unless events already set it. |
| `--shift-to-now` | Translate timestamps so the earliest event is now (relative gaps kept). |
| `--new-session-id` | Allocate a new session UUID, distinct from the original run. |
| `--governance`, `--no-governance` | Include the governance export layer (default: off). |
| `--layer` | Enable only these export layers (repeatable). Default: structure, execution, semantic, provenance. |
| `--realtime` | Emit incremental topology.node.*/tool.*/llm.* signal spans as each event replays, instead of the single end-of-run *.graph span (realtime replaces it, same as the live otel plugin's realtime mode). |

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

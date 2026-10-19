<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-push(1)

## NAME

`mas-lab telemetry push` — serialize spans to an OTLP collector

## SYNOPSIS

```text
mas-lab telemetry push SPANS_FILE [--endpoint URL] [--infra NAME]
    [--app-name NAME] [--dry-run] [--shift-to-now] [--new-session-id]
```

## DESCRIPTION

Thin wrapper over `run_push_otlp` / `collector.push_file`. Input is detected
from the first JSONL record:

* **OTel SDK spans** (`context.trace_id`) — streamed in HTTP batches. This is
  the large-file path; the file is never `read_text`'d as a whole.
* **Native events** (`kind` / `timestamp`) — converted with the stateful replay
  helper, then pushed (fallback).

Same work as pipeline `telemetry:export-otel-collector` (`export_otel`).

## OPTIONS

| Flag | Meaning |
|------|------|
| `TRACE_FILE` | Positional argument. |
| `--endpoint`, `-e` | OTLP HTTP collector endpoint (must accept POST /v1/traces JSON). |
| `--service-name`, `-s` | service.name resource attribute. |
| `--batch-size` | Number of spans per HTTP request. |
| `--app-name` | Override service.name resource attribute (takes precedence over --service-name). Useful to tag a re-exported trace with a distinct app identity, e.g. 'mas-lab-import-test-001'. Default: no override. |
| `--dry-run` | Convert and print a summary but do not send anything. |
| `--json-output` | Print result as JSON (useful for scripting). |

## PIPELINE

`telemetry:export-otel-collector` → `library-telemetry/pipelines/export-otel-collector.yaml`

## SEE ALSO

- [mas-lab-telemetry](index.md)
- [mas-lab-telemetry-convert](convert.md)
- [mas-lab-telemetry-dump](dump.md)
- [otlp-collector.md](../otlp-collector.md)

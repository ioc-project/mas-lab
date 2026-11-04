# Telemetry pipeline library

Reusable `kind: Pipeline` manifests shipped with **library-telemetry**.

The conversion, verification, and collector-serialization logic lives in
`mas.library.telemetry.*`. The `mas-lab` CLI and `mas-lab-graph` step adapters
(`events_to_otel`, `verify_otel`, `compare_otel_spans`, `export_otel`) are
thin shortcuts that call the same library functions — so a pipeline step and
its matching `mas-telemetry` subcommand run identical code. `events_to_otel`
and `export_otel` carry a temporary "-v2" suffix because the plain names
collide with library-lab's own same-named generic steps (last-registered wins
silently) — drop the suffix once this library supersedes those in the
opensource merge.

| Pipeline step type | Library entry point |
|--------------------|---------------------|
| `events_to_otel`   | `steps.run_convert` / `conversion.replay.replay_events_file` |
| `verify_otel`         | `steps.run_verify_spans` / `verification.verify_otel_file` |
| `compare_otel_spans`  | `steps.run_compare_spans` / `verification.compare_otel_span_files` |
| `export_otel` (OTLP) | `steps.run_push_otlp` / `collector.push_file` |

## Referencing pipelines

Install `library-telemetry` in the workspace venv. The package registers
scheme **`telemetry`** via `mas.runtime.manifest_libraries` — no
`config.yaml` entry required.

```yaml
run:
  post:
    - ref: telemetry:native-to-otel-json
```

CLI:

```bash
mas-lab benchmark run experiment.yaml \
  --pipeline run:telemetry:native-to-otel-json
```

Or drive the same steps directly:

```bash
mas-lab telemetry convert runs/item0/traces/events.jsonl -o /tmp/otel_sdk_spans.jsonl --app-name my-app
mas-lab telemetry verify  /tmp/otel_sdk_spans.jsonl --level L3 --fail
mas-lab telemetry push    /tmp/otel_sdk_spans.jsonl --endpoint http://localhost:4318 \
  --shift-to-now --new-session-id
```

## Pipeline catalog

| File | Output | Purpose |
|------|--------|---------|
| `native-to-otel-json.yaml` | `otel_sdk_spans_replay.jsonl` | Offline `events.jsonl` → OTel JSONL + SpanSpec verify |
| `native-to-kg-via-otel.yaml` | `kg_otel.json`, `parity_report.json` | Full OTel replay round-trip → KG (via `library-kg`) + compare to native `kg` |
| `otel-plugin-to-kg.yaml` | `kg_otel_plugin.json` | Live `otel_sdk_spans.jsonl` → KG |
| `observe-plugin-to-kg.yaml` | `kg_observe.json` | Live `observe_sdk_spans.jsonl` → KG |
| `otel-span-parity.yaml` | `otel_parity_report.json` | Replay reference vs live plugin span JSONL |
| `export-otel-collector.yaml` | OTLP export | Push spans to a collector (`--infra` with OTLP endpoint) |

> KG-folding steps (`normalize_otel`, `normalize_events`, `validate_kg`,
> `compare_kg`) belong to **library-kg** / **mas-lab-graph**; the
> `*-to-kg-via-otel` and `*-plugin-to-kg` pipelines compose telemetry conversion
> with those KG steps.

## Native → OTel → collector

```yaml
run:
  post:
    - ref: telemetry:native-to-otel-json
    - ref: telemetry:export-otel-collector
```

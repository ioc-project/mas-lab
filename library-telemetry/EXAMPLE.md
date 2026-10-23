# Worked example: native trace to verified OTel spans

This walkthrough takes one multi-agent run, a travel planner, from its native `events.jsonl` to OpenTelemetry (OTel) spans that a collector and the SCOPE can ingest. It is for readers who want to run the full convert, verify, push, read-back chain once.

Sample input: [`examples/travel-planner.events.jsonl`](examples/travel-planner.events.jsonl), a 14-event trace where a `moderator` routes to an `itinerary_agent` and a `budget_agent`.

```bash
uv pip install -e "library-telemetry[convert,verify]"
mas-lab telemetry convert examples/travel-planner.events.jsonl -o traces/otel_sdk_spans.jsonl
mas-lab telemetry verify traces/otel_sdk_spans.jsonl --level L3
```

The chain:

```
events.jsonl --1 convert--> otel_sdk_spans.jsonl --2 verify--> 3 serialize --> OTel collector --4 dump--> spans back
```

Steps 1 and 2 run as a [golden test](tests/test_example_story.py), so their outputs below are real. Steps 3 and 4 need Docker. Each step is shown as a `mas-lab` command and as the pipeline behind it. See the [install matrix](README.md#install) for the optional extras.

## 1. Convert

```bash
mas-lab telemetry convert examples/travel-planner.events.jsonl -o traces/otel_sdk_spans.jsonl
```

The command prints the span count:

```json
{
  "spans": 15,
  "output": "traces/otel_sdk_spans.jsonl"
}
```

With the default `observe_sdk` profile, the span tree uses the names SCOPE expects:

- `invoke_agent travel-planner` (session root)
- `moderator.agent`, `itinerary_agent.agent`, `budget_agent.agent`
- `moderator.chat`, `route_search.tool`
- `agent_start_event` and `agent_end_event` lifecycle spans
- `session.start`, `session.end`
- `travel-planner.graph` (the topology span)

`mas-lab telemetry inspect traces/otel_sdk_spans.jsonl` prints the same counts by `mas.boundary` (`AgentCall`, `LLMCall`, `ToolCall`, `TaskCall`, `Graph`, ...). The `raw` profile, which the golden test uses, produces the smaller MAS-native tree (`TaskCall`, `AgentCall`, `CallAnnotation`, ...).

Two behaviors matter for SCOPE:

- The application name comes from the trace (`app_name: travel-planner`). Pass `--app-name` to override.
- The converter emits a `<app>.graph` span that carries the agent topology on `gen_ai.ioa.graph` (nodes: `orchestrator, moderator, itinerary_agent, budget_agent`). Without it SCOPE cannot render the graph. See [docs/conversion.md](docs/conversion.md).

The pipeline behind the command is [`pipelines/native-to-otel-json.yaml`](pipelines/native-to-otel-json.yaml):

```yaml
run:
  post:
    - ref: telemetry:native-to-otel-json
```

It runs `events_to_otel` (`steps.run_convert`), then `verify_otel` (`steps.run_verify_spans`).

## 2. Verify

```bash
mas-lab telemetry verify traces/otel_sdk_spans.jsonl --level L3
```

The output gives the conformance per SpanSpec level. `--fail` exits non-zero on error.

```json
{
  "L1": "passing",
  "L2": "passing",
  "L3": "passing",
  "L4": "full"
}
```

Verification runs structural checks, the JSON-schema envelope and the SpanSpec L1-L4 levels. See [docs/verification.md](docs/verification.md) and [docs/spanspec.md](docs/spanspec.md). Two rules are mandatory: every span carries `application_id`, and every trace contains a `<app>.graph` span (without it, `verify` fails at L2). The contract lives in [`schemas/`](schemas/README.md).

## 3. Push to a collector

An infra manifest declares where spans go. This library owns the `OtelCollector` target ([`infra/local-otel.yaml`](infra/local-otel.yaml)):

```yaml
kind: Infra
spec:
  targets:
    - kind: OtelCollector
      endpoint: http://localhost:4318
```

Start a local collector and ClickHouse (see [`docker/README.md`](docker/README.md)), then push:

```bash
docker compose -f docker/compose.yaml up -d
mas-lab telemetry push traces/otel_sdk_spans.jsonl --infra infra/local-otel.yaml
```

A dry run needs no collector. It builds the OTLP batches and stops:

```bash
mas-lab telemetry push traces/otel_sdk_spans.jsonl --infra infra/local-otel.yaml --dry-run
# {"spans": 15, "batches": 1, "status": "dry-run", "detail": "15 spans -> http://localhost:4318/v1/traces", ...}
```

The endpoint precedence is `--endpoint`, then the manifest, then `$OTEL_EXPORTER_OTLP_ENDPOINT`. The push uses only the standard library; see [docs/otlp-collector.md](docs/otlp-collector.md).

The pipeline is [`pipelines/export-otel-collector.yaml`](pipelines/export-otel-collector.yaml) (the `export_otel` step, `steps.run_push_otlp`). Chain it after convert:

```yaml
run:
  post:
    - ref: telemetry:native-to-otel-json
    - ref: telemetry:export-otel-collector
```

## 4. Read back

Collectors persist to ClickHouse. Fetching a session needs the `clickhouse` extra and a reachable ClickHouse:

```bash
mas-lab telemetry list-apps
mas-lab telemetry dump trip-042 -o traces/roundtrip.otel.jsonl
```

Then confirm the round trip preserved semantics ([docs/compare-spans.md](docs/compare-spans.md)):

```bash
mas-lab telemetry compare traces/otel_sdk_spans.jsonl traces/roundtrip.otel.jsonl
# {"passed": 6, "failed": 0, "total_checks": 6}   (exit code 1 if any check fails)
```

## As one benchmark pipeline

```bash
mas-lab benchmark run experiment.yaml \
  --pipeline run:telemetry:native-to-kg-via-otel
```

[`native-to-kg-via-otel.yaml`](pipelines/native-to-kg-via-otel.yaml) replays the events to OTel, verifies them, folds them back into a knowledge graph (via `library-kg`) and compares it to the natively built graph. See [pipelines/README.md](pipelines/README.md).

## Next

| Topic | Doc |
|-------|-----|
| Conversion and extending the mapping | [docs/conversion.md](docs/conversion.md) |
| SpanSpec L1-L4 conformance | [docs/spanspec.md](docs/spanspec.md) |
| Structural, schema and SpanSpec verification | [docs/verification.md](docs/verification.md) |
| OTLP collector and ClickHouse read-back | [docs/otlp-collector.md](docs/otlp-collector.md) |
| Span parity comparison | [docs/compare-spans.md](docs/compare-spans.md) |
| Pipeline catalog | [pipelines/README.md](pipelines/README.md) |
| API reference | [README.md](README.md) |

# Serializing spans to an OTLP collector

This page shows how to send an OpenTelemetry (OTel) span set to an OTLP (OpenTelemetry Protocol) collector and read it back from ClickHouse. Use it to feed spans to a collector or to OXP.

```bash
mas-lab telemetry push otel_sdk_spans.jsonl --endpoint http://localhost:4318
```

It parallels the Neo4j serialization in `library-kg`:

| library-kg (KG → Neo4j) | library-telemetry (spans → collector) |
|-------------------------|---------------------------------------|
| `push_kg_to_neo4j` | `push_spans_to_collector` / `push_file` |
| `fetch_kg_from_neo4j` | `dump_spans` (ClickHouse) |
| `KGArtifact.push_to_neo4j` | `OtelSpanSet.push_to_collector` |

## Push

The push uses only the standard library (`urllib`); sending pre-built spans does not need the OTel SDK. It targets any OTLP HTTP/JSON endpoint (`POST /v1/traces`, `Content-Type: application/json`), for example an OpenTelemetry Collector on `http://localhost:4318`.

```python
from mas.library.telemetry.collector import push_file

# Auto-detects native events.jsonl vs OTel SDK spans.jsonl.
result = push_file(
    "otel_sdk_spans.jsonl",
    endpoint="http://localhost:4318",
    service_name="my-app",
    app_name="my-app",       # → application_id on every span (OXP grouping)
)
print(result)   # {"spans": N, "batches": M, "status": "ok", "detail": "..."}
```

Native `events.jsonl` input is converted to OTel SDK spans first (via the library
converter — requires the `convert` extra), then pushed.

From an artifact:

```python
from mas.library.telemetry import OtelSpanSet
OtelSpanSet.from_file("otel_sdk_spans.jsonl").push_to_collector(endpoint="http://localhost:4318")
```

Dry run (build batches, print the plan, send nothing):

```bash
mas-lab telemetry push otel_sdk_spans.jsonl --endpoint http://localhost:4318 --dry-run
```

Without `--endpoint`, the collector comes from `--infra` (an infra manifest with
an `OtelCollector` target, for example `infra/local-otel.yaml`) or
`$OTEL_EXPORTER_OTLP_ENDPOINT`. `--dry-run` falls back to
`http://localhost:4318` when none is set.

Write OTLP/SDK JSONL without sending:

```python
from mas.library.telemetry.collector import convert_file_to_otlp_jsonl
convert_file_to_otlp_jsonl("events.jsonl", "otlp.jsonl", app_name="my-app")
```

## Read back (ClickHouse)

Collectors commonly persist to ClickHouse (`otel_traces`). Fetch a session's
spans back out — requires the `clickhouse` extra
(`uv pip install -e "mas-library-telemetry[clickhouse]"`) and a reachable
ClickHouse (port-forward the service first):

```python
from mas.library.telemetry.collector import dump_spans, list_apps

list_apps()                                  # [{ServiceName, spans, sessions}, ...]
dump_spans("<mas.session.id>", output_path="/tmp/session.otel.jsonl")
dump_spans("<TRACE_HEX_ID>", query_by="trace", output_path="/tmp/trace.otel.jsonl")
```

```bash
mas-lab telemetry list-apps
mas-lab telemetry dump <SESSION_ID> -o /tmp/session.otel.jsonl
mas-lab telemetry dump <TRACE_HEX_ID> --by trace -o /tmp/trace.otel.jsonl
```

Connection defaults come from `$CLICKHOUSE_HOST` (default `localhost`),
`$CLICKHOUSE_PORT` (`8123`), `$CLICKHOUSE_USER` (`admin`) and
`$CLICKHOUSE_DATABASE` (`default`); the password is read from
`$CLICKHOUSE_PASSWORD`. Local stack: [`../docker/README.md`](../docker/README.md).

## OtlpSpan

`collector.otlp.OtlpSpan` is the minimal OTLP HTTP/JSON span model
(`traceId`, `spanId`, `startTimeUnixNano`, `attributes`, `status`, …).
`_sdk_spans_to_otlp` maps Python OTel SDK `span.to_json()` records to it, and
`_build_otlp_payload` wraps a batch as a `ResourceSpans` payload.

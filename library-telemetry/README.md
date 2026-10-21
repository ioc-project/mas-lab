# mas-library-telemetry

Converts native MAS execution events (`events.jsonl`) into OpenTelemetry (OTel) spans, verifies the spans against the MAS SpanSpec, and sends them to an OTLP collector.

Use it when you want to inspect a MAS run in an OTel backend, check that the spans conform to the spec, or feed them to the Observe and eXplain Platform (OXP, [observe-and-explain-platform](https://github.com/outshift-open/observe-and-explain-platform)), whose `norm` component normalizes incoming spans.

## Quick start

```python
from mas.library.telemetry import OtelSpanSet

spans = OtelSpanSet.from_events("run/traces/events.jsonl", app_name="my-app")  # needs [convert]
spans.save("run/traces/otel_sdk_spans.jsonl")

report = spans.validate(strictness="required")     # SpanSpec L1-L4
print(report.conformance("L3"))

spans.push_to_collector(endpoint="http://localhost:4318")
```

Or from the command line:

```bash
mas-lab telemetry convert events.jsonl -o otel_sdk_spans.jsonl --app-name my-app
mas-lab telemetry verify otel_sdk_spans.jsonl --level L3
```

## How it works

```
events.jsonl
    | Conversion (conversion/)   dispatch each event kind to a registered handler,
    |                            rebuild the span tree from call_id / parent_call_id,
    |                            keep timestamps, gate by export layer
    v
otel_sdk_spans.jsonl
    | Verification (verification/)   structural checks + JSON-schema envelope + SpanSpec L1-L4
    v
verified spans
    | Serialization (collector/)     push_file() -> OTLP HTTP collector
    v                                (dump_spans() reads back from ClickHouse)
OTLP collector / ClickHouse
```

`OtelSpanSet` is the data container passed through every stage.

This library is the inverse of `library-kg`: `library-kg` turns OTel spans into a knowledge graph (KG), this library turns native events into OTel spans.

```
              library-telemetry                     library-kg
events.jsonl ------------------> OTel spans ------------------> kg.json
             (convert + verify)             (normalize + extract)
```

Key behaviors:

- The default converter profile is `observe_sdk` everywhere (live plugin, pipeline steps, `replay_events_file`, `OtelSpanSet.from_events`, CLI). Spans then carry the attributes OXP `norm` requires: `agent_id`, `gen_ai.request.model`, `ioa_observe.entity.name`, prefixed `session.id`, `ioa_observe.agent.span_id`. Pass `converter_profile="raw"` for the unprefixed profile.
- `otel.realtime` (plugin config) and `OBSERVE_REALTIME_OBSERVABILITY_ENABLED` default to off. When on, the plugin emits incremental `topology.node.*`, `tool.*` and `llm.*` signals and does not also emit the end-of-run `.graph` span.
- The `[convert]` extra depends on `ioa-observe-sdk>=1.0.49` for semantic constants. MAS Lab does not call `Observe.init()`, neither for the live plugin nor for replay.
- `session.end` is emitted on `mas_call_end`, with `finish()` or plugin close as the fallback.

The core (`conversion/`, `verification/`, `collector/`, `steps/`, `OtelSpanSet`) does not depend on `mas.lab.*`, `mas.ctl.*` or `mas.runtime.*`. Only the integration layers do: the live plugin (`plugins/otel_plugin.py`, imports `mas.runtime`) and the benchmark adapters (`bench.py`, `codecs.py`, `steps/list_clickhouse_sessions.py`, import `mas.lab`).

## Install

Every external dependency is an optional extra. The package imports with nothing installed; only the feature that needs a missing dependency raises an error.

```bash
uv pip install -e library-telemetry                       # base: compare + OTLP push (stdlib only)
uv pip install -e "library-telemetry[convert]"            # + events -> OTel conversion
uv pip install -e "library-telemetry[verify]"             # + SpanSpec and JSON-schema verification
uv pip install -e "library-telemetry[clickhouse]"         # + ClickHouse read-back
uv pip install -e "library-telemetry[convert,verify,clickhouse,dev]"   # everything
```

| Extra | Enables | Without it |
|-------|---------|-----------|
| `convert` | events.jsonl to OTel spans (`MasOtelConverter`, replay, `from_events`); installs the OpenTelemetry SDK and `ioa-observe-sdk` | conversion raises `OtelSdkUnavailableError` |
| `verify` | `SpanValidator` (L1-L4) and JSON-schema envelope (`pyyaml`, `jsonschema`) | validators raise or degrade to best-effort |
| `clickhouse` | `dump_spans` / `list_apps` read-back | raises `ClickHouseUnavailableError` |

The inverse direction (OTel to KG) needs `uv pip install -e library-kg`. The `mas-lab telemetry` commands and the pipeline steps also need the `[bench]` extra (`mas-lab-bench`, `click`); the repository workspace install provides it.

## Recipes

### Batch functions

```python
from mas.library.telemetry.pipeline import (
    convert_events_to_spans_file,   # events.jsonl -> otel_sdk_spans.jsonl
    verify_spans_file,              # structural + schema + SpanSpec
    push_spans_file,                # -> OTLP collector
)

convert_events_to_spans_file("events.jsonl", "spans.jsonl", app_name="my-app")
report = verify_spans_file("spans.jsonl", spanspec_level="L3")
push_spans_file("spans.jsonl", "http://localhost:4318", app_name="my-app")
```

### Convert only (offline replay)

```python
from mas.library.telemetry.conversion import replay_events_file

n = replay_events_file("events.jsonl", "otel_sdk_spans.jsonl", app_name="my-app")
```

### Verify a spans file

```python
from mas.library.telemetry import verify_otel_file, SpanValidator

report = verify_otel_file("otel_sdk_spans.jsonl", spanspec_level="L3")
print(report["ok"], report["spanspec"]["conformance_at_level"])

# SpanSpec only
SpanValidator().validate_file("otel_sdk_spans.jsonl", strictness="recommended")
```

### Compare two span sets (parity)

```python
from mas.library.telemetry import OtelSpanSet

ref = OtelSpanSet.from_file("live_plugin_spans.jsonl")
cand = OtelSpanSet.from_events("events.jsonl", app_name="x")
print(cand.compare_to(ref, strict=True)["passed"])   # True: faithful replay
```

### Push to and read back from a collector

```python
from mas.library.telemetry.collector import push_file, dump_spans, list_apps

push_file("otel_sdk_spans.jsonl", "http://localhost:4318", app_name="my-app")
dump_spans("<session-id>", output_path="/tmp/session.otel.jsonl")   # needs [clickhouse]
```

## CLI (`mas-lab telemetry`)

The CLI is a `mas-lab` component; there is no separate binary. `mas-lab otel` is an alias. `mas-lab telemetry show TRACE_FILE` (print a native trace) comes from core `mas-lab`.

```bash
# events.jsonl -> otel_sdk_spans.jsonl
mas-lab telemetry convert events.jsonl -o otel_sdk_spans.jsonl --app-name my-app \
  [--layer structure --layer execution ...] [--governance] [--realtime] [--shift-to-now] [--new-session-id]

# Verify (structural + JSON schema + SpanSpec L1-L4)
mas-lab telemetry verify otel_sdk_spans.jsonl --level L3 [--spanspec PATH] [--fail]

# Quick summary (no validation)
mas-lab telemetry inspect otel_sdk_spans.jsonl

# Did the runtime report an error? (--fail exits 1 on any ERROR span)
mas-lab telemetry errors otel_sdk_spans.jsonl [--fail]

# What was said and called, in order (LLM replies and tool calls)
mas-lab telemetry conversation otel_sdk_spans.jsonl [--no-tools] [--width N]

# Calls to named tools with their inputs (e.g. the tool that commits a decision)
mas-lab telemetry calls otel_sdk_spans.jsonl --tool run_action [--json]

# Structural parity of two span files
mas-lab telemetry compare reference.jsonl candidate.jsonl

# Push to an OTLP collector
mas-lab telemetry push otel_sdk_spans.jsonl [--endpoint http://localhost:4318 | --infra infra.yaml] [--dry-run] [--shift-to-now] [--new-session-id]

# Read back from ClickHouse
mas-lab telemetry dump <SESSION_ID> -o session.otel.jsonl
mas-lab telemetry list-apps
mas-lab telemetry apps list | sessions list | traces list   # ClickHouse listings

# Introspect the extensible mapping
mas-lab telemetry kinds
```

Command notes:

| Command | Notes |
|---------|-------|
| `convert` | `--service-name` is the fallback name when `--app-name` is unset and events carry none. `--layer` defaults to structure, execution, semantic, provenance. `--governance` adds the governance layer. `--rewrite-tool-delegation/--no-rewrite-tool-delegation` (default on) turns `delegate_to_*` tool calls into agent handoffs. Prints span count and output path as JSON. |
| `verify` | `--level` defaults to `L3`. `--fail` exits with code 1 when any check fails. |
| `inspect` | Prints span, trace and `mas.boundary` counts as JSON. |
| `compare` | `--strict/--no-strict` (default strict). |
| `push` | `--infra` reads the endpoint from an infra manifest. Without either flag the endpoint comes from `$OTEL_EXPORTER_OTLP_ENDPOINT`. `--dry-run` converts and summarizes without sending. |
| `dump` | `--by session` (default, the ClickHouse `session_id` column) or `--by trace` (hex trace id). Needs `[clickhouse]`. |
| `kinds` | Lists native event kinds that have a registered span handler. |
| `list-apps` | Lists `ServiceName` values in ClickHouse `otel_traces`. |

When installed, this component replaces the core `push` / `verify` / `dump` stubs and keeps the core `show`, `apps`, `sessions`, `traces` commands.

Matching pipelines: `telemetry:native-to-otel-json` (convert + verify), `telemetry:otel-span-parity` (compare), `telemetry:export-otel-collector` (push), `telemetry:dump-session` (dump).

## Pipeline steps

The step functions in `mas.library.telemetry.steps` are plain Python and do not depend on `mas.lab.benchmark.pipeline` (except `list_clickhouse_sessions`). The `PipelineStep` adapters live in `mas.library.telemetry.bench` (the `[bench]` extra) and are declared as `step` plugins in `library.yaml`: `events_to_otel`, `verify_otel`, `compare_otel_spans`, `export_otel`, `clickhouse_dump`, `list_clickhouse_sessions`. Pipeline YAML definitions are in [`pipelines/`](pipelines/README.md).

| Module | Step function | Description |
|--------|--------------|-------------|
| `steps.convert` | `run_convert(events_path, *, output_dir, output_filename, service_name, app_name, export_layers, converter_profile, realtime, ...)` | events.jsonl to `OtelSpanSet` |
| `steps.verify_spans` | `run_verify_spans(spans_path, *, spanspec_level, spanspec_strictness, fail_on_error)` | structural + schema + SpanSpec |
| `steps.compare_spans` | `run_compare_spans(reference, candidate, *, strict, output_path)` | structural span parity |
| `steps.compare_spans` | `run_compare_spans_multi(reference, candidates, *, strict)` | one reference vs many candidates |
| `steps.push_otlp` | `run_push_otlp(spans_path, *, endpoint, infra, service_name, app_name, dry_run)` | push to OTLP collector (endpoint from `endpoint`, an infra manifest, or `$OTEL_EXPORTER_OTLP_ENDPOINT`) |
| `steps.push_otlp` | `run_dump_spans(session_id, *, output_path, query_by)` | ClickHouse to spans JSONL |

## API reference

### `mas.library.telemetry`

```python
from mas.library.telemetry import (
    OtelSpanSet, stream_span_sets,                 # artifact
    SpanValidator, ValidationReport, Violation,    # SpanSpec verification
    verify_otel_spans, verify_otel_file,           # structural + combined
    compare_otel_span_sets, compare_otel_span_files,  # parity
)
```

`OtelSpanSet` holds a list of OTel SDK spans plus metadata.

| Member | Description |
|--------|-------------|
| `OtelSpanSet.from_events(path, *, service_name, app_name, export_layers)` | convert events.jsonl to spans (needs `[convert]`) |
| `OtelSpanSet.from_file(path)` / `.from_spans(list)` | load spans from JSONL / memory |
| `.save(path)` / `.to_jsonl()` | write `otel_sdk_spans.jsonl` |
| `.push_to_collector(*, endpoint, ...)` | serialize to an OTLP collector |
| `OtelSpanSet.fetch_from_clickhouse(session_id, ...)` | read back from ClickHouse (`[clickhouse]`) |
| `.validate(*, strictness)` / `.verify_structural()` | verification |
| `.compare_to(reference, *, strict)` | span parity vs a reference |
| `.span_count` / `.trace_ids()` / `.span_names()` | accessors |

### `mas.library.telemetry.conversion`

| Symbol | Description |
|--------|-------------|
| `MasOtelConverter` | stateful live/replay converter (needs the SDK) |
| `replay_events_file(input, output, *, service_name, app_name, export_layers)` | batch replay |
| `JSONLineFileSpanExporter` | OTel exporter writing one JSON line per span |
| `ExportLayers` / `parse_export_layers` | layer toggles |
| `register(*kinds)` | decorator to add a handler for new event kinds |
| `build_handler_table()` / `registered_kinds()` | the assembled dispatch table |

Details: [docs/conversion.md](docs/conversion.md).

### `mas.library.telemetry.verification`

| Symbol | Description |
|--------|-------------|
| `SpanValidator` | validate spans against `.spanspec.yaml` (L1-L4) |
| `ValidationReport` / `Violation` | report types |
| `validate_spans_json_schema` | JSON Schema envelope validation |
| `verify_otel_spans` | structural checks (fields, root, boundary, single trace) |
| `verify_otel_file` | structural + schema + SpanSpec, from a file |
| `compare_otel_span_sets` / `_files` / `_files_multi` | structural parity (6 checks) |

Details: [docs/verification.md](docs/verification.md), [docs/spanspec.md](docs/spanspec.md).

### `mas.library.telemetry.collector`

| Symbol | Description |
|--------|-------------|
| `push_file(path, endpoint, *, service_name, app_name, dry_run, batch_size)` | file to OTLP collector (auto-detects events vs spans) |
| `push_spans_to_collector(sdk_spans, endpoint, ...)` | in-memory SDK spans to collector |
| `convert_file_to_otlp_jsonl(path, output, ...)` | write OTLP/SDK JSONL without pushing |
| `OtlpSpan` | minimal OTLP HTTP/JSON span model |
| `dump_spans(session_id, *, query_by, output_path, ...)` | ClickHouse to JSONL (`[clickhouse]`) |
| `list_apps(...)` | list ServiceNames in `otel_traces` |

Details: [docs/otlp-collector.md](docs/otlp-collector.md).

### `mas.library.telemetry.exceptions`

```
TelemetryError                    (base)
+-- ConversionError
|   +-- OtelSdkUnavailableError    the SDK is required but not installed
|   +-- UnknownEventKindError      no handler registered for a kind
|   +-- MissingRequiredFieldError  required event field absent
+-- VerificationError
|   +-- SpecNotFoundError          .spanspec.yaml not found
+-- SerializationError
    +-- CollectorPushError         OTLP push failed
    +-- ClickHouseUnavailableError clickhouse-connect not installed
```

## Schemas

Two layers, never merged (see [`schemas/README.md`](schemas/README.md)):

| Layer | Artifact | Validates |
|-------|----------|-----------|
| Wire envelope | `otel_sdk_spans.schema.json` | JSON shape (`name`, `context`, timestamps, `attributes`) |
| MAS semantics | `mas.spanspec.yaml` | per `span.name`: required and recommended `mas.*` attributes (L1-L4) |

Span node types (`span.name`): `TaskCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `MemoryCall`, `RAGQuery`, `SkillExecution`, `NetworkCall`, `WorkflowTransition`, `AgentCommunication`, `Worker`, `GovernanceEvent`, `ContextContribution`, `CallAnnotation`.

## Module layout

```
mas/library/telemetry/
+-- __init__.py            top-level public API + package_root()
+-- exceptions.py          typed exception hierarchy
+-- artifact.py            OtelSpanSet, the primary data container
+-- pipeline.py            public batch API
+-- lab_cli.py             mas-lab telemetry CLI component
+-- bench.py               PipelineStep adapters (needs [bench])
+-- codecs.py              span codecs for the benchmark framework
+-- infra.py               OtelCollector / ClickHouse infra-manifest targets
+-- plugins/otel_plugin.py live `otel` observability plugin
+-- conversion/            events.jsonl -> OTel spans (needs the SDK)
|   +-- converter.py       MasOtelConverter: stateful span-tree core + primitive API
|   +-- exporter.py        JSONLineFileSpanExporter
|   +-- replay.py          replay_events_file: offline batch entry point
|   +-- layers.py          ExportLayers: which layers to emit
|   +-- envelope.py        kind -> (block, summand, mealy) categorization table
|   +-- tool_name.py       resolve_tool_name
|   +-- mappings/          handlers split by category (extensible registry)
|       +-- base.py        register() decorator + SpanEmitter protocol
|       +-- structural.py  mas_call / execution / infrastructure
|       +-- execution.py   llm / tool / processing / skill / network / workflow / comm
|       +-- memory.py      memory_store / memory_read / memory_retrieve / rag_query
|       +-- context.py     context_assembled / context_part / state_update / compaction
|       +-- governance.py  governance / policy / hitl / budget / control
|       +-- trajectory.py  routing / user I/O / parallel_group / obs_wrap_gov
+-- verification/          spans -> conformance report
|   +-- spanspec.py        SpanValidator, ValidationReport, Violation, load_spans
|   +-- schema.py          JSON Schema envelope validation
|   +-- structural.py      verify_otel_spans, verify_otel_file (combined)
|   +-- compare.py         span parity compare (6 structural checks)
+-- collector/             spans -> OTLP collector
|   +-- otlp.py            push_file, push_spans_to_collector, OtlpSpan, convert_*
|   +-- clickhouse.py      dump_spans, list_apps (read-back)
+-- steps/                 standalone step functions (no pipeline dependency)
|   +-- convert.py         run_convert
|   +-- verify_spans.py    run_verify_spans
|   +-- compare_spans.py   run_compare_spans, run_compare_spans_multi
|   +-- push_otlp.py       run_push_otlp, run_dump_spans
+-- schemas/               packaged contracts (shipped in the wheel)
    +-- otel_sdk_spans.schema.json    JSON Schema envelope
    +-- mas.spanspec.yaml             per-span L1-L4 SpanSpec
```

## Related packages

| Package | Role |
|---------|------|
| `library-telemetry` (`mas.library.telemetry`) | native events to OTel spans, span verification, OTLP serialization |
| `library-kg` (`mas.library.kg`) | OTel spans to events to KG, plus KG verification (the inverse direction) |
| `mas-lab` (`mas.lab`) | benchmarks and pipelines; `bench.py` adapts this library's functions to `PipelineStep`s |
| `mas-library-standard` (`mas.library.standard`) | ships the native observability plugin that writes the `events.jsonl` this library converts |

## Extending

### Add an event kind

1. Add a row to `conversion/envelope.py:KIND_ENVELOPE` (`kind -> (block, summand, mealy_symbol)`).
2. Add a handler in the matching `conversion/mappings/<block>.py`, decorated with `@register(<kind>)`.
3. Add a test fixture with a real or synthetic event.

`converter.py` does not change. See [docs/conversion.md](docs/conversion.md).

### Add a SpanSpec attribute or span type

1. Add or extend the shape in `schemas/mas.spanspec.yaml` (and the packaged copy).
2. Add the JSON envelope hint in `schemas/otel_sdk_spans.schema.json` if needed.
3. Re-run `verify_otel_file`.

## Development

```bash
uv pip install -e "library-telemetry[convert,dev]"
cd library-telemetry && pytest tests/

# Smoke test: events -> spans -> verify -> parity
python -c "
from mas.library.telemetry import OtelSpanSet
ss = OtelSpanSet.from_events('tests/fixtures/events.jsonl', app_name='smoke')
print(ss.span_count, 'spans;', ss.validate().conformance('L3'), 'at L3')
print('self-parity:', ss.compare_to(ss)['passed'])
"
```

Design rules:

- The conversion, verification and collector modules must not import `mas.lab.*`, `mas.ctl.*` or `mas.runtime.*`. Only the plugin and the benchmark adapters may.
- Conversion is the only path that needs the OpenTelemetry SDK. Importing `mas.library.telemetry.conversion` does not require it; `MasOtelConverter` is imported lazily and raises `OtelSdkUnavailableError` on construction.
- Adding an event kind touches only a mapping module and the envelope table, never the converter (same design as `library-kg`).
- Replay and live mode run the same handlers. Deterministic session ids make replays reproducible, so parity checks are meaningful.

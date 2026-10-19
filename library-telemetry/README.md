# mas-library-telemetry

Native-events **→ OTel conversion**, span **verification**, and OTLP **collector
serialization** for MAS multi-agent systems.

Takes a native `events.jsonl` trace of agent execution → OTel SDK spans
(`otel_sdk_spans.jsonl`) → verified against the MAS SpanSpec → serialized to an
OTLP collector.

Default converter profile is ``observe_sdk`` on the live plugin and pipeline
steps so spans carry OXP ``norm``'s required attributes (`agent_id`,
`gen_ai.request.model`, `ioa_observe.entity.name`, prefixed `session.id`,
`ioa_observe.agent.span_id`). Offline unit replay still defaults to ``raw``
unless you pass ``converter_profile="observe_sdk"``.

`otel.realtime` (plugin config) and `OBSERVE_REALTIME_OBSERVABILITY_ENABLED`
default **off**. When on, the plugin emits incremental `topology.node.*` /
`tool.*` / `llm.*` signals and does **not** also emit the end-of-run `.graph`
span.

The `[convert]` extra depends on `ioa-observe-sdk>=1.0.49` for semantic
constants. MAS Lab does **not** call `Observe.init()` for the live plugin
or for replay — conversion stays native-events → OTel. `session.end` is
emitted on `mas_call_end` (known end of the run), with `finish()` /
plugin close as the fallback.

**The inverse of `library-kg`.** Where `library-kg` turns *OTel spans → KG*, this
library turns *native events → OTel spans*. Together they round-trip:

```
              library-telemetry                     library-kg
events.jsonl ──────────────────▶ OTel spans ──────────────────▶ kg.json
             (convert + verify)             (normalize + extract)
```

**Self-contained.** No dependencies on `mas.lab.*`, `mas.ctl.*`, or
`mas.runtime.*`. The OpenTelemetry SDK is only needed for the *conversion* path
(optional `[convert]` extra); verification, span-parity compare, and OTLP push
work with just `pyyaml` + `jsonschema` + the standard library.

---

## Conceptual architecture

The canonical input is **`events.jsonl`** — a structured trace of native MAS
execution events (the same file `library-kg` normalises *from*).

```
events.jsonl
    │  Conversion  (conversion/)
    │  replay_events_file() / MasOtelConverter.process_event()
    │  • dispatch each event kind to a registered handler
    │  • reconstruct the span tree from call_id / parent_call_id
    │  • preserve timestamps, gate by export layer
    ▼
otel_sdk_spans.jsonl
    │  Verification  (verification/)
    │  verify_otel_file() = structural + JSON-schema envelope + SpanSpec L1–L4
    ▼
verified spans
    │  Serialization  (collector/)
    │  push_file() → OTLP HTTP collector   (dump_spans() reads back from ClickHouse)
    ▼
OTLP collector / ClickHouse
```

The **`OtelSpanSet`** artifact is the data container threaded through all of this
(the counterpart of `library-kg`'s `KGArtifact`).

---

## Install

Every external dependency is an **optional extra** — the package imports with
nothing installed, and only the *feature* powered by a missing dependency is
unavailable (with a clear error), never the whole library.

```bash
# Base — span-parity compare + OTLP push (stdlib only), and the artifact/CLI shells
uv pip install -e library-telemetry

# + native-events → OTel conversion (OpenTelemetry SDK)
uv pip install -e "library-telemetry[convert]"

# + SpanSpec (L1–L4) and JSON-schema verification (pyyaml + jsonschema)
uv pip install -e "library-telemetry[verify]"

# + ClickHouse read-back (dump_spans / list-apps)
uv pip install -e "library-telemetry[clickhouse]"

# Everything, for development / CI
uv pip install -e "library-telemetry[convert,verify,clickhouse,dev]"
```

| Extra | Enables | Without it |
|-------|---------|-----------|
| `convert` | events.jsonl → OTel spans (`MasOtelConverter`, replay, `from_events`) | conversion raises `OtelSdkUnavailableError` |
| `verify` | `SpanValidator` (L1–L4) + JSON-schema envelope | validators raise / degrade to best-effort |
| `clickhouse` | `dump_spans` / `list_apps` read-back | raises `ClickHouseUnavailableError` |

Peer library for the inverse direction (OTel → KG):

```bash
uv pip install -e library-kg
```

---

## Quick start

### End-to-end: events.jsonl → verified OTel spans → collector

```python
from mas.library.telemetry import OtelSpanSet

spans = OtelSpanSet.from_events("run/traces/events.jsonl", app_name="my-app")  # needs [convert]
spans.save("run/traces/otel_sdk_spans.jsonl")

report = spans.validate(strictness="required")     # SpanSpec L1–L4
print(report.conformance("L3"))

spans.push_to_collector(endpoint="http://localhost:4318")
```

### Batch functions (start here)

```python
from mas.library.telemetry.pipeline import (
    convert_events_to_spans_file,   # events.jsonl → otel_sdk_spans.jsonl
    verify_spans_file,              # structural + schema + SpanSpec
    push_spans_file,                # → OTLP collector
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
from mas.library.telemetry import compare_otel_span_sets, OtelSpanSet

ref = OtelSpanSet.from_file("live_plugin_spans.jsonl")
cand = OtelSpanSet.from_events("events.jsonl", app_name="x")
print(cand.compare_to(ref, strict=True)["passed"])   # True → faithful replay
```

### Push / read back from a collector

```python
from mas.library.telemetry.collector import push_file, dump_spans, list_apps

push_file("otel_sdk_spans.jsonl", "http://localhost:4318", app_name="my-app")
dump_spans("<session-id>", output_path="/tmp/session.otel.jsonl")   # needs [clickhouse]
```

---

## CLI (`mas-lab telemetry`)

Provided as a thin `mas-lab` CLI component (there is no separate standalone
binary — the library exposes steps, pipelines, a Python API, and this component).

```bash
# events.jsonl → otel_sdk_spans.jsonl
mas-lab telemetry convert events.jsonl -o otel_sdk_spans.jsonl --app-name my-app [--shift-to-now] [--new-session-id]

# Verify (structural + JSON schema + SpanSpec L1–L4)
mas-lab telemetry verify otel_sdk_spans.jsonl --level L3 [--strict] [--fail]

# Quick summary (no validation)
mas-lab telemetry inspect otel_sdk_spans.jsonl

# Structural parity of two span files
mas-lab telemetry compare reference.jsonl candidate.jsonl

# Push to an OTLP collector
mas-lab telemetry push otel_sdk_spans.jsonl --endpoint http://localhost:4318 [--dry-run] [--shift-to-now] [--new-session-id]

# Read back from ClickHouse
mas-lab telemetry dump <SESSION_ID> -o session.otel.jsonl
mas-lab telemetry list-apps

# Introspect the extensible mapping
mas-lab telemetry kinds
```

The `mas-lab telemetry` lab commands are thin shortcuts to these same functions
and to the [pipeline steps](pipelines/README.md).

---

## Pipeline steps

Standalone step functions in `mas.library.telemetry.steps` — pure Python, no
dependency on `mas.lab.benchmark.pipeline`. The `PipelineStep` adapters that make
them pipeline-discoverable live in `mas.library.telemetry.bench` (the `[bench]`
extra) and register via the `mas.lab.pipeline_steps` entry-point group.

| Module | Step function | Description |
|--------|--------------|-------------|
| `steps.convert` | `run_convert(events_path, *, output_dir, service_name, app_name, export_layers)` | events.jsonl → `OtelSpanSet` |
| `steps.verify_spans` | `run_verify_spans(spans_path, *, spanspec_level, spanspec_strictness, fail_on_error)` | structural + schema + SpanSpec |
| `steps.compare_spans` | `run_compare_spans(reference, candidate, *, strict, output_path)` | structural span parity |
| `steps.compare_spans` | `run_compare_spans_multi(reference, candidates, *, strict)` | one reference vs many candidates |
| `steps.push_otlp` | `run_push_otlp(spans_path, *, endpoint, service_name, app_name, dry_run)` | push to OTLP collector |
| `steps.push_otlp` | `run_dump_spans(session_id, *, output_path, query_by)` | ClickHouse → spans JSONL |

Pipeline YAML definitions: [`pipelines/`](pipelines/README.md).

---

## API reference

### Top-level (`mas.library.telemetry`)

```python
from mas.library.telemetry import (
    OtelSpanSet, stream_span_sets,                 # artifact
    SpanValidator, ValidationReport, Violation,    # SpanSpec verification
    verify_otel_spans, verify_otel_file,           # structural + combined
    compare_otel_span_sets, compare_otel_span_files,  # parity
)
```

#### `OtelSpanSet`

Primary data container: a list of OTel SDK spans + metadata.

| Member | Description |
|--------|-------------|
| `OtelSpanSet.from_events(path, *, service_name, app_name, export_layers)` | convert events.jsonl → spans (needs `[convert]`) |
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

See [docs/conversion.md](docs/conversion.md) — the extensible, split-by-category
handler design.

### `mas.library.telemetry.verification`

| Symbol | Description |
|--------|-------------|
| `SpanValidator` | validate spans against `.spanspec.yaml` (L1–L4) |
| `ValidationReport` / `Violation` | report types |
| `validate_spans_json_schema` | JSON Schema envelope validation |
| `verify_otel_spans` | structural checks (fields, root, boundary, single trace) |
| `verify_otel_file` | structural + schema + SpanSpec, from a file |
| `compare_otel_span_sets` / `_files` / `_files_multi` | structural parity (6 checks) |

See [docs/verification.md](docs/verification.md), [docs/spanspec.md](docs/spanspec.md).

### `mas.library.telemetry.collector`

| Symbol | Description |
|--------|-------------|
| `push_file(path, endpoint, *, service_name, app_name, dry_run, batch_size)` | file → OTLP collector (auto-detects events vs spans) |
| `push_spans_to_collector(sdk_spans, endpoint, ...)` | in-memory SDK spans → collector |
| `convert_file_to_otlp_jsonl(path, output, ...)` | write OTLP/SDK JSONL without pushing |
| `OtlpSpan` | minimal OTLP HTTP/JSON span model |
| `dump_spans(session_id, *, query_by, output_path, ...)` | ClickHouse → JSONL (`[clickhouse]`) |
| `list_apps(...)` | list ServiceNames in `otel_traces` |

See [docs/otlp-collector.md](docs/otlp-collector.md).

### `mas.library.telemetry.exceptions`

```
TelemetryError                    (base)
├── ConversionError
│   ├── OtelSdkUnavailableError    the SDK is required but not installed
│   ├── UnknownEventKindError      no handler registered for a kind
│   └── MissingRequiredFieldError  required event field absent
├── VerificationError
│   └── SpecNotFoundError          .spanspec.yaml not found
└── SerializationError
    ├── CollectorPushError         OTLP push failed
    └── ClickHouseUnavailableError clickhouse-connect not installed
```

---

## Module layout

```
mas/library/telemetry/
├── __init__.py            top-level public API + package_root()
├── exceptions.py          typed exception hierarchy
├── artifact.py            OtelSpanSet — primary data container
├── pipeline.py            public batch API — start here
├── cli.py                 mas-lab telemetry CLI
│
├── conversion/            events.jsonl → OTel spans  (needs the SDK)
│   ├── converter.py       MasOtelConverter — stateful span-tree core + primitive API
│   ├── exporter.py        JSONLineFileSpanExporter
│   ├── replay.py          replay_events_file — offline batch entry point
│   ├── layers.py          ExportLayers — which layers to emit
│   ├── envelope.py        kind → (block, summand, mealy) categorization table
│   ├── tool_name.py       resolve_tool_name
│   └── mappings/          split-by-category handlers (extensible registry)
│       ├── base.py        register() decorator + SpanEmitter protocol
│       ├── structural.py  mas_call / execution / infrastructure
│       ├── execution.py   llm / tool / processing / skill / network / workflow / comm
│       ├── memory.py      memory_store / memory_read / memory_retrieve / rag_query
│       ├── context.py     context_assembled / context_part / state_update / compaction
│       ├── governance.py  governance / policy / hitl / budget / control
│       └── trajectory.py  routing / user I/O / parallel_group / obs_wrap_gov
│
├── verification/          OTel spans → conformance report (analog of kg/verifier)
│   ├── spanspec.py        SpanValidator, ValidationReport, Violation, load_spans
│   ├── schema.py          JSON Schema envelope validation
│   ├── structural.py      verify_otel_spans, verify_otel_file (combined)
│   └── compare.py         span parity compare (6 structural checks)
│
├── collector/             OTel spans → OTLP collector (analog of neo4j/)
│   ├── otlp.py            push_file, push_spans_to_collector, OtlpSpan, convert_*
│   └── clickhouse.py      dump_spans, list_apps (read-back)
│
├── steps/                 standalone step functions (no pipeline dep)
│   ├── convert.py         run_convert
│   ├── verify_spans.py    run_verify_spans
│   ├── compare_spans.py   run_compare_spans, run_compare_spans_multi
│   └── push_otlp.py       run_push_otlp, run_dump_spans
│
└── schemas/               packaged contracts (shipped in the wheel)
    ├── otel_sdk_spans.schema.json    JSON Schema envelope
    └── mas.spanspec.yaml             per-span L1–L4 SpanSpec
```

---

## Schemas

Two layers, never merged — see [`schemas/README.md`](schemas/README.md):

| Layer | Artifact | Validates |
|-------|----------|-----------|
| Wire envelope | `otel_sdk_spans.schema.json` | JSON shape (`name`, `context`, timestamps, `attributes`) |
| MAS semantics | `mas.spanspec.yaml` | per `span.name`: required/recommended `mas.*` attrs (L1–L4) |

Span node types (`span.name`): `TaskCall`, `AgentCall`, `LLMCall`, `ToolCall`,
`ProcessingCall`, `MemoryCall`, `RAGQuery`, `SkillExecution`, `NetworkCall`,
`WorkflowTransition`, `AgentCommunication`, `Worker`, `GovernanceEvent`,
`ContextContribution`, `CallAnnotation`.

---

## Relationship to other packages

| Package | Role |
|---------|------|
| `library-telemetry` (`mas.library.telemetry`) | native events → OTel spans, span verification, OTLP serialization. **Standalone OSS module.** |
| `library-kg` (`mas.library.kg`) | OTel spans → events → KG + KG verification. The inverse direction. |
| `mas-lab` / `library-standard` (`mas.library.standard`) | Ships the OSS runtime; the native→OTel converter here is a refactored copy of the one there (round-trip parity tests keep them span-identical). |
| `mas-lab-graph` | Thin pipeline-step wrappers that call `library-telemetry` (and `library-kg`) stages from `mas-bench` pipelines. |

---

## Extending

### New event kind → span mapping

1. Add a row to `conversion/envelope.py:KIND_ENVELOPE`
   (`kind → (block, summand, mealy_symbol)`).
2. Add a handler in the matching `conversion/mappings/<block>.py` module,
   decorated with `@register(<kind>)`.
3. Add a test fixture with a real or synthetic event.

Nothing in `converter.py` changes. See [docs/conversion.md](docs/conversion.md).

### New SpanSpec attribute / span type

1. Add or extend the shape in `schemas/mas.spanspec.yaml` (and the packaged copy).
2. Add the JSON envelope hint in `schemas/otel_sdk_spans.schema.json` if needed.
3. Re-run `verify_otel_file`.

---

## Development

```bash
uv pip install -e "library-telemetry[convert,dev]"
cd library-telemetry && pytest tests/

# Smoke test: events → spans → verify → parity
python -c "
from mas.library.telemetry import OtelSpanSet
ss = OtelSpanSet.from_events('tests/fixtures/events.jsonl', app_name='smoke')
print(ss.span_count, 'spans;', ss.validate().conformance('L3'), 'at L3')
print('self-parity:', ss.compare_to(ss)['passed'])
"
```

### Design principles

**Zero internal deps.** `library-telemetry` must not import from `mas.lab.*`,
`mas.ctl.*`, or `mas.runtime.*`. This is what makes it a standalone OSS module.

**Conversion is the only SDK-gated path.** Verification, compare, and OTLP push
work without `opentelemetry-sdk`. Import `mas.library.telemetry.conversion` never
requires the SDK either — `MasOtelConverter` is imported lazily and raises a clear
`OtelSdkUnavailableError` only on construction.

**Thin core, mapping in categories.** Adding an event kind touches only a mapping
module + the envelope table — never the converter — exactly like `library-kg`.

**Replay ≡ live.** The same handlers run in both modes; deterministic session ids
make replays reproducible so parity checks are meaningful.

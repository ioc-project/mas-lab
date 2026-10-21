# OTel telemetry reference

This page describes how `mas.library.kg` turns OpenTelemetry (OTel) spans into a knowledge graph (KG), and the span format that path expects. A span is one timed operation in a trace, the tree of operations of one run. Read it if you feed spans exported from a MAS Lab run (or another source) into the KG pipeline.

The first-party code in this library only detects the span shape and reshapes SDK spans to ClickHouse-style keys. `norm`, the OTel normalizer of the open-source Observe and eXplain Platform (OXP, [github.com/outshift-open/observe-and-explain-platform](https://github.com/outshift-open/observe-and-explain-platform)), builds the nodes and edges.

Smallest example (needs the `[norm]` extra, see [Section 1](#1-overview)):

```bash
mas-lab kg otel-to-kg SPANS.jsonl --run-id RUN --output-dir OUT   # writes OUT/kg.jsonld
```

| Part | Owner |
|------|-------|
| Span shape detection, SDK-to-ClickHouse reshape, CLI and step wrappers (`mas.library.kg.observability.otel_via_norm`, `steps.normalize_otel`, `mas-lab kg ...`) | this library |
| Span dispatch, attribute reading, node and edge construction (`norm.normalize()`) | OXP `norm` (optional extra `[norm]`) |
| Ontology classes and generated models | `oxp-ontology` (PyPI, hard dependency) |

See also: [normalization.md](normalization.md), [native-telemetry.md](native-telemetry.md), [steps.md](steps.md), and [Native events to OTel conversion](../../library-telemetry/docs/conversion.md) (how MAS Lab produces these spans).

## Table of contents

1. [Overview](#1-overview)
2. [Accepted span shapes](#2-accepted-span-shapes)
3. [Span names dispatched by `norm`](#3-span-names-dispatched-by-norm)
4. [Attributes read by `norm`](#4-attributes-read-by-norm)
5. [Example spans](#5-example-spans)
6. [Usage guide](#6-usage-guide)
7. [Relationship to the native path](#7-relationship-to-the-native-path)
8. [Helper modules not on the OTel path](#8-helper-modules-not-on-the-otel-path)

## 1. Overview

```
OTel spans (JSON / JSONL)                       mas.library.kg (first-party)                OXP norm
  - ClickHouse rows (SpanName, SpanAttributes)   detect_span_shape()                        normalize(rows)
  - OTel SDK spans (name, context, attributes) ->to_clickhouse_spans()  -- ClickHouse rows -> (nodes, edges)
                                                  align_graph()  <---------------------------- nodes, edges
                                                  KGArtifact -> kg.jsonld
```

`normalize_otel(spans)` in `observability/otel_via_norm.py`:

1. Detects the shape of the input (`detect_span_shape`).
2. Reshapes SDK spans to ClickHouse rows (`to_clickhouse_spans`); ClickHouse rows pass through unchanged.
3. Calls `norm.normalizer.normalize(rows)`, which returns `(nodes, edges)`.
4. Passes the result through `core.ontology_align.align_graph`.

This library applies no heuristics, gap filling or event synthesis on the OTel path, and does not convert OTel back to `events.jsonl`.

**Requirements.** `norm` is an optional extra (`pip install "mas-library-kg[norm]"`, which currently installs `backend/norm` from the OXP repository on GitHub until it is published as a package). Without it, `run_normalize_otel` and `build_kg_from_otel_spans` log a warning and return an empty KG (zero nodes and edges, no error). Likewise, a span list whose shape is not recognised yields an empty KG with `otel_format: "unknown"`.

**Run id.** `norm` derives application, session and agent ids from the spans (see [Section 4](#4-attributes-read-by-norm)). The `--run-id` / `run_id` argument is only recorded in the artifact (`run_id` field), not used to rewrite node ids. Native-path options (`strict`, `synthesize_llm_gaps`, `allow_heuristics`, `write_events`, `split_session_id`, `app_name`, ...) are accepted by `run_normalize_otel` for call-site compatibility and ignored.

## 2. Accepted span shapes

`detect_span_shape(spans)` inspects the first dict in the list and returns `"clickhouse_row"` or `"sdk"`, or raises `OtelFormatError`. An empty list is treated as `"clickhouse_row"`.

| Shape | Detected when the span has | Typical source |
|-------|----------------------------|----------------|
| `clickhouse_row` | any of `SpanName`, `SpanAttributes`, `SpanId` | Rows exported from ClickHouse |
| `sdk` | no ClickHouse key, a `name` key, and `context` or `attributes` or `start_time` | `ReadableSpan.to_json()` output of the OpenTelemetry Python SDK |

Input files may be JSON Lines (one span per line) or a single JSON array (`observability.helpers.load_spans`); blank lines are skipped and malformed lines are logged and skipped.

### SDK span to ClickHouse row

`sdk_span_to_clickhouse` maps each SDK span as follows:

| ClickHouse key | Taken from (first non-empty) |
|----------------|------------------------------|
| `SpanName` | `name` |
| `SpanAttributes` | `attributes` (dict) |
| `SpanId` | `context.span_id`, `span_id`; lowercase hex, `0x` removed, zero-padded to 16 chars |
| `TraceId` | `context.trace_id`, `trace_id`; same treatment, 32 chars |
| `ParentSpanId` | `parent_id`, `context.parent_span_id`; same treatment, 16 chars; `""` for roots |
| `ServiceName` | `attributes.application_id` if set; else `resource.attributes["service.name"]`; else `attributes["service.name"]` |
| `Timestamp` | `start_time` rewritten to `YYYY-MM-DD HH:MM:SS.fffffffff` (UTC). ISO-8601 strings, epoch seconds and epoch nanoseconds are accepted |
| `Duration` | nanoseconds: `Duration` if present, else `end_time - start_time`, else `0` |

Status, span events, links and span kind are not carried over by the reshape. `norm` falls back to the `execution.success` attribute for success, and to a `StatusCode` column only when a ClickHouse row provides one.

## 3. Span names dispatched by `norm`

`norm` selects a handler from the span name (`SpanName`). Spans with any other name are ignored (no node, no error). This describes the OXP `backend/norm` package; the OXP repository is authoritative and may add more.

| Span name | Handler result |
|-----------|----------------|
| `session.start` | `Session` node (and a placeholder `MAS`) |
| `session.end` | Updates the `Session` end time and success |
| `<name>.graph` | Declared `MAS`, `Agent` and `Tool` nodes from the graph attribute |
| `<name>.agent` | `Agent` (when undeclared) and `AgentCall`, with initial and final `State` |
| `<name>.chat` | `LLM`, `LLMCall`, initial and final `State`, `Transition` |
| `<name>.tool` | `Tool`, `ToolCall`, initial and final `State`, `Transition` |

After all spans are processed, `norm` links agent hand-offs (from `ioa_observe.handoff.source.span_ids`, with a timestamp-order fallback), chains capability calls inside an agent call, and assigns boundary states to the session and MAS call.

Names seen in MAS Lab traces that `norm` does not map: `agent_start_event` / `agent_end_event` (lifecycle markers, `mas.boundary = AgentLifecycleEvent`) and `invoke_agent <app>` (a `TaskCall`-boundary span). The observe-sdk MAS extension suffixes (`*.memory`, `*.processing`, `*.context`, `*.governance`, `*.skill`, `*.rag`) are likewise not dispatched by stock `norm`; `observability/path_equivalence.py` materializes them only for native-versus-OTel comparison, not for `run_normalize_otel`.

## 4. Attributes read by `norm`

All attributes live in `SpanAttributes` (SDK: `attributes`). Values below are what the `norm` handlers read; fields not listed are ignored.

### 4.1 Identity and timing (all handled spans)

| Attribute / column | Meaning |
|--------------------|---------|
| `application_id` (else `ServiceName`) | Application (MAS) name |
| `session.id` | Session id. `norm` strips a leading `<application_id>_` prefix, so `trip-planner_<uuid>` gives session `<uuid>` |
| `agent_id` | Agent that owns the span |
| `ioa_start_time` | Start time in epoch seconds (preferred); otherwise `Timestamp` is used |
| `Duration` | Nanoseconds; end time is start plus duration |
| `execution.success` | String `"true"` / `"false"`; otherwise success unless `StatusCode` is `Error` |
| `SpanId`, `ParentSpanId` | Recorded on nodes and used to find the owning agent call |
| `ioa_observe.agent.span_id` | Span id of the agent span that owns a `.chat` or `.tool` span |

### 4.2 `session.start` / `session.end`

`session.ended_at` (epoch seconds) is read on `session.end`; `session.started_at` is carried on `session.start` spans in the shipped examples.

### 4.3 `<name>.agent`

| Attribute | Use |
|-----------|-----|
| `ioa_observe.entity.name` | Agent name (falls back to `agent_id`) |
| `ioa_observe.entity.description` | Agent description |
| `ioa_observe.entity.input`, `ioa_observe.entity.output` | Agent call input and output |
| `ioa_observe.handoff.source.span_ids` | Source agent spans of a hand-off |
| `ioa_observe.agent.sequence`, `ioa_observe.agent.previous` | Present in traces; sequencing hints |

### 4.4 `<name>.chat`

| Attribute | Use |
|-----------|-----|
| `gen_ai.provider.name`, `gen_ai.request.model` | Provider and model of the `LLM` / `LLMCall` |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.total_tokens` | Token counts |
| `gen_ai.usage.cache_read.input_tokens` | Cache-read token count |
| `gen_ai.request.temperature`, `gen_ai.response.finish_reasons`, `gen_ai.response.id` | Temperature, finish reason (first element of the array), response id |
| `gen_ai.input.messages`, `gen_ai.system_instructions` | Prompt (system instructions are prepended) |
| `gen_ai.output.messages` | Completion |

### 4.5 `<name>.tool`

| Attribute | Use |
|-----------|-----|
| `ioa_observe.entity.name` | Tool name |
| `ioa_observe.entity.description` | Tool description |
| `gen_ai.tool.call.arguments` (else `ioa_observe.entity.input`) | Tool input |
| `gen_ai.tool.call.result` (else `ioa_observe.entity.output`) | Tool output |

### 4.6 `<name>.graph`

`gen_ai.ioa.graph` (the declared topology), `ioa_observe.entity.name`, `ioa_observe.entity.description`. Other `gen_ai.ioa.graph*` attributes (protocol, determinism score, dynamism) appear in traces.

### 4.7 MAS attributes carried on spans

MAS Lab instrumentation also writes `mas.boundary` (for example `Session`, `AgentCall`, `LLMCall`, `ToolCall`, `TaskCall`, `Graph`, `AgentLifecycleEvent`), `mas.call.id`, `mas.agent.id`, `mas.run.id`, `mas.status`, `mas.llm.*`, `mas.tool.*`, `ioa_observe.span.kind` (`agent`, `llm`, `tool`, `task`, `graph`) and `ioa_observe.workflow.name`. Stock `norm` dispatches on the span name, not on these, so they are provenance that `norm` currently does not read. The constants for them are in `observability/vocabulary.py`.

## 5. Example spans

Shipped in `library-kg/examples/trip-planner/otel_spans.jsonl` (18 spans) and `library-kg/examples/qa-agent/otel_spans.jsonl` (8 spans). Both are OTel SDK-shaped.

An agent span (abridged):

```json
{
  "name": "moderator.agent",
  "context": {"trace_id": "0x...", "span_id": "0x5388125d8de9d1e6"},
  "kind": "SpanKind.INTERNAL",
  "parent_id": null,
  "start_time": "2026-08-03T22:11:21.6Z",
  "status": {"status_code": "UNSET"},
  "attributes": {
    "agent_id": "moderator",
    "session.id": "trip-planner_93947a02-ed82-46bd-91fe-c2d9979198ce",
    "ioa_observe.span.kind": "agent",
    "ioa_observe.entity.name": "moderator",
    "ioa_observe.entity.input": "...",
    "ioa_observe.entity.output": "...",
    "ioa_observe.agent.sequence": 1,
    "mas.boundary": "AgentCall",
    "mas.call.id": "...",
    "execution.success": "true"
  },
  "resource": {"attributes": {"service.name": "trip-planner"}}
}
```

The same file contains `session.start` / `session.end`, `<agent>.chat`, `<tool>.tool` (for example `lookup_schedule.tool`), `agent_start_event` / `agent_end_event`, `invoke_agent trip-planner` and `trip-planner.graph` spans. Span ids in these files are `0x`-prefixed hex strings; the reshape strips the prefix.

The ClickHouse row produced for a span by `mas-lab kg otel-to-events` has this shape (abridged):

```json
{"SpanName": "agent_start_event", "SpanAttributes": {"mas.boundary": "AgentLifecycleEvent", "agent_id": "moderator", "session.id": "trip-planner_93947a02-...", "ioa_start_time": "1785795081.6004171"}, "SpanId": "2a7f8f29074cf0cd", "TraceId": "db78df238fe16e96e4277124b29c4b14", "ParentSpanId": "0aaa3ad3a47cccc5", "ServiceName": "trip-planner", "Timestamp": "2026-08-03 22:11:21.600417000", "Duration": 0}
```

## 6. Usage guide

### CLI

```bash
# Spans -> KG (needs the [norm] extra; prints the artifact JSON, writes OUT/kg.jsonld)
mas-lab kg otel-to-kg SPANS.jsonl --run-id RUN --output-dir OUT

# Same through the normalize command
mas-lab kg normalize --otel SPANS.jsonl --run-id RUN -o kg.jsonld

# Reshape only (no norm needed): writes OUT/otel_clickhouse.jsonl
mas-lab kg otel-to-events SPANS.jsonl --run-id RUN --output-dir OUT
```

| Command | Notes |
|---------|-------|
| `kg otel-to-kg` | Options: `--run-id` (required), `--output-dir`, `--ontology-path`, `--dry-run`, plus `--no-strict`, `--no-synthesize`, `--no-fill-synthesized-processing-defaults`, `--write-events`, which are accepted and ignored on this path. |
| `kg normalize --otel FILE` | Takes the same normalization options as the native path (`--include-*`, `--app-name`, ...), which are ignored for OTel input. Without `--run-id` the run id is taken from `--session-id`, else the first span's `mas.session.id`, `session.id` or `mas.run.id` attribute, else its trace id, else the file name. |
| `kg otel-to-events` | Despite the name, it does not write `events.jsonl`. It writes ClickHouse-shaped rows to `OUT/otel_clickhouse.jsonl` and prints `{"spans_path", "span_count", "otel_format"}`. `--run-id` is required but unused; `--no-strict` and `--no-synthesize` are ignored. |

### Python

```python
from mas.library.kg.steps import run_normalize_otel

artifact = run_normalize_otel("otel_spans.jsonl", run_id="r1", output_dir="out/")
print(artifact.node_count, artifact.edge_count)
print(artifact.metadata["otel_format"], artifact.metadata["otel_spans"])
```

The artifact metadata records `otel_format` (`clickhouse_row`, `sdk` or `unknown`), `otel_spans`, `normalization_path: "otel_via_norm"` and `event_count: 0`. To reshape without `norm`:

```python
from mas.library.kg.observability.helpers import load_spans
from mas.library.kg.observability.otel_via_norm import detect_span_shape, to_clickhouse_spans
from pathlib import Path

spans = load_spans(Path("otel_spans.jsonl"))
print(detect_span_shape(spans))           # "sdk"
rows = to_clickhouse_spans(spans)         # list of ClickHouse-shaped dicts
```

### Producing spans

MAS Lab produces OTel spans from a run's `events.jsonl` with `library-telemetry` (converter profile `observe_sdk`); see [conversion.md](../../library-telemetry/docs/conversion.md). Span validation against the span specs also lives in `library-telemetry` ([verification.md](../../library-telemetry/docs/verification.md)); `library-kg` does not validate OTel spans beyond shape detection.

## 7. Relationship to the native path

`examples/qa-agent/` ships one run both ways: `events.jsonl` normalized with `run_normalize` (`kg-native.jsonld`), and its replayed `otel_spans.jsonl` normalized with `run_normalize_otel` (`kg-otel.jsonld`). The two paths do not produce identical graphs: the native path builds `MASCall`, `AgentCall`, `LLMCall`, ... nodes from every event kind and optional layer ([native-telemetry.md](native-telemetry.md)), while the OTel path builds the `Session`, `MAS`, `MASCall`, `Agent`, `AgentCall`, `LLM`, `LLMCall`, `Tool`, `ToolCall`, `State` and `Transition` nodes that `norm` derives from the span names in [Section 3](#3-span-names-dispatched-by-norm).

`observability/path_equivalence.py` (`compare_native_and_otel`, modes `default` and `complete`) compares the two on a canonical subgraph; it is exercised by `tests/kg/test_native_otel_norm_equivalence.py`.

## 8. Helper modules not on the OTel path

`observability/extractors.py` (attribute and timestamp helpers such as `normalize_attrs`, `otel_ts_to_epoch`, `extract_tokens`) and `observability/vocabulary.py` (attribute-key constants: `IoaObserveAttrs`, `GenAIAttrs`, `MasBoundaryAttrs`, `RoutingAttrs`, `LinkAttrs`, `InsightClawAttrs` for the `openclaw.*` keys) are standalone utilities with unit tests. `normalize_otel` does not call them. They are not a second normalizer.

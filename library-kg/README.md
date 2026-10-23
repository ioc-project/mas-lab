# mas-library-kg

Converts agent traces (native `events.jsonl` or OpenTelemetry spans) into a knowledge graph
(KG) document, `kg.jsonld`, that conforms to the MAS ontology, and validates, queries,
compares and stores that graph.

Use it to analyse, test or visualise multi-agent system (MAS) runs as a graph instead of
a flat log.

```bash
uv pip install -e library-kg
mas-lab kg normalize events.jsonl --run-id my-session -o kg.jsonld
mas-lab kg validate kg.jsonld
```

A KG is a set of typed nodes (`Session`, `AgentCall`, `LLMCall`, ...) and typed edges
(`hasLLMCall`, ...). An ontology defines the allowed types. OpenTelemetry (OTel) is the
open standard for traces and spans.

**Self-contained core.** The normalisation and validation code does not import
`mas.lab.*`, `mas.ctl.*` or `mas.runtime.*`. Only the optional `mas-lab kg` CLI and the
benchmark pipeline steps use the `mas-lab` framework. Neo4j support is an optional extra
(`[neo4j]`).

The package builds on two pieces of
[SCOPE](https://github.com/ioc-project/scope):

| SCOPE piece | Role here | Install |
|-----------|-----------|---------|
| `oxp-ontology` | Ontology TTL, SHACL shapes and generated `oxp_ontology.models.*`. SHACL (Shapes Constraint Language) validates graph shapes. | Hard dependency (`>=1.0.1`) |
| `norm` | SCOPE's OTel normaliser; `norm.normalize()` does the OTel-to-KG step | Optional extra `[norm]`, installed from git until it is published (the PyPI name `norm` belongs to an unrelated package, so the published name will likely be `oxp-norm`) |

First-party to this package: the native `events.jsonl`-to-KG normaliser, KG validation,
Neo4j push and dump, the `mas-lab kg` commands and the pipeline steps.

Contents: [Architecture](#architecture) · [Install](#install) · [Quick start](#quick-start) ·
[CLI](#cli) · [Pipeline steps](#pipeline-steps) · [API reference](#api-reference) ·
[Module layout](#module-layout) · [Schemas](#schemas) · [Extending](#extending) ·
[Development](#development)

---

## Architecture

The canonical input is `events.jsonl`, a structured trace of agent execution events.

```
events.jsonl  <- validate against events.schema.json
    |
    |  normalize_events() + extract_graph()
    |  Maps each event kind to node and edge types of the MAS ontology.
    |  Resolves parent-child call chains, timestamps and State content.
    v
kg.jsonld     <- verify structural invariants + SHACL + attribute conformance
    |
    |  (optional) annotate_kg_nodes(node_type, edge_name, value_fn)
    v
kg.jsonld with additional annotation nodes and edges
```

### OTel path

This library does not rebuild `events.jsonl` from OTel spans. The OTel path
(`observability/otel_via_norm.py`, needs the `[norm]` extra) reshapes the spans and calls
`norm.normalize()`. The native path uses `build_kg_document`.

```
OTel spans (ClickHouse rows or SDK JSON)
    |  otel_via_norm.normalize_otel(): reshape SDK keys to ClickHouse keys, then norm.normalize()
    v
(nodes, edges) targeting oxp_ontology.models.*
```

The inverse direction (`events.jsonl` to OTel) lives in `library-telemetry`.

Two OTel wire formats exist:

| Format | Key signals | Source |
|--------|-------------|--------|
| ClickHouse export | `SpanName` / `SpanAttributes` top-level keys | Collector / ClickHouse `otel_traces` |
| MAS SDK (`ioa_observe`) | `name` / `attributes` / `context` top-level keys | JSON span export of the Python `ioa_observe` SDK (from `mas-library-telemetry`) |

SDK spans are reshaped mechanically onto the ClickHouse keys that `norm` expects. This
library has no OTel handler or dispatch of its own.

**Ontology split.** `oxp-ontology` declares the core classes: Session, MASCall, AgentCall,
LLMCall, ToolCall, ProcessingCall, State, Transition, MAS, Agent, LLM, Tool and
Processing. Native-path classes that `oxp-ontology` does not declare yet (RAGQuery,
MemoryCall, SkillCall, governance and trajectory annotations) are declared in
`ontology/extensions/`.

---

## Install

```bash
uv pip install -e library-kg                    # normalisation, query, compare, spec, annotation
uv pip install -e "library-kg[neo4j]"           # plus Neo4j push and dump
uv pip install -e "library-kg[neo4j,dev]"       # development (pytest, ruff, validation + verify stacks)
uv pip install -e "library-kg[norm]"            # OTel path (temporary git extra until oxp-norm is published)
```

Other extras: `verify` (JSON Schema validation of `events.jsonl`), `validation` (`rdflib`
and `pyshacl`, required for the SHACL check), `bench` (pipeline steps and the `mas-lab kg`
CLI), `plot` and `all`. Without `validation`, the `shacl` check is reported as skipped,
not passed. `oxp-ontology` (`>=1.0.1`) is a hard dependency and resolves automatically.
The ontology files (`mas-ontology.ttl`, `mas-shapes.ttl`) come from `oxp-ontology`, so
validation always follows the canonical ontology package.

To validate against an unpublished local ontology version (for example `1.1.1`) without a
separate virtual environment, load `oxp-ontology` from local sources for one command:

```bash
uv run --with-editable /abs/path/to/oxp-ontology pytest tests/test_validate_kg.py -q
```

For OTel span contracts and SpanSpec definitions, also install the peer library:
`uv pip install -e library-telemetry`.

---

## Quick start

### OTel spans to KG

```python
import json
from pathlib import Path
from mas.library.kg.pipeline import build_kg_from_otel_spans, write_kg_json

spans = json.loads(Path("run.otel.json").read_text())
doc = build_kg_from_otel_spans(spans, run_id="my-session-001")
write_kg_json(doc, "kg.jsonld")
```

To get `(nodes, edges)` without the document wrapper (needs the `[norm]` extra):

```python
from mas.library.kg.observability.otel_via_norm import normalize_otel

nodes, edges = normalize_otel(spans)
```

### events.jsonl to KG

```python
from mas.library.kg.pipeline import build_kg_from_events_path, write_kg_json

doc = build_kg_from_events_path("events.jsonl", run_id="my-session-001")
write_kg_json(doc, "kg.jsonld")
```

### Validate a KG

```python
from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

try:
    report = run_validate_kg("kg.jsonld")   # all default checks, including SHACL
except KGValidationError as exc:             # raised when error_count > 0
    report = exc.report
print(report["error_count"], report["warning_count"], report["skipped_count"])
```

The individual checks live in `mas.library.kg.core.verifier` (table under
[API reference](#maslibrarykgcoreverifier)); step details in
[docs/steps.md](docs/steps.md#run_validate_kg).

### Validate events.jsonl

```python
from mas.library.kg.steps.verify_events import run_verify_events

report = run_verify_events("events.jsonl", strictness="recommended")
print(report["error_count"])
```

### Query and filter a KG in memory

```python
import json
from mas.library.kg import KGIndex, FacetQuery, KGSource, KGView

doc = json.loads(open("kg.jsonld").read())

idx = KGIndex.from_doc(doc)
print(idx.session)           # Session node or None
print(len(idx.agent_calls))  # all AgentCall nodes
print(idx.calls_by_time[:3]) # three earliest calls

fq = FacetQuery(agent_ids=["planner"], time_range=(1700000000.0, 1700000100.0))
subgraph = KGSource(doc).subgraph(fq)   # dangling edges removed

llm_calls = KGView.from_kg(doc).query("LLMCall", modelName="azure/gpt-4o")
```

### Compare two KGs

```python
from mas.library.kg import compare_kg

result = compare_kg(candidate_doc, reference_doc)
print(result.passed)    # True / False
print(result.summary)   # {"total_checks": 7, "passed": 6, "failed": 1}
for check in result.checks:
    print("PASS" if check["passed"] else "FAIL", check["name"], check.get("message", ""))
```

### Inject an app spec into a KG

```python
from mas.library.kg import merge_spec_into_kg

spec = {
    "version": "1.0",
    "intent": "Resolve customer support tickets autonomously",
    "agents": [{"id": "triage", "role": "Classify and route tickets"}],
    "tools":  [{"id": "search_kb", "description": "Search knowledge base"}],
}
doc = merge_spec_into_kg(doc, spec)   # adds IntentSpec / AgentSpec / ToolSpec nodes + conformance edges
```

### Push a KG to Neo4j

```python
from mas.library.kg.neo4j import push_kg_to_neo4j

# The push denormalises (copies session-scoped fields onto every node and edge);
# pass skip_denormalize=True to opt out.
push_kg_to_neo4j(doc, uri="bolt://localhost:7687", username="neo4j",
                 password="...", app_name="my-app")
```

### Annotate KG nodes (optional enrichment)

`annotate_kg_nodes` adds computed annotations to nodes of any type. You give the node type
to target, the edge name to add and a `value_fn`. `value_fn` takes a node dict and returns
a dict of annotation fields, or `None` to skip the node:

```python
from mas.library.kg import annotate_kg_nodes
import openai

client = openai.OpenAI()

def embed_fn(node):
    content = node.get("content", "").strip()
    if not content:
        return None
    resp = client.embeddings.create(model="text-embedding-3-small", input=[content])
    return {"vector": resp.data[0].embedding,
            "model": "text-embedding-3-small", "dimensions": 1536}

enriched_doc = annotate_kg_nodes(
    doc,
    node_type="State",
    edge_name="hasEmbedding",
    value_fn=embed_fn,
    annotation_node_type="StateEmbedding",
)
```

The function is pure: it returns a new document and does not mutate the input. `value_fn`
supplies the external computation (embedding, scoring, classification); the library only
manages graph structure.

---

## CLI

There is no standalone `mas-kg` command. `library-kg` registers a `kg` command group in
the shared `mas-lab` CLI (`KgCliComponent` in `lab_cli.py`):

```bash
# events.jsonl -> kg.jsonld (without -o / --output-dir the KG goes to stdout)
mas-lab kg normalize events.jsonl --run-id my-session -o kg.jsonld

# OTel spans -> kg.jsonld (needs the [norm] extra)
mas-lab kg normalize --otel otel_spans.jsonl --run-id my-session -o kg.jsonld

# Validate a KG document (exit code 1 when there are errors)
mas-lab kg validate kg.jsonld [--ontology-path /path/to/mas-ontology.ttl] [--strict]
mas-lab kg validate --list-checks

# Verify an events.jsonl file
mas-lab kg verify events.jsonl [--strictness recommended]

# Push to / dump from Neo4j (neo4j-push is an alias of push)
mas-lab kg push kg.jsonld --dry-run
mas-lab kg dump --session-id SESSION_ID -o dumped.jsonld

# Lower-level OTel helpers
mas-lab kg otel-to-events otel_spans.json --run-id my-session --output-dir out/   # writes out/otel_clickhouse.jsonl
mas-lab kg otel-to-kg otel_spans.json --run-id my-session --output-dir out/       # writes out/kg.jsonld, prints the KG
```

`otel-to-events` reshapes SDK spans into the ClickHouse-style rows that `norm` expects
(`otel_clickhouse.jsonl`). It does not produce native `events.jsonl`. Run
`mas-lab kg <command> --help` for every option. Steps and manifests:
[docs/steps.md](docs/steps.md), [pipelines/](pipelines/README.md).

---

## Pipeline steps

The standalone step functions live in `mas.library.kg.steps` and are pure Python (no
dependency on `mas.lab.benchmark.pipeline`). `PipelineStep` adapters for benchmark
pipelines (`normalize_events`, `normalize_otel`, `validate_kg`, `verify_events`,
`compare_kg`, `neo4j_push`, `neo4j_dump`) live in `mas.library.kg.bench` and are
registered in `library.yaml`.

Functions: `run_normalize`, `run_normalize_otel`, `run_validate_kg` (raises
`KGValidationError` on errors), `run_verify_events`, `run_annotate`, `run_compare_kg`,
`run_neo4j_push` and `run_neo4j_dump`. Each lives in the `steps` module of the same name
without the `run_` prefix (for example `steps.neo4j_push`). Step functions that return a KG
return a `KGArtifact`; the others return a report `dict`.

`artifact`, `candidate` and `reference` accept `KGArtifact | str | Path`. Full signatures,
parameters and examples: [docs/steps.md](docs/steps.md). Pipeline YAML definitions:
[`pipelines/`](pipelines/).

---

## API reference

### Top level (`mas.library.kg`)

```python
from mas.library.kg import (
    KGIndex, FacetQuery, KGSource, KGView,      # query / filter
    compare_kg, KGCompareResult,                 # comparison
    build_spec_nodes, merge_spec_into_kg,        # spec injection
    annotate_kg_nodes,                           # annotation
)
from mas.library.kg.artifact import KGArtifact, stream_artifacts  # step I/O
```

#### `KGArtifact`

The data type that step functions exchange: a KG as nodes, edges and metadata. It lives in
`mas.library.kg.artifact`.

| Member | Type | Description |
|--------|------|-------------|
| `KGArtifact.from_file(path)` | classmethod | Load from a `kg.jsonld` file |
| `KGArtifact.from_doc(doc)` | classmethod | Build from a `{"nodes": [...], "edges": [...], "metadata": {...}}` dict |
| `KGArtifact.empty()` | classmethod | Return an empty artifact |
| `.nodes`, `.edges` | `list[dict]` | All node and edge dicts |
| `.metadata` | `dict` | Free-form metadata (`run_id`, `created_at`, ...) |
| `.node_count`, `.edge_count` | `int` | Number of nodes and edges |
| `.run_id()` | method | `metadata["run_id"]` or `None` |
| `.session_id()` | method | Session ID from the `Session` node, or `None` |
| `.to_doc()` | method | Plain `{"nodes", "edges", "metadata"}` dict |
| `.to_json()` / `.to_jsonld()` | method | JSON / JSON-LD string |
| `.save(path)` | method | Write to a `*.jsonld` path (other suffixes raise `ValueError`); creates parent directories; returns `Path` |
| `.push_to_neo4j(*, uri, username, password, ...)` | method | Push to Neo4j; delegates to `push_kg_to_neo4j` |
| `.fetch_from_neo4j(*, session_id, run_id, ...)` | classmethod | Fetch a session KG from Neo4j |

`stream_artifacts(paths: Iterable[str | Path]) -> Iterator[KGArtifact]` loads many
`kg.jsonld` files lazily, one per iteration step:

```python
from mas.library.kg.artifact import stream_artifacts

for art in stream_artifacts(Path("data").glob("*/kg.jsonld")):
    print(art.run_id(), art.node_count)
```

#### Query classes

`KGIndex` (O(1) typed lookups and neighbour traversal), `FacetQuery` (JSON-serializable
filter spec, accepts camelCase and snake_case keys), `KGSource` (applies a `FacetQuery` to a
document) and `KGView` (`query(node_type, **field_filters)`; each filter is an equality
match, strings case-insensitive) are documented in [docs/kg-query.md](docs/kg-query.md).

#### `compare_kg`

```python
result: KGCompareResult = compare_kg(candidate_doc, reference_doc, *, strict=False)
```

Runs seven structural checks: `agent_coverage`, `node_distribution`, `edge_distribution`,
`call_depth`, `tool_coverage`, `delegation_topology`, `element_level_diff`. With
`strict=True`, element-level diff failures count as failures.

`KGCompareResult` members: `.passed` (`bool`, true if all checks passed), `.checks`
(`list[dict]` with `name`, `passed`, `message`), `.summary`
(`{"total_checks": int, "passed": int, "failed": int}`) and `.to_dict()` (full report).

#### `build_spec_nodes` and `merge_spec_into_kg`

```python
nodes, edges = build_spec_nodes(mas_spec)
kg_doc = merge_spec_into_kg(kg_doc, mas_spec)
```

`build_spec_nodes` builds `IntentSpec`, `AgentSpec`, `ToolSpec` and `SkillSpec` nodes from
an app spec dict. IDs are deterministic (`spec:agent:<id>`, ...), so the sub-graph is
shared across runs and can be embedded once. Each node carries `_text` for semantic
embedding. Optional spec keys: `version`, `intent` / `goal`, `agents`, `tools`, `skills`.

`merge_spec_into_kg` interns those nodes into `kg_doc` without duplication and adds
conformance edges: `AgentCall` / `LLMCall` to `AgentSpec` via `instanceOf`, and `ToolCall`
to `ToolSpec` via `invokes`. It returns the merged document; the metadata it sets is in
[docs/kg-spec.md](docs/kg-spec.md).

### `mas.library.kg.neo4j`

Requires the `[neo4j]` extra. Full reference: [docs/neo4j.md](docs/neo4j.md).

- `push_kg_to_neo4j(doc, *, uri, username, password, database, batch_size, ensure_indexes, app_name, source, annotations, extension_layers, clear_session, skip_denormalize, dry_run)`: denormalise and push a KG document with `UNWIND`-batched Cypher.
- `push_annotations_to_neo4j(doc, *, uri, username, password, database, batch_size, dry_run)`: push annotation KGs whose node refs may point at existing nodes (skips `denormalize`).
- `execute_merge(nodes, edges, *, uri, username, password, database, batch_size, ...)`: push pre-parsed node and edge lists.
- `fetch_kg_from_neo4j(*, session_id, run_id, uri, username, password, database)`: fetch a session KG as a `{"nodes", "edges"}` dict.
- `denormalize(nodes, edges, *, app_name, source, annotations, extension_layers)`: copy session-scoped attributes onto every node and edge; returns `(nodes, edges)`.
- `session_id_from_nodes(nodes)`: extract the `sessionId` from a node list.
- `build_merge_statements(nodes, edges)`: Cypher `MERGE` statements as strings (no driver needed).

In `denormalize`, caller-supplied `annotations` apply first; `session_id`, `app_name` and
`source` always win. All functions accept `from_id` / `to_id` (canonical) and
`source` / `target` (legacy) edge keys; edges with neither are skipped silently.

### `mas.library.kg.pipeline`

| Function | Description |
|----------|-------------|
| `build_kg_from_otel_spans(spans, *, run_id, ontology_path=None, ...)` | OTel spans to KG document dict (via `norm`) |
| `build_kg_document(events, *, run_id, ontology_path=None, include_*, strict, ...)` | Native events list to KG document dict |
| `build_kg_from_events_path(path, *, run_id=None, ontology_path=None, include_*, ...)` | Load `events.jsonl` from disk, return a KG document dict |
| `normalize(raw_input)` | Detect native or OTel input and return `(nodes, edges)` |
| `write_kg_json(doc, path)` | Write a KG document to a `*.jsonld` path; returns `Path` |
| `load_events_jsonl(path)` | Load `events.jsonl` into a list of dicts |
| `stream_events_jsonl(path)` | Lazy iterator over `events.jsonl` lines |

### `mas.library.kg.observability.otel_via_norm`

| Symbol | Description |
|--------|-------------|
| `normalize_otel(spans)` | OTel spans (SDK or ClickHouse shape) to `(nodes, edges)` via `norm.normalize()` |
| `detect_span_shape(spans)` | The detected wire format |
| `to_clickhouse_spans(spans)` | Reshape SDK spans onto ClickHouse keys |

### `mas.library.kg.core.graph_builder` and `native_graph_builder`

| Symbol | Description |
|--------|-------------|
| `normalize_events(events, ontology, run_id, *, include_*, strict)` | Annotate events with ontology metadata (phase 1) |
| `extract_graph(normalized, session_id_override=None, *, split_session_id, app_name_override, application_node, ...)` | Annotated events to `(nodes, edges)` (phase 2) |
| `extract_plot_events(raw_events)` | Filter to plottable kinds for visualisation |

Most callers use `build_kg_document` instead; see [docs/normalization.md](docs/normalization.md).

### `mas.library.kg.core.verifier`

| Function | Check name | Invariant |
|----------|------------|-----------|
| `check_unknown_node_types(nodes, edges)` | `unknown_node_types` | All `node_type` values are ontology-declared |
| `check_unknown_edge_types(nodes, edges)` | `unknown_edge_types` | All `edge_type` values are ontology-declared |
| `check_block_vocabulary(nodes, edges)` | `block_vocabulary` | `block` uses the allowed structural / execution / trajectory vocabulary |
| `check_layer_vocabulary(nodes, edges)` | `layer_vocabulary` | `layer` only on State / Transition nodes |
| `check_orphaned_edges(nodes, edges)` | `orphaned_edges` | Every edge endpoint is an existing node id |
| `run_shacl_validation(nodes, edges, ontology_path, run_id, *, strict=False)` | `shacl` | SHACL shape validation (needs `rdflib` and `pyshacl`) |
| `check_experiment_extension(nodes, edges)` | `experiment_extension` | Optional check (`--enable-extension-layer experiment`) |

### `mas.library.kg.observability.native.validate`

| Symbol | Description |
|--------|-------------|
| `EventValidator` | Validates events against `events.schema.json` and `events.spec.yaml` |
| `EventValidator.validate(events, *, strictness, line_offset)` | Returns an `EventValidationReport` (`.violations`: list of `EventViolation`) |
| `load_events(path)` | Load an `events.jsonl` file into a list of dicts |
| `StrictnessMode` | `"required"` / `"recommended"` / `"complete"` |
| `EventViolation` | Dataclass for one violation (`severity` is `"error"` or `"warning"`) |

For a file path, use `run_verify_events` ([docs/steps.md](docs/steps.md#run_verify_events)).

### `mas.library.kg.core.annotate`

`annotate_kg_nodes(doc, *, node_type, edge_name, value_fn, annotation_node_type)` is also
importable as `from mas.library.kg import annotate_kg_nodes`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `doc` | `dict` | KG document (`{"nodes": [...], "edges": [...], ...}`) |
| `node_type` | `str` | Only nodes with `node["node_type"] == node_type` are annotated |
| `edge_name` | `str` | Edge type added from the source node to the annotation node |
| `value_fn` | `Callable[[dict], dict \| None]` | Called with each matching node; returns annotation fields, or `None` to skip |
| `annotation_node_type` | `str` | `node_type` of the new annotation nodes (default `"Annotation"`) |

`mas.library.kg.embeddings.state_embeddings` holds the State-embedding helpers
(`iter_state_nodes`, `embedding_graph_ops`, ...) that the `embed_states` pipeline step uses.

### `mas.library.kg.exceptions`

```
KGNormalizationError           (base)
├── OtelFormatError             unknown or ambiguous wire format
├── OtelSchemaError             span missing a required field
├── UnknownSpanNameError        span name not in mappings
├── UnknownSpanBoundaryError    mas.boundary value or event kind not in mappings
└── MissingCallIdError          call_id absent on a span that requires one
```

---

## Module layout

```
mas/library/kg/
├── __init__.py               top-level public API (lazy) + package_root()
├── artifact.py               KGArtifact dataclass + stream_artifacts()
├── exceptions.py             typed exception hierarchy
├── ontology.py               TTL path resolution (oxp-ontology + local extensions)
├── pipeline.py               public batch API: build_kg_document, build_kg_from_events_path, ...
├── infra.py                  `kind: Infra` manifest -> Neo4j connection target
├── bench.py                  PipelineStep adapters (normalize_events, validate_kg, ...)
├── lab_cli.py                the `mas-lab kg` command group
├── plots.py                  multilevel trajectory plot step / processor
│
├── core/                     KG algorithms
│   ├── event_mappings.py     KIND_TO_CLASS: event kind -> ontology class
│   ├── graph_builder.py      normalize_events + extract_graph (events -> nodes/edges)
│   ├── native_graph_builder.py  NativeGraphBuilder (batch / realtime builders)
│   ├── oxp_models.py         bridge to oxp_ontology.models.*
│   ├── annotate.py           annotate_kg_nodes
│   ├── query.py              KGIndex, FacetQuery, KGSource, KGView
│   ├── compare.py            compare_kg, KGCompareResult (7 structural checks)
│   ├── spec.py               build_spec_nodes, merge_spec_into_kg
│   └── verifier.py           structural + SHACL checks
│
├── neo4j/                    Neo4j integration (requires [neo4j] extra)
│   ├── push.py               push_kg_to_neo4j, push_annotations_to_neo4j,
│   │                         execute_merge, build_merge_statements
│   ├── dump.py               fetch_kg_from_neo4j
│   └── denormalize.py        denormalize(), session_id_from_nodes()
│
├── steps/                    standalone step functions (KGArtifact | str | Path)
│   ├── normalize.py, normalize_otel.py, validate_kg.py, verify_events.py,
│   │   annotate.py, compare_kg.py, neo4j_push.py, neo4j_dump.py   (one run_* each)
│   └── embed_states.py       EmbedStatesStep - State embeddings (pipeline step)
│
├── embeddings/               State-embedding helpers (state_embeddings.py)
│
└── observability/            native extractors + thin OTel wrapper
    ├── otel_via_norm.py      OTel SDK/ClickHouse reshape + norm.normalize()
    ├── vocabulary.py         attribute key constants
    ├── extractors.py         pure parsing functions
    ├── helpers.py            shared loaders, trace-cache paths
    ├── path_equivalence.py   native vs OTel-via-norm comparator (dev/CI tool)
    └── native/               native events.jsonl handlers / completeness / validate / verify
```

Outside `src/`: `schemas/` (`events.schema.json`, `events.spec.yaml`; shipped inside the
wheel as package data), `ontology/extensions/` (native-only ontology classes),
`pipelines/`, `infra/`, `examples/`, `docker/` and `tests/`.

---

## Schemas

### `events.jsonl`: native trace format

Each line is one JSON event. Every event has a `kind` (for example `mas_call_start`,
`execution_start`, `llm_call_start`, `llm_call_end`, `tool_call_start`,
`state_update_start`, `governance_decision`), a `timestamp` (Unix seconds), `agent_id` and
`run_id`. Events that form the call tree also carry a `call_id` and an optional
`parent_call_id`. Start and end events of one call share a `call_id`. The other fields
depend on the kind.

[docs/native-telemetry.md](docs/native-telemetry.md) lists all kinds and fields. The
machine-readable definitions are
[`schemas/events.schema.json`](schemas/events.schema.json) and
[`schemas/events.spec.yaml`](schemas/events.spec.yaml).

### `kg.jsonld`: KG document

```json
{
  "@context": "...",
  "nodes": [
    {
      "id": "session-demo",
      "node_type": "Session",
      "sessionId": "session-demo",
      "runId": "demo",
      "startTime": 1700000000.0,
      "endTime": 1700000010.0,
      "canonicalId": "urn:mas:session:session-demo"
    }
  ],
  "edges": [
    {"edge_type": "hasMASCall", "from_id": "session-demo", "to_id": "mascall:demo:mas:..."}
  ],
  "metadata": { "events": 44, "nodes": 47, "edges": 79, "layers": { "...": "..." } }
}
```

The ontology defines the node types (Session, Run, MASCall, AgentCall, LLMCall, ToolCall,
ProcessingCall, State, Transition, CallAnnotation, ...) and edge types (`hasAgentCall`,
`hasLLMCall`, `hasInitialState`, `hasFinalState`, `annotates`, ...); see
[docs/ontology.md](docs/ontology.md). A real graph is
[`examples/qa-agent/kg-native.jsonld`](examples/qa-agent/kg-native.jsonld).

### Ontology sources

| TTL file | Source |
|----------|--------|
| `mas-ontology.ttl` | `oxp-ontology` |
| `mas-shapes.ttl`, `mas-shapes-custom.ttl` | `oxp-ontology` |
| Native-path extensions (RAGQuery, MemoryCall, SkillCall, governance, ...) | `library-kg/ontology/extensions/` |

Pass `ontology_path` to override the package-provided path. Native graph extraction does
not need the TTL (`KIND_TO_CLASS` is the map). TTL enrichment and SHACL validation do.

---

## Relationship to other packages

| Package | Role |
|---------|------|
| `library-telemetry` (`mas.library.telemetry`) | OTel JSON schema, SpanSpec definitions, span verification and comparison, and the native events to OTel export (the inverse direction) |
| `library-kg` (`mas.library.kg`) | Normalisation, KG extraction, KG verification, Neo4j serialisation |
| `oxp-ontology` (SCOPE) | Ontology TTL, SHACL shapes and generated models. Required by `library-kg` |
| `norm` (SCOPE) | OTel-to-KG normaliser for the OTel path. Optional extra `[norm]` |
| `mas-lab-bench` | Benchmark pipeline framework that runs the steps in `mas.library.kg.bench` (extra `[bench]`) |

---

## Extending

**New native event kind or node type.**

1. Map the kind to a class in `core/event_mappings.py:KIND_TO_CLASS`.
2. If the node needs custom fields, implement the extraction in `core/graph_builder.py`.
3. If `oxp-ontology` lacks the class, add it to
   `ontology/extensions/mas-kg-native-extensions.ttl` and document the gap.
4. Add a native `events.jsonl` fixture test.

**New OTel span shape.** OTel dispatch lives in `norm` (`norm.ioa_observe`). Do not add
handlers in this library. `observability/otel_via_norm.py` only reshapes SDK JSON onto
ClickHouse keys and calls `norm.normalize()`.

---

## Development

```bash
uv pip install -e "library-kg[neo4j,dev]" -e library-telemetry
cd library-kg && pytest tests/

# Smoke test against the shipped example trace
python -c "
from mas.library.kg.pipeline import build_kg_from_events_path
doc = build_kg_from_events_path('examples/qa-agent/events.jsonl', run_id='smoke-test')
print(f\"{len(doc['nodes'])} nodes, {len(doc['edges'])} edges\")
"
```

Tests that exercise the OTel path need the `[norm]` extra and skip without it.

Design rules:

- **Decoupled core.** The normalisation and validation code does not import `mas.lab.*`,
  `mas.ctl.*` or `mas.runtime.*`. Only `lab_cli.py`, `bench.py`, `codecs.py`, `plots.py`
  and `steps/embed_states.py` use the `mas-lab` framework;
  `tests/test_package_decoupling.py` guards lazy imports.
- **One ontology source.** TTL files resolve from `oxp-ontology`, with no local copies.
- **No hardcoded API keys.** Embedding steps take a caller-supplied function or an API-key
  environment variable name. The library never creates a provider client with an embedded
  key.

## Pipelines

Reusable benchmark pipeline YAMLs: [pipelines/README.md](pipelines/README.md). Compare
workflow: [docs/compare-kg.md](docs/compare-kg.md).

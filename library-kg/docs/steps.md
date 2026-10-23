# Step functions (`mas.library.kg.steps`)

Eight standalone Python functions that cover the knowledge graph (KG) lifecycle:
normalise events or OpenTelemetry (OTel) spans into a KG, validate it, annotate it,
compare it and store it in Neo4j. Use them from a notebook, a script or a CI job.
`mas.library.kg.bench` wraps the same functions as benchmark pipeline steps
([Pipeline YAML](#pipeline-yaml)).

The functions do not import `mas.lab.*`, `mas.ctl.*` or `mas.runtime.*`.

Smallest example:

```python
from mas.library.kg.steps import run_normalize, run_validate_kg

artifact = run_normalize("events.jsonl", run_id="run-001", output_dir="data/run-001")
report = run_validate_kg(artifact)   # raises KGValidationError when there are errors
print(artifact.node_count, report["error_count"])
```

Contents: [Overview](#overview) · [run_normalize](#run_normalize) ·
[run_normalize_otel](#run_normalize_otel) · [run_validate_kg](#run_validate_kg) ·
[run_verify_events](#run_verify_events) · [run_annotate](#run_annotate) ·
[run_compare_kg](#run_compare_kg) · [run_neo4j_push](#run_neo4j_push) ·
[run_neo4j_dump](#run_neo4j_dump) · [Streaming](#streaming-multiple-artifacts) ·
[Chaining](#chaining-steps) · [Pipeline YAML](#pipeline-yaml)

---

## Overview

| Function (module under `mas.library.kg.steps`) | Input | Output | Purpose |
|------------------------------------------------|-------|--------|---------|
| `run_normalize` (`normalize`) | `events.jsonl` path | `KGArtifact` | Native events to KG |
| `run_normalize_otel` (`normalize_otel`) | OTel spans path | `KGArtifact` | OTel spans to KG via `norm` (extra `[norm]`) |
| `run_validate_kg` (`validate_kg`) | `KGArtifact \| str \| Path` | `dict` | Structural and SHACL validation |
| `run_verify_events` (`verify_events`) | `events.jsonl` path | `dict` | Schema and spec validation of events |
| `run_annotate` (`annotate`) | `KGArtifact \| str \| Path` | `KGArtifact` | Generic node annotation |
| `run_compare_kg` (`compare_kg`) | two `KGArtifact \| str \| Path` | `dict` | Structural parity comparison |
| `run_neo4j_push` (`neo4j_push`) | `KGArtifact \| str \| Path` | `dict` | Push a KG to Neo4j |
| `run_neo4j_dump` (`neo4j_dump`) | none (reads Neo4j) | `KGArtifact` | Fetch a KG from Neo4j |

All eight are importable from `mas.library.kg.steps`.

<a id="kgartifact"></a>
**KG inputs.** A step that takes a KG accepts a `KGArtifact`, or a `str` / `Path` to a
`kg.jsonld` file. Pass a `KGArtifact` to chain steps in memory without a disk round trip.
Only `.jsonld` files are accepted (`KGArtifact.from_file` and `KGArtifact.save` reject
other suffixes).

```python
from mas.library.kg.artifact import KGArtifact

art = KGArtifact.from_file("runs/001/kg.jsonld")
art = KGArtifact.from_doc({"nodes": [...], "edges": [...], "metadata": {...}})
```

---

<a id="run_normalize"></a>
## 1. `run_normalize`: events.jsonl to KGArtifact

Module `mas.library.kg.steps.normalize`.

```python
def run_normalize(
    events_path: str | Path,
    run_id: str,
    output_dir: Optional[str | Path] = None,
    *,
    ontology_path: Optional[str] = None,
    split_session_id: bool = True,
    app_name: Optional[str] = None,
    application_node: bool = False,
    fill_synthesized_processing_defaults: bool = True,
    dry_run: bool = False,
    strict: bool = False,
    include_infrastructure: bool = False,
    include_trajectory: bool = True,
    include_provenance: bool = False,
    include_governance: bool = False,
) -> KGArtifact
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `events_path` | required | Path to the input `events.jsonl`. |
| `run_id` | required | Run identifier, written into every node and edge. |
| `output_dir` | `None` | Directory to write `kg.jsonld` into. `None` keeps the artifact in memory only. |
| `ontology_path` | `None` | Explicit path to `mas-ontology.ttl`. Omit to resolve it from `oxp-ontology`. |
| `split_session_id` | `True` | Split a `run_id` of the form `<appName>_<sessionUuid>` and write `Session.appName`. |
| `app_name` | `None` | Explicit `Session.appName` override. |
| `application_node` | `False` | Emit an `Application` node and `hasSession` edges. |
| `fill_synthesized_processing_defaults` | `True` | Populate recommended fields on synthesized `ProcessingCall` nodes. |
| `dry_run` | `False` | Parse and validate, write nothing. |
| `strict` | `False` | Raise on an observability gap (unmapped event kind, missing `call_id`, ...) instead of logging a warning and skipping the event. |
| `include_infrastructure`, `include_trajectory`, `include_provenance`, `include_governance` | `False`, `True`, `False`, `False` | Event categories (layers) to include; see [normalization.md](normalization.md#8-layer-defaults-and-control). |

Returns the normalised `KGArtifact`. With `output_dir` set and `dry_run` false, it also
writes `<output_dir>/kg.jsonld`.

```python
artifact = run_normalize("events.jsonl", run_id="run-001")
print(artifact)   # KGArtifact(nodes=42, edges=61, run_id='run-001')

# Custom ontology, parse and validate only
run_normalize("events.jsonl", run_id="test-run",
              ontology_path="/opt/ontologies/mas-ontology.ttl", dry_run=True)
```

---

<a id="run_normalize_otel"></a>
## 2. `run_normalize_otel`: OTel spans to KGArtifact

Module `mas.library.kg.steps.normalize_otel`.

```python
def run_normalize_otel(
    spans_path: str | Path,
    run_id: str,
    output_dir: Optional[str | Path] = None,
    *,
    ontology_path: Optional[str] = None,
    split_session_id: bool = True,
    app_name: Optional[str] = None,
    application_node: bool = False,
    fill_synthesized_processing_defaults: bool = True,
    strict: bool = False,
    allow_heuristics: bool = True,
    include_normalization_provenance: bool = False,
    synthesize_llm_gaps: bool = True,
    write_events: bool = True,
    dry_run: bool = False,
) -> KGArtifact
```

The step delegates to the SCOPE function `norm.normalize()`
(extra `[norm]`; see [otel-telemetry.md](otel-telemetry.md)). The options
`split_session_id`, `app_name`, `application_node`,
`fill_synthesized_processing_defaults`, `strict`, `allow_heuristics`,
`synthesize_llm_gaps` and `write_events` exist so call sites can switch between the two
normalisers. This step **ignores** them. If `norm` is not installed, or the span shape is
not recognised, the step logs a warning and returns an empty artifact instead of failing.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `spans_path` | required | OTel spans file (JSON array or JSONL of span dicts). |
| `run_id` | required | Run identifier, stored in the artifact metadata. |
| `output_dir` | `None` | Directory to write `kg.jsonld` into. |
| `ontology_path` | `None` | Unused here; `norm` resolves `oxp-ontology` itself. |
| `include_normalization_provenance` | `False` | Record `provenance_mode` and `heuristics_applied` in the metadata. |
| `dry_run` | `False` | Convert, write nothing. |

The artifact metadata holds:

- `otel_format`: the detected span shape (ClickHouse, SDK or `"unknown"`).
- `otel_spans`: the number of input spans.
- `event_count`: always `0`; this path does not produce `events.jsonl`.

```python
artifact = run_normalize_otel("traces/run-001.otel.json", run_id="run-001",
                              output_dir="data/run-001")
print(artifact.metadata["otel_format"], artifact.metadata["otel_spans"])
```

---

<a id="run_validate_kg"></a>
## 3. `run_validate_kg`: structural and SHACL validation

Module `mas.library.kg.steps.validate_kg`. SHACL (Shapes Constraint Language) checks the
graph against the ontology shapes.

```python
def run_validate_kg(
    artifact: KGArtifact | str | Path,
    *,
    ontology_path: Optional[str] = None,
    strict: bool = False,
    checks: Optional[list[str]] = None,
    extension_layers: Optional[list[str]] = None,
    warning_verbosity: str = "full",
) -> dict
```

The function always raises `KGValidationError` (`mas.library.kg.steps.validate_kg`) when
the report contains errors; there is no switch to turn that off. Catch it and read
`exc.report` for the same dict a successful call returns.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `artifact` | required | The KG to validate. |
| `ontology_path` | `None` | Path to `mas-ontology.ttl` for SHACL validation. |
| `strict` | `False` | Treat recommended-attribute gaps as errors. |
| `checks` | `None` | Check names to run. `None` runs the default checks. An unknown name is reported as an error row. |
| `extension_layers` | `None` | Optional extension layers; `["experiment"]` adds the `experiment_extension` check. |
| `warning_verbosity` | `"full"` | `"full"` (one row per warning), `"summary"` (one aggregated row per check) or `"none"`. Counts are unaffected. The CLI default is `"summary"`. |

**Default checks:** `unknown_node_types`, `unknown_edge_types`, `block_vocabulary`,
`layer_vocabulary`, `orphaned_edges`, `shacl`. Optional: `experiment_extension`.
`mas-lab kg validate --list-checks` prints them. The `shacl` check needs the
`[validation]` extra (`rdflib`, `pyshacl`).

Return value:

```python
{
    "error_count":   int,
    "warning_count": int,
    "skipped_count": int,
    "results": [{"check": str, "status": str, "detail": str}, ...],
}
```

`status` is `"pass"`, `"error"`, `"warning"` or `"skipped"`. A check that cannot run (for
example `shacl` without `pyshacl`) is `"skipped"`, never `"pass"`.

```python
from mas.library.kg.steps import run_validate_kg
from mas.library.kg.steps.validate_kg import KGValidationError

try:
    report = run_validate_kg("data/run-001/kg.jsonld", strict=True)
except KGValidationError as exc:
    report = exc.report   # same dict; error_count > 0
for r in report["results"]:
    if r["status"] != "pass":
        print(f"[{r['status']}] {r['check']}: {r['detail']}")

# Run a subset of checks
run_validate_kg(artifact, checks=["unknown_node_types", "orphaned_edges"])
```

---

<a id="run_verify_events"></a>
## 4. `run_verify_events`: events.jsonl validation

Module `mas.library.kg.steps.verify_events`. This step reads the native `events.jsonl`
file, not a `KGArtifact`, so it takes a path only.

```python
def run_verify_events(
    events_path: str | Path,
    *,
    strictness: Literal["required", "recommended", "complete"] = "required",
    fail_on_error: bool = False,
) -> dict
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `events_path` | required | Path to `events.jsonl`. |
| `strictness` | `"required"` | `"required"` checks mandatory fields; `"recommended"` adds warnings; `"complete"` enforces every rule. |
| `fail_on_error` | `False` | Raise `ValueError` if any errors are found. |

Return value:

```python
{
    "error_count":   int,
    "warning_count": int,
    "event_count":   int,
    "violations": [
        {"level": str, "severity": str,   # severity: "error" | "warning"
         "rule": str, "message": str, "kind": str, "line": int, "field": str},
        ...
    ],
}
```

```python
report = run_verify_events("data/run-001/events.jsonl", strictness="recommended")
print(f"{report['event_count']} events, {report['error_count']} errors")
for v in report["violations"]:
    print(f"  line {v['line']} [{v['severity']}] {v['rule']}: {v['message']}")
```

---

<a id="run_annotate"></a>
## 5. `run_annotate`: generic node annotation

Module `mas.library.kg.steps.annotate`. See [annotation.md](annotation.md) for the
annotation model.

```python
def run_annotate(
    artifact: KGArtifact | str | Path,
    value_fn: Callable[[dict], dict | None],
    *,
    node_type: str,
    edge_name: str,
    annotation_node_type: str = "Annotation",
    output_path: Optional[str | Path] = None,
    dry_run: bool = False,
) -> KGArtifact
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `artifact` | required | Input KG. For a path input with `output_path=None`, the input file is overwritten. |
| `value_fn` | required | Called with each matching node. Return a dict of annotation fields, or `None` to skip the node. |
| `node_type` | required | Only nodes with `node["node_type"] == node_type` are processed. |
| `edge_name` | required | Edge type added from the source node to the annotation node (for example `"hasEmbedding"`). |
| `annotation_node_type` | `"Annotation"` | `node_type` of the new annotation nodes. |
| `output_path` | `None` | Write the enriched KG here instead of overwriting. |
| `dry_run` | `False` | Compute annotations, write nothing. |

Returns the enriched `KGArtifact` (original plus new annotation nodes and edges).
`value_fn` supplies the external computation (embedding, scoring); the step only manages
graph structure.

```python
def score_fn(node: dict):
    tokens = node.get("completion_tokens", 0)
    return {"quality_score": min(1.0, tokens / 500)} if tokens else None

scored = run_annotate(
    artifact,
    score_fn,
    node_type="LLMCall",
    edge_name="hasQualityScore",
    annotation_node_type="QualityAnnotation",
    output_path="data/run-001/kg-scored.jsonld",
)
print(f"Added {scored.node_count - artifact.node_count} score nodes")
```

For an embedding `value_fn` (State nodes, `hasEmbedding` edge), see the
[README](../README.md#annotate-kg-nodes-optional-enrichment).

---

<a id="run_compare_kg"></a>
## 6. `run_compare_kg`: structural parity comparison

Module `mas.library.kg.steps.compare_kg`. The checks are described in
[kg-compare.md](kg-compare.md).

```python
def run_compare_kg(
    candidate: KGArtifact | str | Path,
    reference: KGArtifact | str | Path,
    *,
    strict: bool = False,
    fail_on_error: bool = False,
    output_path: Optional[str | Path] = None,
) -> dict
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `candidate` | required | KG under test. |
| `reference` | required | Ground-truth KG. |
| `strict` | `False` | Count element-level diff failures as comparison failures. |
| `fail_on_error` | `False` | Raise `RuntimeError` if the comparison fails. |
| `output_path` | `None` | Write the parity report JSON to this path. |

Return value:

```python
{
    "passed":  bool,
    "checks":  [{"name": str, "passed": bool, "message": str}, ...],
    "summary": {"total_checks": int, "passed": int, "failed": int},
    "reference_stats":  {...},
    "candidate_stats":  {...},
    "candidate_path": str, "reference_path": str,   # when the inputs were paths
    "report_path": str,                              # when output_path is set
}
```

The seven checks are `agent_coverage`, `node_distribution`, `edge_distribution`,
`call_depth`, `tool_coverage`, `delegation_topology` and `element_level_diff`.

```python
report = run_compare_kg(
    "data/run-001/kg.jsonld",
    "data/reference/kg.jsonld",
    output_path="data/run-001/parity_report.json",
)
print("PASS" if report["passed"] else "FAIL", report["summary"])
# {"total_checks": 7, "passed": 6, "failed": 1}
print(report["candidate_stats"])   # {"node_count": 42, "edge_count": 61, "agent_ids": [...], ...}
```

---

<a id="run_neo4j_push"></a>
## 7. `run_neo4j_push`: push a KG to Neo4j

Module `mas.library.kg.steps.neo4j_push`. Needs the `[neo4j]` extra
(`uv pip install -e "library-kg[neo4j]"` from the repository root). The underlying
function is [`push_kg_to_neo4j`](neo4j.md#push_kg_to_neo4j).

```python
def run_neo4j_push(
    artifact: KGArtifact | str | Path,
    *,
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    password_env: Optional[str] = None,
    database: Optional[str] = None,
    batch_size: int = 200,
    app_name: str = "",
    source: str = "mas-lab",
    annotations: Optional[dict] = None,
    extension_layers: Optional[list[str]] = None,
    clear_session: bool = False,
    ensure_indexes: bool = True,
    dry_run: bool = False,
) -> dict
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `artifact` | required | KG to push. |
| `uri` | `None` | Bolt URI. Falls back to `NEO4J_URI`, then `bolt://localhost:7687`. |
| `username` | `None` | Falls back to `NEO4J_USER`. |
| `password` | `None` | Literal password. Prefer `password_env`. |
| `password_env` | `None` | Name of the environment variable that holds the password. Falls back to `NEO4J_PASSWORD`. |
| `database` | `None` | Falls back to `NEO4J_DB_AGENT`, then `"neo4j"`. |
| `batch_size` | `200` | `UNWIND` batch size for Cypher writes. |
| `app_name` | `""` | Written as `appName` on every node and edge. |
| `source` | `"mas-lab"` | Data-origin marker written to every element. |
| `annotations` | `None` | Extra attributes merged onto every element before pushing. |
| `extension_layers` | `None` | Optional ontology extension layers (for example `["experiment"]`). |
| `clear_session` | `False` | Delete all existing nodes of this session before pushing. |
| `ensure_indexes` | `True` | Create covering indexes before writing (safe to repeat). |
| `dry_run` | `False` | Build Cypher, do not connect. |

Return value:

```python
{
    "rows": int,                    # Cypher rows affected
    "nodes": int, "edges": int,
    "node_count": int, "edge_count": int,   # same values as nodes / edges
    "uri": str,
    "dry_run": bool,
    "kg_path": str,                 # when the input was a file path
}
```

```bash
export NEO4J_URI=bolt://neo4j.example.com:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=...        # your database password
export NEO4J_DB_AGENT=mas
```

```python
result = run_neo4j_push("data/run-001/kg.jsonld", app_name="my-agent-app")

# Explicit connection; the password comes from os.environ["MY_NEO4J_SECRET"]
result = run_neo4j_push(
    artifact,
    uri="bolt://neo4j.internal:7687",
    username="neo4j",
    password_env="MY_NEO4J_SECRET",
    database="mas-runs",
    app_name="my-agent-app",
    source="lab-experiment-7",
    clear_session=True,   # remove previous data of this session first
)
```

---

<a id="run_neo4j_dump"></a>
## 8. `run_neo4j_dump`: fetch a KG from Neo4j

Module `mas.library.kg.steps.neo4j_dump`. Needs the `[neo4j]` extra. The underlying
function is [`fetch_kg_from_neo4j`](neo4j.md#fetch_kg_from_neo4j).

```python
def run_neo4j_dump(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    output_path: Optional[str | Path] = None,
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    password_env: Optional[str] = None,
    database: Optional[str] = None,
) -> KGArtifact
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `session_id` | `None` | Session to retrieve (preferred over `run_id`). |
| `run_id` | `None` | Run to retrieve when `session_id` is absent. Pass at least one of the two; otherwise the function raises `ValueError`. |
| `output_path` | `None` | Save the fetched `KGArtifact` here. |
| `uri`, `username`, `password`, `password_env`, `database` | `None` | Same fallbacks as [`run_neo4j_push`](#run_neo4j_push). |

Returns the `KGArtifact` reconstructed from Neo4j.

```python
artifact = run_neo4j_dump(
    session_id="sess-abc-001",
    uri="bolt://neo4j.internal:7687",
    username="neo4j",
    password_env="MY_NEO4J_SECRET",
    database="mas-runs",
    output_path="backup/sess-abc-001.kg.jsonld",
)
```

---

<a id="streaming-multiple-artifacts"></a>
## Streaming multiple KGs

`stream_artifacts` (in `mas.library.kg.artifact`) lazily loads many `kg.jsonld` files, one
at a time. It accepts any iterable of paths: glob results, a list built at run time, or
lines of a manifest file. Combine it with steps that accept a `KGArtifact`:

```python
from pathlib import Path
from mas.library.kg.artifact import stream_artifacts
from mas.library.kg.steps import run_validate_kg, run_compare_kg, run_neo4j_push
from mas.library.kg.steps.validate_kg import KGValidationError

REFERENCE = Path("reference/kg.jsonld")
RUNS = sorted(Path("data").glob("run-*/kg.jsonld"))

passed = failed = 0
for artifact in stream_artifacts(RUNS):
    rid = artifact.run_id() or "?"
    try:
        run_validate_kg(artifact)
    except KGValidationError as exc:
        print(f"[{rid}] invalid: {exc.report['error_count']} errors")
        failed += 1
        continue
    if not run_compare_kg(artifact, REFERENCE)["passed"]:
        print(f"[{rid}] parity failure")
        failed += 1
        continue
    run_neo4j_push(artifact, source="batch")
    print(f"[{rid}] pushed {artifact.node_count} nodes, {artifact.edge_count} edges")
    passed += 1

print(f"{passed} passed, {failed} failed")
```

---

## Chaining steps

KG-producing steps return a `KGArtifact` and KG-consuming steps accept one, so no
intermediate files are needed. The script below normalises OTel spans, verifies a native
capture of the same run, validates, compares against a reference and pushes.
`run_verify_events` takes a native `events.jsonl` path, so it reads a separate file.

```python
from pathlib import Path
from mas.library.kg.steps import (
    run_normalize_otel, run_verify_events, run_validate_kg,
    run_compare_kg, run_neo4j_push,
)

OUTPUT_DIR = Path("data/run-007")

# 1. OTel to KG (writes kg.jsonld)
artifact = run_normalize_otel("traces/run-007.otel.json", run_id="run-007",
                              output_dir=OUTPUT_DIR)
print(artifact.node_count, artifact.edge_count, artifact.metadata["otel_format"])

# 2. Verify the native capture of the same run (path based)
events_report = run_verify_events("traces/run-007.events.jsonl", strictness="recommended")
if events_report["error_count"]:
    raise RuntimeError(f"events.jsonl has {events_report['error_count']} errors")

# 3. Validate the in-memory artifact (raises KGValidationError on errors)
kg_report = run_validate_kg(artifact)

# 4. Compare against a reference KG loaded from disk
parity = run_compare_kg(artifact, Path("reference/kg.jsonld"),
                        output_path=OUTPUT_DIR / "parity_report.json")
print("Parity:", "PASS" if parity["passed"] else "FAIL")

# 5. Push; the connection comes from NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DB_AGENT
push_result = run_neo4j_push(artifact, app_name="my-experiment", source="run-007")
print(push_result["nodes"], push_result["edges"], push_result["uri"])
```

---

## Pipeline YAML

The same logic is available as `kind: Pipeline` step types. The adapters in
`mas.library.kg.bench` map a step `config:` to a `run_*` call and are registered in
`library.yaml` (extra `[bench]`). The table lists the config keys each adapter reads and
what it writes to the step output directory.

| Step `type` | Wraps | Config keys read | Output |
|-------------|-------|------------------|--------|
| `verify_events` | `run_verify_events` | `events_path`, `filename`, `strictness`, `fail_on_error` | `events_verify_report.json` |
| `normalize_events` | `run_normalize` | `events_path`, `filename`, `run_id`, `ontology_path`, `strict`, `include_infrastructure`, `include_trajectory`, `include_provenance`, `include_governance`, `artifact` (default `kg`) | `kg.jsonld` |
| `normalize_otel` | `run_normalize_otel` | `spans_path`, `filename`, `run_id`, `ontology_path`, `strict`, `artifact` (default `kg_otel`) | `kg.jsonld` |
| `validate_kg` | `run_validate_kg` | `kg_path`, `ontology_path`, `strict`, `fail_on_error` | `kg_validation_report.json` |
| `compare_kg` | `run_compare_kg` | `reference_artifact`, `candidate_artifact`, `reference_kg_path`, `candidate_kg_path`, `strict`, `fail_on_error` | `kg_compare_report.json` |
| `neo4j_push` | `run_neo4j_push` | `kg_path`, `infra`, `uri`, `username`, `password_env`, `database`, `app_name`, `batch_size`, `clear_session`, `dry_run` | push summary |
| `neo4j_dump` | `run_neo4j_dump` | `session_id`, `run_id`, `infra`, `uri`, `username`, `password_env`, `database` | `kg.jsonld` |

A step finds its input from its own config, from the run directory (`events.jsonl`,
`otel_sdk_spans.jsonl`), or from the output of a step named in `depends_on` (for example
`kg_path` from a `normalize_events` step). Unlike `run_validate_kg`, the `validate_kg`
step always writes its report and aborts the pipeline only when `fail_on_error: true`.
The library also ships `embed_states` (State embeddings) and
`plot_multilevel_trajectory_kg` (needs the `[plot]` extra).

[`pipelines/README.md`](../pipelines/README.md) lists the bundled pipelines. Attach the
bundled `native-to-kg` pipeline to a benchmark run in the experiment manifest:

```yaml
run:
  post:
    - ref: kg:native-to-kg
```

or on the command line:

```bash
mas-lab benchmark run my/experiment.yaml --pipeline run:kg:native-to-kg
```

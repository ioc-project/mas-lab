# Tutorial: from trace to knowledge graph

This tutorial follows one trace through the `library-kg` workflow: raw event log, knowledge
graph (KG), Neo4j, round-trip dump, diff. It is for readers who finished
[Tutorial 9](../../docs/tutorials/09-kg-scope/README.md) (native to KG, OTel to KG,
serialization, equivalence, validation) and want Neo4j push and dump, round-trip
comparison, annotation and querying. Each `mas-lab kg` command appears next to the library
function it calls, so you can switch to Python when the CLI is not enough.

Smallest example (steps 2 and 3 below):

```bash
mas-lab kg normalize library-kg/examples/qa-agent/events.jsonl --run-id tutorial-native -o /tmp/tutorial-kg.jsonld
mas-lab kg validate /tmp/tutorial-kg.jsonld
```

**What is first-party and what is SCOPE.** [SCOPE](https://ioc-project.github.io/scope/)
(System for Cognitive Observability Provenance and Explainability,
[ioc-project/scope](https://github.com/ioc-project/scope))
is the observability platform.
This tutorial uses two SCOPE pieces: `oxp-ontology` (the ontology, the SHACL shapes and the
generated models; a hard dependency of `mas-library-kg`) and, for the OpenTelemetry (OTel)
path only, `norm` (the SCOPE OTel normalizer; optional extra `[norm]`, installed from git
until it is published). The native-to-KG normalizer, validation, Neo4j push and dump, the
`mas-lab kg` commands and the pipeline steps belong to `mas-library-kg`.

**Requirements**

- A MAS Lab checkout with the packages installed from source:
  `uv pip install -e "library-kg[verify,validation,neo4j]"` (add `[norm]` for step 4).
  `library-kg` registers `mas-lab kg ...` when it is installed next to the `mas-lab` CLI.
- Docker, only for the Neo4j steps (6 to 8): `library-kg/docker/compose.yaml`.
- The sample traces in `library-kg/examples/qa-agent/`.

Run commands from the repository root.

| Reference | Doc |
|-----------|-----|
| Step functions | [steps.md](steps.md) |
| Neo4j push, dump, denormalize | [neo4j.md](neo4j.md) |
| KG structural comparison | [kg-compare.md](kg-compare.md) |
| `KGIndex`, `FacetQuery` | [kg-query.md](kg-query.md) |
| Spec injection | [kg-spec.md](kg-spec.md) |
| Node annotation | [annotation.md](annotation.md) |

---

## The scenario

`qa-agent` is the single-agent question-answering example from the first MAS Lab tutorial.
One run answers "What is 15 * 23?" with one agent call and one LLM call. The run was
captured in two formats:

```
library-kg/examples/qa-agent/
├── events.jsonl        # native event trace
├── otel_spans.jsonl    # the same run as OTel SDK spans (replayed from events.jsonl)
├── kg-native.jsonld    # KG produced by run_normalize on events.jsonl
├── kg-otel.jsonld      # KG produced by run_normalize_otel on otel_spans.jsonl
└── README.md
```

The goal: turn the trace into a KG, store it in Neo4j, and check that nothing is lost.

`events.jsonl` holds one JSON object per line. Every event has a `kind`, an `agent_id`
and a `timestamp`. Events that form the call tree also have a `call_id` and an optional
`parent_call_id`:

```jsonl
{"kind": "mas_call_start", "agent_id": "mas", "call_id": "5bcde400-...", "block": "structural", "layer": "structure", "timestamp": 1791132003.876734, ...}
{"kind": "execution_start", "agent_id": "agent", "call_id": "agent-u1-exec", "parent_call_id": "5bcde400-...", "input": "What is 15 * 23?", "block": "execution", "layer": "execution", ...}
```

[native-telemetry.md](native-telemetry.md) lists all kinds and fields.

---

## 1. Set up Neo4j

Start Neo4j 5 (Bolt on port 7687, browser on 7474, authentication disabled; a laptop lab,
see [`docker/README.md`](../docker/README.md)):

```bash
docker compose -f library-kg/docker/compose.yaml up -d
docker compose -f library-kg/docker/compose.yaml ps   # wait until healthy
```

The Neo4j Browser is at <http://localhost:7474>.

Connection settings resolve as: explicit flag, then infra manifest, then environment. The
simplest option is the environment:

```bash
export NEO4J_URI=bolt://localhost:7687
```

`NEO4J_URI` is a shortcut for the shipped manifest
[`infra/local-neo4j.yaml`](../infra/local-neo4j.yaml), which you can also pass with
`--infra`. The manifest holds the name of the password variable, never the password. With
the authentication-free compose file, leave that variable unset.

```yaml
apiVersion: mas/v1
kind: Infra
metadata:
  name: local-neo4j
spec:
  targets:
    - kind: Neo4j
      uri: bolt://localhost:7687
      database: neo4j
      username: neo4j
      password_env: NEO4J_PASSWORD   # name of the env var, never the value
```

Reference: [neo4j.md, Infra manifest](neo4j.md#infra-manifest).

---

## 2. Verify the native events

Check that `events.jsonl` is well formed (required fields present, event kinds
recognised) before you normalise it. This is the `verify_events` step.

```bash
mas-lab kg verify library-kg/examples/qa-agent/events.jsonl --strictness recommended
```

The command prints a JSON report (`error_count`, `warning_count`, `event_count`,
`violations`) and exits with status 1 when there are errors. The report has the shape of
the return value of [`run_verify_events`](steps.md#run_verify_events):

```python
from mas.library.kg.steps import run_verify_events

report = run_verify_events("library-kg/examples/qa-agent/events.jsonl",
                           strictness="recommended")   # "required" | "recommended" | "complete"
print(report["event_count"], "events,", report["error_count"], "errors,",
      report["warning_count"], "warnings")
for v in report["violations"][:3]:
    print(f"  [{v['severity']}] line {v['line']}: {v['message']}")
```

The shipped `events.jsonl` was captured with an empty `run_id`, so the verifier reports
`missing required field 'run_id'` for most lines. Normalisation still works because step 3
passes the run id explicitly. For traces you capture yourself, set `run_id` at the source
and expect zero errors.

As a pipeline step:

```yaml
- name: verify
  type: verify_events
  config:
    strictness: recommended
    fail_on_error: true
```

---

## 3. Normalise events to a KG

Normalisation reads `events.jsonl`, maps each event to ontology-typed nodes and edges,
resolves call containment and writes a `kg.jsonld` artifact.

```bash
mas-lab kg normalize library-kg/examples/qa-agent/events.jsonl \
  --run-id tutorial-native \
  -o /tmp/tutorial-kg.jsonld
```

It prints a summary (counts depend on the library version):

```json
{
  "output": "/tmp/tutorial-kg.jsonld",
  "node_count": 56,
  "edge_count": 89,
  "session_id": "session-tutorial-native",
  "run_id": null
}
```

Without `-o` / `--output-dir`, the command prints the KG to stdout. The session id is
`session-<run_id>`. The flags `--include-governance`, `--include-provenance` and
`--include-infrastructure` add event categories; see
[normalization.md](normalization.md#8-layer-defaults-and-control).

The output is a [`KGArtifact`](steps.md#kgartifact) serialised as JSON-LD:

```json
{
  "@context": "...",
  "nodes": [
    {"node_type": "Session", "id": "session-tutorial-native", "sessionId": "session-tutorial-native", ...},
    {"node_type": "MASCall", "id": "mascall:tutorial-native:mas:5bcde400-...", ...},
    ...
  ],
  "edges": [
    {"edge_type": "hasMASCall", "from_id": "session-tutorial-native", "to_id": "mascall:..."},
    ...
  ],
  "metadata": {"events": 44, "nodes": 56, "edges": 89, "layers": {...}, ...}
}
```

The command wraps [`run_normalize`](steps.md#run_normalize):

```python
from mas.library.kg.steps import run_normalize

artifact = run_normalize(
    "library-kg/examples/qa-agent/events.jsonl",
    run_id="tutorial-native",
    output_dir="/tmp/tutorial-kg",      # writes /tmp/tutorial-kg/kg.jsonld
)
print(artifact)   # KGArtifact(nodes=56, edges=89, run_id=...)
```

`run_normalize` calls `mas.library.kg.pipeline.build_kg_from_events_path`, which runs the
two phases in [normalization.md](normalization.md#1-overview): annotate each event, then
extract the graph.

---

## 4. Normalise OTel spans

If your system emits OTel spans instead of native events, normalise those. This path calls
the SCOPE function `norm.normalize()` and needs the `[norm]` extra:

```bash
uv pip install -e "library-kg[norm]"

mas-lab kg normalize --otel library-kg/examples/qa-agent/otel_spans.jsonl \
  --run-id tutorial-otel \
  -o /tmp/tutorial-kg-otel.jsonld
```

Without `norm`, the command logs a warning and writes an empty KG (0 nodes, 0 edges)
instead of failing.

To convert native events into OTel spans, use the telemetry package:

```bash
mas-lab telemetry convert library-kg/examples/qa-agent/events.jsonl \
  -o /tmp/converted_spans.jsonl --app-name qa-agent
```

Python equivalent of the normalisation:

```python
from mas.library.kg.steps import run_normalize_otel

artifact = run_normalize_otel(
    "library-kg/examples/qa-agent/otel_spans.jsonl",
    run_id="tutorial-otel",
    output_dir="/tmp/tutorial-kg-otel",
)
print(artifact.metadata["otel_format"], artifact.metadata["otel_spans"])
```

A graph built from the spans ships as
[`examples/qa-agent/kg-otel.jsonld`](../examples/qa-agent/kg-otel.jsonld). Two different
normalisers build the native and OTel graphs, so compare them structurally (step 8 and
Tutorial 9, section 4), not byte for byte.

Reference: [otel-telemetry.md](otel-telemetry.md),
[steps.md, run_normalize_otel](steps.md#run_normalize_otel).

---

## 5. Validate the KG

The default checks are `unknown_node_types`, `unknown_edge_types`, `block_vocabulary`,
`layer_vocabulary`, `orphaned_edges` and `shacl`.

```bash
mas-lab kg validate /tmp/tutorial-kg.jsonld
```

The command prints a JSON report and exits with status 1 if `error_count` is not zero:

```json
{
  "error_count": 0,
  "warning_count": 0,
  "skipped_count": 1,
  "results": [
    {"check": "unknown_node_types", "status": "pass", "detail": ""},
    {"check": "unknown_edge_types", "status": "pass", "detail": ""},
    {"check": "block_vocabulary", "status": "pass", "detail": ""},
    {"check": "layer_vocabulary", "status": "pass", "detail": ""},
    {"check": "orphaned_edges", "status": "pass", "detail": ""},
    {"check": "shacl", "status": "skipped", "detail": "shacl check requires pyshacl and rdflib (pip install 'mas-library-kg[validation]')"}
  ]
}
```

This output is what you get without the `[validation]` extra (`rdflib`, `pyshacl`): the
`shacl` row is `"skipped"` and `skipped_count` is 1. A skipped check is never a pass. With
the extra installed, the command also checks the SHACL shapes from `oxp-ontology`. On
short captures this can add warnings (for example optional LLM token fields) and some
cardinality findings; see
[Tutorial 9, section 5](../../docs/tutorials/09-kg-scope/README.md).

Useful flags: `--strict` (recommended-attribute gaps become errors), `--list-checks`,
`--check NAME` (run only this check, repeatable), `--skip-check NAME`,
`--warning-verbosity full|summary|none`.

`run_validate_kg` raises `KGValidationError` when there are errors; the report is on the
exception:

```python
from mas.library.kg.steps import run_validate_kg
from mas.library.kg.steps.validate_kg import KGValidationError

try:
    report = run_validate_kg("/tmp/tutorial-kg.jsonld")
except KGValidationError as exc:
    report = exc.report
print(report["error_count"], "errors")
```

Reference: [steps.md, run_validate_kg](steps.md#run_validate_kg).

---

## 6. Push to Neo4j

Preview first. `--dry-run` prints the Cypher without connecting and needs neither a
running Neo4j nor the `neo4j` extra:

```bash
mas-lab kg push /tmp/tutorial-kg.jsonld --dry-run
```

The output is a JSON object with `rows: 0`, the node and edge counts and a `statements`
list of Cypher strings. Then push for real (needs `uv pip install -e
"library-kg[neo4j]"` and the container from step 1):

```bash
mas-lab kg push /tmp/tutorial-kg.jsonld --infra library-kg/infra/local-neo4j.yaml
# or, with NEO4J_URI exported:
mas-lab kg push /tmp/tutorial-kg.jsonld
```

The push is idempotent: it uses Cypher `MERGE`, so a second run leaves the graph
unchanged. Add `--clear-session` to replace the session instead. `mas-lab kg neo4j-push`
is an alias of `push`. The output is the push summary:

```json
{
  "rows": 145,
  "nodes": 56,
  "edges": 89,
  "uri": "bolt://localhost:7687",
  "dry_run": false,
  "node_count": 56,
  "edge_count": 89,
  "kg_path": "/tmp/tutorial-kg.jsonld"
}
```

The command resolves the connection, loads the KG and calls
[`run_neo4j_push`](steps.md#run_neo4j_push), which calls
[`push_kg_to_neo4j`](neo4j.md#push_kg_to_neo4j). The Python form:

```python
from mas.library.kg.artifact import KGArtifact

artifact = KGArtifact.from_file("/tmp/tutorial-kg.jsonld")
result = artifact.push_to_neo4j(
    uri="bolt://localhost:7687",
    username="neo4j",
    password="",                 # the compose file runs with authentication disabled
    database="neo4j",
)
print(result["nodes"], "nodes,", result["edges"], "edges")
```

Before writing, `denormalize` copies `sessionId`, `appName`, `source` and `block` onto every
node and edge. Each node gets its `node_type` as label, and writes are `UNWIND`-batched
(200 rows by default).

---

## 7. Browse in Neo4j Browser

Open <http://localhost:7474> and run these queries. The session id is `session-` plus the
run id from step 3.

```cypher
// All nodes for our session
MATCH (n {sessionId: "session-tutorial-native"}) RETURN n LIMIT 50

// Node counts by type
MATCH (n {sessionId: "session-tutorial-native"})
RETURN labels(n)[0] AS type, count(*) AS n ORDER BY n DESC

// The LLM call made by the agent call
MATCH (a:AgentCall {sessionId: "session-tutorial-native"})-[:hasLLMCall]->(l:LLMCall)
RETURN a.id, l.id, l.startTime
```

---

## 8. Dump from Neo4j and check the round trip

Fetch the session back into a file. Pass exactly one of `--session-id` and `--run-id`.

```bash
mas-lab kg dump --session-id session-tutorial-native \
  --infra library-kg/infra/local-neo4j.yaml \
  -o /tmp/tutorial-dumped.jsonld
```

The command prints a summary with `output`, `node_count`, `edge_count`, `session_id` and
`run_id`. Python equivalent:

```python
from mas.library.kg.steps import run_neo4j_dump

artifact = run_neo4j_dump(
    session_id="session-tutorial-native",
    output_path="/tmp/tutorial-dumped.jsonld",
    uri="bolt://localhost:7687",
)
print(artifact.node_count, "nodes retrieved")
```

Compare the dump with the original. There is no `mas-lab kg compare` command. Use
[`run_compare_kg`](steps.md#run_compare_kg) or the `compare_kg` pipeline step:

```python
from mas.library.kg.steps import run_compare_kg

report = run_compare_kg(
    candidate="/tmp/tutorial-dumped.jsonld",
    reference="/tmp/tutorial-kg.jsonld",
)
print("passed:", report["passed"], report["summary"])
for check in report["checks"]:
    print(f"  {'PASS' if check['passed'] else 'FAIL'} {check['name']}")
```

The seven checks are `agent_coverage`, `node_distribution`, `edge_distribution`,
`call_depth`, `tool_coverage`, `delegation_topology` and `element_level_diff`. The last
counts toward `passed` only with `strict=True`. Each check dict has `name`, `passed` and
check-specific fields; see [kg-compare.md](kg-compare.md). To compare documents directly:

```python
from mas.library.kg import compare_kg
from mas.library.kg.artifact import KGArtifact

candidate = KGArtifact.from_file("/tmp/tutorial-dumped.jsonld")
reference = KGArtifact.from_file("/tmp/tutorial-kg.jsonld")
print(compare_kg(candidate.to_doc(), reference.to_doc()).summary)
# {"total_checks": 7, "passed": ..., "failed": ...}
```

Reference: [kg-compare.md](kg-compare.md),
[neo4j.md, fetch_kg_from_neo4j](neo4j.md#fetch_kg_from_neo4j).

---

## 9. Annotate the KG

Annotation adds computed values (embeddings, scores, labels) as new typed nodes and edges,
without re-running the normaliser:

```python
from mas.library.kg.steps import run_annotate

def score_call(node):
    """Return a score for every AgentCall node."""
    if node.get("success") is False:
        return {"score": 0.0, "reason": "execution_error"}
    return {"score": 0.75, "reason": "default"}

artifact = run_annotate(
    "/tmp/tutorial-kg.jsonld",
    value_fn=score_call,
    node_type="AgentCall",
    edge_name="hasQualityScore",
    annotation_node_type="QualityAnnotation",
    output_path="/tmp/tutorial-kg-annotated.jsonld",
)
print("annotated KG:", artifact)
```

Without `output_path`, a path input is overwritten in place. Reference:
[annotation.md](annotation.md).

---

## 10. Query the KG

[`KGIndex`](kg-query.md#kgindex) builds an in-memory index for typed lookups without a
database:

```python
from mas.library.kg import KGIndex
from mas.library.kg.artifact import KGArtifact

idx = KGIndex.from_doc(KGArtifact.from_file("/tmp/tutorial-kg.jsonld").to_doc())

print(idx.session["id"])                 # the Session node
print(len(idx.agent_calls), "agent calls,", len(idx.llm_calls), "LLM calls")
print([n["node_type"] for n in idx.calls_by_time])   # calls in time order

root = idx.find_root_agent_call()
print(root["id"], "->", [n["node_type"] for n in idx.out_neighbors(root["id"])])
```

Reference: [kg-query.md](kg-query.md) (`KGIndex`, `FacetQuery`, `KGSource`, `KGView`).

---

## 11. Run the steps as a pipeline

A `kind: Pipeline` manifest declares the same steps for the benchmark pipeline runner. The
library ships manifests in [`pipelines/`](../pipelines/README.md): `native-to-kg.yaml`
(verify, normalise, validate), `native-to-neo4j.yaml` (the same plus a push) and
`neo4j-dump.yaml`. Attach one to a benchmark run:

```bash
mas-lab benchmark run my/experiment.yaml --pipeline run:kg:native-to-neo4j
```

or reference it from the experiment manifest:

```yaml
run:
  post:
    - ref: kg:native-to-neo4j
```

| `mas-lab kg` command | Pipeline `type` | Library function |
|----------------------|-----------------|------------------|
| `verify` | `verify_events` | [`run_verify_events`](steps.md#run_verify_events) |
| `normalize` | `normalize_events` | [`run_normalize`](steps.md#run_normalize) |
| `normalize --otel` | `normalize_otel` | [`run_normalize_otel`](steps.md#run_normalize_otel) |
| `validate` | `validate_kg` | [`run_validate_kg`](steps.md#run_validate_kg) |
| `push` | `neo4j_push` | [`run_neo4j_push`](steps.md#run_neo4j_push) |
| `dump` | `neo4j_dump` | [`run_neo4j_dump`](steps.md#run_neo4j_dump) |
| no command | `compare_kg` | [`run_compare_kg`](steps.md#run_compare_kg) |
| Python only | none | [`run_annotate`](steps.md#run_annotate) |

The step config keys are in [steps.md, Pipeline YAML](steps.md#pipeline-yaml). The CLI
commands parse flags, resolve the infra manifest, call the library function and print the
result. For branching logic or custom error handling, import the functions directly.

---

## 12. The whole flow in Python

```python
#!/usr/bin/env python3
"""library-kg tutorial: the whole flow in Python."""
from mas.library.kg.steps import (
    run_verify_events, run_normalize, run_validate_kg,
    run_neo4j_push, run_neo4j_dump, run_compare_kg,
)

EVENTS = "library-kg/examples/qa-agent/events.jsonl"
RUN_ID = "tutorial-native"
SESSION = f"session-{RUN_ID}"
NEO4J = dict(uri="bolt://localhost:7687", username="neo4j", password="")

# 1. Verify events (the shipped sample has an empty run_id, so errors are expected)
report = run_verify_events(EVENTS, strictness="recommended")
print(f"{report['event_count']} events, {report['error_count']} errors")

# 2. Normalise to a KGArtifact (in memory)
artifact = run_normalize(EVENTS, run_id=RUN_ID)
print(f"normalized: {artifact.node_count} nodes, {artifact.edge_count} edges")

# 3. Validate (raises KGValidationError if there are errors)
report = run_validate_kg(artifact)
print(f"valid: {report['error_count']} errors, {report['skipped_count']} skipped")

# 4. Push to Neo4j
push = run_neo4j_push(artifact, **NEO4J)
print(f"pushed {push['nodes']} nodes, {push['edges']} edges")

# 5. Dump from Neo4j
dumped = run_neo4j_dump(session_id=SESSION, **NEO4J)
print(f"dumped {dumped.node_count} nodes, {dumped.edge_count} edges")

# 6. Round-trip comparison
diff = run_compare_kg(dumped, artifact)
print("round trip passed:", diff["passed"], diff["summary"])
```

---

## 13. Tear down

```bash
# Stop the Neo4j container (the data volume persists)
docker compose -f library-kg/docker/compose.yaml down

# Stop and remove the data volume
docker compose -f library-kg/docker/compose.yaml down -v
```

To remove one session from a running database, re-push with `--clear-session`, or run in
Neo4j Browser:

```cypher
MATCH (n {sessionId: "session-tutorial-native"}) DETACH DELETE n
```

---

## Next steps

- Merge app-spec nodes into the KG: [kg-spec.md](kg-spec.md).
- Attach embeddings, scores or any computed value with a custom `value_fn`:
  [annotation.md](annotation.md).
- Slice and filter the graph in memory without Cypher: [kg-query.md](kg-query.md).
- Process a full benchmark run lazily with `stream_artifacts(paths)`:
  [steps.md, Streaming](steps.md#streaming-multiple-artifacts).

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 9 — Building a knowledge graph from OTel and native events

> **Packages:** `mas-library-kg` (native→KG is first-party; OTel→KG reuses
> the `norm` package from OXP), `mas-library-telemetry` (span files from
> [Tutorial 8](../08-telemetry/README.md))
> **Prerequisite:** [Tutorial 8](../08-telemetry/README.md)
> **Example traces:** [`library-kg/examples/qa-agent/`](https://github.com/outshift-open/mas-lab/tree/main/library-kg/examples/qa-agent)

## Goal

Native `events.jsonl` and OTel spans are flat, ordered logs. You turn them
into a knowledge graph (KG): typed nodes (agent call, tool call, LLM call)
joined by typed edges, so you can ask "which tools did this agent call".

By the end you can build a KG from both sources, write it to a JSON-LD file or
Neo4j (a graph database), check the two KGs agree, validate against the
ontology (the published node and edge vocabulary), and keep the KG current
during a run.

## How the pieces fit

| Step | Input | Command or call | Output |
| --- | --- | --- | --- |
| 1 | `otel_spans.jsonl` | `normalize_otel` | `kg.jsonld` |
| 2 | `events.jsonl` | `normalize_events` | `kg.jsonld` |
| 3 | `kg.jsonld` | `kg neo4j-push` | Cypher or Neo4j graph |
| 4 | both `kg.jsonld` | pytest | equivalence verdict |
| 5 | `kg.jsonld` | `kg validate` | structural and SHACL report |
| 6 | growing spans file | convert, normalize, push on a timer | current graph |

SHACL is a W3C language for checking graph shape.

OXP is the open-source
[Observe and eXplain Platform](https://github.com/outshift-open/observe-and-explain-platform).
Two of its pieces are not part of MAS Lab:

- `oxp-ontology` (PyPI, hard dependency of `mas-library-kg`): the ontology,
  the SHACL shapes (`mas-shapes.ttl`) and the Python models
  `oxp_ontology.models.*`.
- `norm` (extra `mas-library-kg[norm]`, installed from git): turns OTel spans
  into ontology nodes and edges with `norm.normalize()`.

`mas-library-kg` is first-party: the native→KG normalizer, validation, Neo4j
serialization, `mas-lab kg` commands and pipeline steps. The OTel path wraps
`norm.normalize()`, so it matches the platform's graph. The native path builds
the same models without OTel or `norm`, because nothing else reads
`events.jsonl`.

`mas-lab kg normalize` and `kg neo4j-push` are shortcuts for the steps
`normalize_events`, `normalize_otel` and `neo4j_push`
(`kg:pipelines/native-to-kg.yaml`, `native-to-neo4j.yaml`). The sink is a file
path (`-o`), an infra manifest (`--infra`, `kind: Neo4j`) or `$NEO4J_URI`.
Layers `structure`, `execution` and `trajectory` are on by default;
`provenance`, `governance` and `infrastructure` are off. Enable them with
`--include-governance`, `--include-provenance` and similar flags. Reference:
[library-kg docs](https://github.com/outshift-open/mas-lab/tree/main/library-kg/docs).

## What you need

```bash
uv pip install -e "library-kg[verify]"          # native → KG
uv pip install -e "library-kg[norm,verify]"     # OTel → KG (OXP wrap)
uv pip install -e "library-telemetry[convert]"  # if you still need replay
```

Run from the repository root. OTel→KG checks skip without the `norm` extra.

```bash
pytest tests/tutorials/test_tutorial_09.py
pytest tests/tutorials/test_scenario_commands.py -k tuto-09
```

---

## Step 1 — Build a KG from OTel spans

```bash
python - <<'PY'
from pathlib import Path
from mas.library.kg.steps.normalize_otel import run_normalize_otel
out = Path("/tmp/t9-otel-kg")
art = run_normalize_otel(
    Path("library-kg/examples/qa-agent/otel_spans.jsonl"),
    run_id="t9-otel",
    output_dir=out,
)
print(art.node_count, "nodes", art.edge_count, "edges →", out / "kg.jsonld")
PY
```

You should see a node count, an edge count and the path to `kg.jsonld`.
Without the `norm` extra you get a warning and an empty KG (0 nodes).
CLI: `mas-lab kg normalize --otel spans.jsonl -o kg.jsonld`. Pipeline:
`kg:pipelines/otel-to-kg.yaml`. Shipped result:
`library-kg/examples/qa-agent/kg-otel.jsonld`.

---

## Step 2 — Build a KG from native events

Native event kinds with no mapping are skipped with a warning, not an error.

```bash
python - <<'PY'
from pathlib import Path
from mas.library.kg.steps.normalize import run_normalize
out = Path("/tmp/t9-native-kg")
art = run_normalize(
    Path("library-kg/examples/qa-agent/events.jsonl"),
    run_id="t9-native",
    output_dir=out,
)
print(art.node_count, "nodes", art.edge_count, "edges →", out / "kg.jsonld")
PY
```

The output has the same form as Step 1. CLI:
`mas-lab kg normalize events.jsonl -o kg.jsonld` (add `--include-governance`
to opt that layer in). Pipeline: `kg:pipelines/native-to-kg.yaml`. Shipped
result: `library-kg/examples/qa-agent/kg-native.jsonld`.

---

## Step 3 — Serialize to a file or Neo4j

Steps 1 and 2 wrote files. To query the graph, push it to Neo4j.

```bash
docker compose -f library-kg/docker/compose.yaml up -d
export NEO4J_URI=bolt://localhost:7687
```

`--dry-run` prints the Cypher statements (Neo4j's query language) without
connecting. `neo4j-push` is an alias of `push`.

```bash
mas-lab kg neo4j-push library-kg/examples/qa-agent/kg-native.jsonld --dry-run
mas-lab kg neo4j-push library-kg/examples/qa-agent/kg-native.jsonld \
  --infra library-kg/infra/local-neo4j.yaml --dry-run
```

For a shared Neo4j, copy `local-neo4j.yaml`, set the URI and pass it with
`--infra`. See `library-kg/docker/README.md` and the
[library-kg tutorial](https://github.com/outshift-open/mas-lab/blob/main/library-kg/docs/tutorial.md)
for a full Neo4j round trip. The OXP platform UI stack is separate
([compose file](https://github.com/outshift-open/observe-and-explain-platform/blob/main/backend/docker-compose.yml));
you do not need it here.

---

## Step 4 — Check that both KGs agree

Both paths target one ontology, so they must agree on the same run. The test
compares the canonical call tree, ignoring order and ids, on the default and
complete layers.

```bash
cd library-kg
python -m pytest tests/kg/test_native_otel_norm_equivalence.py -q
```

It needs `mas-library-kg[norm]` and `mas-library-telemetry[convert]`. Every
case should pass.

---

## Step 5 — Validate against the ontology

The validator checks vocabulary, blocks, layers and the SHACL shapes in
`oxp-ontology`.

```bash
uv pip install -e "library-kg[validation]"   # rdflib + pyshacl
mas-lab kg validate library-kg/examples/qa-agent/kg-native.jsonld
mas-lab kg validate library-kg/examples/qa-agent/kg-otel.jsonld
```

Without `[validation]`, SHACL is skipped, not passed. On the qa-agent
fixtures the vocabulary checks (`unknown_*`, `block`, `layer`, orphans) pass.
The remaining SHACL rows are mostly `sh:Warning`: LLM token and provider
fields, `hasToolCall` on a tool-less agent, and a few `belongsToMAS`
cardinality hits on this short capture. Run `kg validate` on every new
`kg.jsonld`.

---

## Step 6 — Keep the graph current in realtime

[Tutorial 8, Step 6](../08-telemetry/README.md#step-6-emit-spans-while-the-run-is-going)
showed `--realtime` spans. `normalize_otel` and `neo4j_push` read whatever is
in the file now, so Steps 1 and 3 work unchanged on a realtime file. You
re-run them periodically.

```bash
mas-lab telemetry convert library-telemetry/examples/qa-agent/events.jsonl \
  -o /tmp/t9-realtime-spans.jsonl --app-name qa-agent --realtime

mas-lab kg normalize --otel /tmp/t9-realtime-spans.jsonl \
  -o /tmp/t9-realtime-kg.jsonld --run-id t9-realtime

mas-lab kg neo4j-push /tmp/t9-realtime-kg.jsonld --dry-run
```

You should see a normalize summary with node and edge counts, then `MERGE`
statements. This path needs `mas-library-kg[norm]`; without it the KG is empty.
To go live, replace the first command with an agent run that uses the
`observability-otel-realtime` overlay, and run the other two on a timer.

Re-running on the whole file is safe:

- Node and edge ids derive from call and span ids, not a counter. The
  started/completed id pairing from Tutorial 8 keeps one call as one node.
- `neo4j-push` Cypher uses `MERGE`, not `CREATE` (check with `--dry-run`), so
  a repeated push changes nothing and new properties update in place.

Each tick's graph is a superset of the last and converges on the end-of-run
graph.

This is not incremental ingestion. `norm.normalize()` builds a fresh
`Registry()` per call, so normalizing only new spans would leave tool calls
disconnected. It does not scale to long-running agents. True streaming needs
an event stream with a resumable cursor and an incremental registry in `norm`
and `mas-library-kg`. Neither exists yet.

---

## What you learned

1. OTel→KG reuses OXP's `norm`; native→KG is first-party. Both target
   `oxp_ontology.models.*`, and a test keeps them aligned.
2. `kg normalize` and `kg neo4j-push` are shortcuts for pipeline steps; a path,
   `--infra` or `$NEO4J_URI` picks the sink, and `--dry-run` needs no Neo4j.
3. Layers match Tutorial 8's categories, with governance off by default.
   Realtime KG means re-normalizing the whole growing file on a timer.

## Next

[Tutorial 10 — Evaluation metrics](../10-evaluation-metrics/README.md) scores
runs with metrics and judges.

# Neo4j integration

Write knowledge graphs (KGs) to Neo4j, read them back and manage them with
`mas.library.kg.neo4j`. Use this page if you want to query KGs with Cypher or share them
across runs. Every function needs the `[neo4j]` extra.

Quick start (a local Neo4j without authentication; see
[`docker/README.md`](../docker/README.md)):

```bash
uv pip install -e "library-kg[neo4j]"   # from the repository root
docker compose -f library-kg/docker/compose.yaml up -d
```

```python
from mas.library.kg.neo4j import push_kg_to_neo4j, fetch_kg_from_neo4j

doc = {
    "nodes": [
        {"id": "sess-1", "node_type": "Session", "sessionId": "sess-1"},
        {"id": "agent-1", "node_type": "Agent", "name": "planner"},
        {"id": "call-1", "node_type": "AgentCall", "agent_id": "agent-1"},
    ],
    "edges": [{"from_id": "sess-1", "to_id": "call-1", "edge_type": "hasAgentCall"}],
}

conn = dict(uri="bolt://localhost:7687", username="neo4j",
            password="secret",   # example value; read it from an env var in real code
            database="neo4j")
print(push_kg_to_neo4j(doc, app_name="my-app", **conn))
# {"rows": 4, "nodes": 3, "edges": 1, "uri": "bolt://localhost:7687"}
kg = fetch_kg_from_neo4j(session_id="sess-1", **conn)
print(len(kg["nodes"]))  # 3
```

## Contents

1. [How writes work](#how-writes-work)
2. [Environment variables](#environment-variables)
3. [`denormalize`](#denormalize)
4. [`push_kg_to_neo4j`](#push_kg_to_neo4j)
5. [`push_annotations_to_neo4j`](#push_annotations_to_neo4j)
6. [`fetch_kg_from_neo4j`](#fetch_kg_from_neo4j)
7. [`execute_merge`](#execute_merge)
8. [`build_merge_statements`](#build_merge_statements)
9. [Schema and indexes](#schema-and-indexes)
10. [Property serialization](#property-serialization)
11. [Infra manifest](#infra-manifest)
12. [`mas-lab kg` commands and pipeline steps](#cli-and-steps)

---

## How writes work

**Batching.** All writes use parameterized Cypher with `UNWIND` over lists of parameter
maps, so N elements need `ceil(N / batch_size)` round trips:

```cypher
UNWIND $rows AS row
MERGE (n:AgentCall {id: row.__id})
SET n += row.__props
```

Values are never interpolated into the Cypher string. The interpolated strings from
`build_merge_statements` and `dry_run` are for display only.

**Labels and ids.** Each node gets one label: its `node_type` value (`AgentCall`,
`State`, `Session`, ...). No shared generic label is added; the class hierarchy lives in
the ontology. Nodes are matched by `id` within their label. Edge types are the ontology
edge names (`hasAgentCall`, `hasInitialState`, ...), written as relationship types.

**Why `denormalize` runs before the push.** Session-scoped fields (`sessionId`,
`appName`, `source`, `block`) may be missing on individual nodes and edges. `denormalize`
copies them from the Session node onto every element before any Cypher is generated.
Then indexes on `sessionId` work from the first write, `clear_session=True` finds every
related node, and session queries work across labels:

```cypher
MATCH (n {sessionId: "abc-123"}) RETURN n
```

**Property types.** Neo4j stores scalars, booleans and lists of scalars. See
[Property serialization](#property-serialization) for dicts and lists of dicts.

---

## Environment variables

The core functions in `mas.library.kg.neo4j` take explicit connection parameters (defaults
are in their signatures) and never read the environment. The step functions
(`run_neo4j_push`, `run_neo4j_dump`), the `mas-lab kg push` / `dump` commands and the
pipeline steps resolve each connection field in this order: explicit value, `--infra`
manifest, environment variable.

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://localhost:7687` | Bolt or `neo4j+s` connection URI |
| `NEO4J_USER` | `neo4j` | Database username |
| `NEO4J_PASSWORD` | empty | Database password (rename the variable with `password_env` / `--password-env`) |
| `NEO4J_DB_AGENT` | `neo4j` | Target database name |

---

## `denormalize`

```python
from mas.library.kg.neo4j import denormalize

nodes, edges = denormalize(nodes, edges, *, app_name="", source="mas-lab", annotations=None)
```

Copies session-scoped fields onto every node and edge. It returns new lists and never
mutates the input. It is idempotent.

| Field | Value |
|-------|-------|
| `sessionId` | From the `Session` node's `sessionId` or `id`; falls back to any node's `run_id` |
| `appName` | `app_name`; when empty, the first segment of a hierarchical `sessionId` (`app/experiment/...`) |
| `source` | `source` (default `"mas-lab"`) |
| `block` | By `node_type`, see below; a `block` already on a node is kept |

| `block` value | Node types |
|---------------|------------|
| `"structural"` | `Agent`, `LLM`, `Tool`, `Skill` (catalog and definition nodes) |
| `"trajectory"` | `State`, `Transition` (these also get `layer: "normalized"` if unset) |
| `"execution"` | Everything else (default) |

**Precedence.** `annotations` (and enabled extension-layer attributes) apply first. Then
`sessionId`, `appName` and `source` overwrite. Annotations can add arbitrary properties
but cannot override these three fields. `annotations` is one flat dict merged onto every
node and edge.

```python
nodes = [
    {"id": "sess-1", "node_type": "Session", "sessionId": "sess-1"},
    {"id": "agent-1", "node_type": "Agent", "name": "planner"},
    {"id": "state-1", "node_type": "State", "label": "thinking"},
    {"id": "call-1", "node_type": "AgentCall"},
]
edges = [{"from_id": "agent-1", "to_id": "call-1", "edge_type": "executes"}]

out_nodes, out_edges = denormalize(
    nodes, edges, app_name="my-app", source="demo",
    annotations={"team": "research", "sessionId": "IGNORED"},   # sessionId is overwritten
)
for n in out_nodes:
    print(n["node_type"], n["block"], n["sessionId"], n["appName"], n["team"])
# Session   execution   sess-1  my-app  research
# Agent     structural  sess-1  my-app  research
# State     trajectory  sess-1  my-app  research
# AgentCall execution   sess-1  my-app  research
print(out_edges[0]["sessionId"])   # sess-1: edges get it too

# Without a Session node, sessionId falls back to run_id
denormalize([{"id": "call-1", "node_type": "AgentCall", "run_id": "run-xyz"}], [])[0][0]["sessionId"]
# "run-xyz"
```

---

## `push_kg_to_neo4j`

```python
from mas.library.kg.neo4j import push_kg_to_neo4j

result = push_kg_to_neo4j(
    doc,
    *,
    uri="bolt://localhost:7687", username="neo4j", password="", database="neo4j",
    batch_size=200, ensure_indexes=True,
    app_name="", source="mas-lab", annotations=None, extension_layers=None,
    clear_session=False, skip_denormalize=False, dry_run=False,
)
```

The main write function. It takes a `{"nodes": [...], "edges": [...]}` document, calls
`denormalize` (unless `skip_denormalize=True`), then writes with `UNWIND`-batched
parameterized `MERGE` statements in one transaction. An edge whose endpoint does not
exist yet creates a minimal stub node (`id` and label only) instead of being dropped.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `doc` | required | `{"nodes": [...], "edges": [...]}` document |
| `uri` | `bolt://localhost:7687` | Neo4j Bolt or `neo4j+s` URI |
| `username` | `neo4j` | Database username |
| `password` | `""` | Database password |
| `database` | `neo4j` | Target database |
| `batch_size` | `200` | Nodes or edges per `UNWIND` batch |
| `ensure_indexes` | `True` | Create per-label indexes before writing ([Schema and indexes](#schema-and-indexes)) |
| `app_name` | `""` | Sets `appName` on every element via `denormalize` |
| `source` | `"mas-lab"` | Sets `source` on every element via `denormalize` |
| `annotations` | `None` | Extra properties merged onto every node and edge before the built-in fields |
| `extension_layers` | `None` | Extension layers passed to `denormalize`; `["experiment"]` stamps experiment, scenario, testItem and runLabel from a hierarchical session id |
| `clear_session` | `False` | `DETACH DELETE` all nodes with the document's `sessionId` before writing |
| `skip_denormalize` | `False` | Write nodes and edges as given (used for annotation documents) |
| `dry_run` | `False` | Build Cypher, do not connect or write |

Return value:

```python
{"rows": int,    # parameter rows processed
 "nodes": int, "edges": int,
 "uri": str}     # driver metadata may add more keys
```

In `dry_run` mode, `rows` is `0`, `nodes` and `edges` are the counts that would be
written, and a `statements` key holds the generated Cypher strings. No connection opens,
so use it to audit a push or capture statements in tests.

```python
doc = {
    "nodes": [
        {"id": "sess-abc", "node_type": "Session", "sessionId": "sess-abc"},
        {"id": "agent-1",  "node_type": "Agent",    "name": "summarizer"},
        {"id": "call-1",   "node_type": "AgentCall", "duration_ms": 420},
        {"id": "model-1",  "node_type": "LLM", "model": "claude-3-5-sonnet"},
    ],
    "edges": [
        {"from_id": "sess-abc", "to_id": "agent-1", "edge_type": "belongsToMAS"},
        {"from_id": "agent-1",  "to_id": "call-1",  "edge_type": "executes"},
        {"from_id": "call-1",   "to_id": "model-1", "edge_type": "executesLLM"},
    ],
}

result = push_kg_to_neo4j(doc, uri="bolt://localhost:7687", username="neo4j",
                          password="secret", database="neo4j", app_name="summarizer-service")
# {"rows": 7, "nodes": 4, "edges": 3, "uri": "bolt://localhost:7687"}  (one row per node and edge)

# Dry run
for stmt in push_kg_to_neo4j(doc, password="unused", dry_run=True)["statements"]:
    print(stmt)

# Re-push a session, add properties to every element, tune batching
push_kg_to_neo4j(doc, password="secret", app_name="summarizer-service",
                 clear_session=True,                                  # delete the session's nodes first
                 annotations={"env": "production", "region": "us-east-1"},
                 batch_size=500, ensure_indexes=False)                # indexes already exist
```

`clear_session=True` takes the session id from the document's `Session` node and runs:

```cypher
MATCH (n {sessionId: $sid}) DETACH DELETE n
```

This removes all relationships of those nodes before the new data is written. A document
without a `Session` node deletes nothing.

---

## `push_annotations_to_neo4j`

```python
from mas.library.kg.neo4j import push_annotations_to_neo4j

result = push_annotations_to_neo4j(
    annotations_doc,
    *,
    uri="bolt://localhost:7687", username="neo4j", password="", database="neo4j",
    batch_size=200, dry_run=False,
)
```

Equivalent to `push_kg_to_neo4j(..., skip_denormalize=True)` with a smaller signature:
`denormalize` is not called, and there is no `app_name`, `source`, `annotations`,
`clear_session` or `ensure_indexes` argument. The document is written as is. It returns
`{"nodes_pushed", "edges_pushed", "dry_run", "rows"}`.

Use it to write annotation nodes (such as `Metric`, `Evaluation`, `Tag`) that reference
nodes of an already pushed session. Because `denormalize` is skipped, set `sessionId` and
other session fields on each node and edge yourself. A missing edge endpoint is created as
a minimal stub; give the edge `from_type` / `to_type` role hints (`"session"`, `"run"`,
`"agent"`, `"state"`, `"transition"`) so the stub gets the right label. An endpoint with an
ambiguous role such as `"call"` is matched by id alone.

```python
push_kg_to_neo4j(session_doc, uri=URI, username=USER, password=PASS, database=DB,
                 app_name="eval-pipeline")

metrics_doc = {
    "nodes": [{
        "id": "metric-1", "node_type": "Metric",
        "sessionId": "sess-abc",       # must be pre-set
        "appName": "eval-pipeline",    # must be pre-set
        "source": "mas-lab", "block": "execution",
        "name": "latency_p99", "value": 340.5,
    }],
    "edges": [{
        "from_id": "metric-1", "to_id": "sess-abc",
        "to_type": "session",          # role hint: a stub gets the Session label
        "edge_type": "hasMetric", "sessionId": "sess-abc",
    }],
}
print(push_annotations_to_neo4j(metrics_doc, uri=URI, username=USER, password=PASS, database=DB))
# {"nodes_pushed": 1, "edges_pushed": 1, "dry_run": False, "rows": 2}
```

---

## `fetch_kg_from_neo4j`

```python
from mas.library.kg.neo4j import fetch_kg_from_neo4j

kg = fetch_kg_from_neo4j(*, session_id=None, run_id=None, uri, username, password, database)
```

Retrieves a complete KG for one session or run. Pass `session_id` (matches the
`sessionId` property) or `run_id` (matches `runId`). If both are given, `session_id`
wins. If neither is given, the function raises `ValueError`. `uri`, `username`,
`password` and `database` are as in [`push_kg_to_neo4j`](#push_kg_to_neo4j).

Return value:

```python
{
    "nodes": [
        {"id": "call-1",
         "node_type": "AgentCall",        # taken from the node label
         "sessionId": "sess-abc", "appName": "my-app", "source": "mas-lab",
         "block": "execution",
         # ...other stored properties; dict/list properties are deserialized from __json__: strings
        },
    ],
    "edges": [
        {"from_id": "agent-1", "to_id": "call-1", "edge_type": "executes",
         "sessionId": "sess-abc"},
    ],
    "metadata": {"session_id": "sess-abc", "run_id": None, "node_count": 4, "edge_count": 3,
                 "source": "neo4j", "uri": "bolt://localhost:7687", "database": "neo4j"},
}
```

Only edges whose two endpoints both belong to the session (or run) are returned.

```python
kg = fetch_kg_from_neo4j(session_id="sess-abc", uri="bolt://localhost:7687",
                         username="neo4j", password="secret", database="neo4j")
print(kg["metadata"]["node_count"], kg["metadata"]["edge_count"])   # 4 3
for node in kg["nodes"]:
    print(node["node_type"], node["id"])

kg2 = fetch_kg_from_neo4j(run_id="run-xyz", uri="bolt://localhost:7687",
                          username="neo4j", password="secret", database="neo4j")
```

---

## `execute_merge`

```python
from mas.library.kg.neo4j import execute_merge

result = execute_merge(
    nodes, edges,
    *,
    uri="bolt://localhost:7687", username="neo4j", password="", database="neo4j",
    batch_size=200, ensure_indexes=True,
    app_name="", source="mas-lab", annotations=None, extension_layers=None,
    clear_session=False, skip_denormalize=False,
)
```

The low-level write function that `push_kg_to_neo4j` calls. It takes separate node and
edge lists, not a document dict. It calls `denormalize` itself unless
`skip_denormalize=True`. It has no `dry_run`.

| Situation | Use |
|-----------|-----|
| Write a complete `{"nodes", "edges"}` document | `push_kg_to_neo4j` |
| Hold separate lists, or build a custom adapter | `execute_merge` |
| Nodes already carry `sessionId`, `appName`, `source`, `block` | `execute_merge` with `skip_denormalize=True` |
| Need `dry_run` | `push_kg_to_neo4j` |

```python
from mas.library.kg.neo4j import denormalize, execute_merge

d_nodes, d_edges = denormalize(nodes, edges, app_name="my-adapter")   # denormalize once
result = execute_merge(d_nodes, d_edges, uri="bolt://localhost:7687", username="neo4j",
                       password="secret", database="neo4j", batch_size=100,
                       skip_denormalize=True)
# {"rows": ..., "nodes": ..., "edges": ..., "uri": ...}
```

---

## `build_merge_statements`

```python
from mas.library.kg.neo4j import build_merge_statements

statements = build_merge_statements(nodes, edges)
```

Returns display-oriented Cypher strings: one `MERGE` per node and one `MATCH ... MERGE`
per edge, with values inlined as literals. It does not write anything and does not call
`denormalize`; run that first if you want the session fields in the output. Use it to
capture expected Cypher in unit tests, to print what a push would write, and to debug
schema issues.

```python
d_nodes, d_edges = denormalize(nodes, edges, app_name="demo")
for stmt in build_merge_statements(d_nodes, d_edges):
    print(stmt)
# MERGE (n:Session {id: "sess-1"}) SET n.sessionId = "sess-1", n.appName = "demo", ...
# MATCH (a:Agent {id: "agent-1"}) MATCH (b:AgentCall {id: "call-1"}) MERGE (a)-[:executes {...}]->(b)

def test_merge_statements_use_node_type_label():
    stmts = build_merge_statements([{"id": "n1", "node_type": "AgentCall", "sessionId": "s1"}], [])
    assert any("(n:AgentCall " in s for s in stmts)
```

---

## Schema and indexes

With `ensure_indexes=True` (the default), a push issues `CREATE INDEX ... IF NOT EXISTS`
for two properties on each of these labels: `Session`, `Run`, `Agent`, `AgentCall`,
`LLMCall`, `ToolCall`, `ProcessingCall`, `WorkerCall`, `RoutingCall`, `GovernanceEvent`,
`CallAnnotation`, `ContextContribution`, `State`, `Transition`.

| Index name | Property | Purpose |
|------------|----------|---------|
| `<label lowercased>_id` | `id` | Identity lookups for `MERGE` and edge endpoints |
| `<label lowercased>_sessionId` | `sessionId` | Session-scoped lookups and deletes |

Index creation is best effort: a failure logs a warning and the push continues. With
`ensure_indexes=False`, create them yourself (one label shown; repeat per label):

```cypher
CREATE INDEX agentcall_id IF NOT EXISTS FOR (n:AgentCall) ON (n.id);
CREATE INDEX agentcall_sessionId IF NOT EXISTS FOR (n:AgentCall) ON (n.sessionId);
```

Example queries:

```cypher
// All nodes in a session, grouped by block
MATCH (n {sessionId: "sess-abc"}) RETURN n.id, labels(n), n.block ORDER BY n.block

// Execution-block nodes only
MATCH (n {sessionId: "sess-abc", block: "execution"}) RETURN n

// All Agent nodes of an app, across sessions
MATCH (n:Agent {appName: "summarizer-service"}) RETURN n.name, n.sessionId

// Calls made by an agent
MATCH (a:Agent {sessionId: "sess-abc"})-[:executes]->(c:AgentCall) RETURN a.name, c.id, c.duration

// Delete a session
MATCH (n {sessionId: "sess-abc"}) DETACH DELETE n

// Check index usage
EXPLAIN MATCH (n:AgentCall {sessionId: "sess-abc"}) RETURN n
```

---

## Property serialization

Neo4j stores scalars, booleans and flat lists of scalars. Nested dicts and lists of dicts
are stored as strings with a `__json__:` prefix. Serialization happens before the write
and `fetch_kg_from_neo4j` reverses it, so the high-level API needs no manual handling.

| Python type | Stored in Neo4j as |
|-------------|--------------------|
| `str` | `"hello"` |
| `int` / `float` | `42` / `3.14` |
| `bool` | `true` / `false` |
| `list[scalar]` | `["a", "b", "c"]` |
| `dict` | `"__json__:{\"key\": \"value\"}"` |
| `list[dict]` | `"__json__:[{\"a\": 1}, {\"b\": 2}]"` |

If you read nodes with raw Cypher, you see the `__json__:...` string and decode it
yourself:

```python
import json

raw_value = node_props.get("tool_calls")
if isinstance(raw_value, str) and raw_value.startswith("__json__:"):
    tool_calls = json.loads(raw_value[len("__json__:"):])
```

---

## Infra manifest

Connection settings can live in a small `kind: Infra` manifest instead of flags. The
library owns the `Neo4j` target kind. The manifest never stores the password, only the
name of the environment variable that holds it. The shipped example is
[`infra/local-neo4j.yaml`](../infra/local-neo4j.yaml):

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
      password_env: NEO4J_PASSWORD
```

Pass it with `--infra` (CLI and the `neo4j_push` / `neo4j_dump` pipeline steps). Each
connection field resolves as explicit flag, then manifest, then environment (`NEO4J_URI`,
`NEO4J_USER`, `NEO4J_DB_AGENT`, `NEO4J_PASSWORD`). Setting `NEO4J_URI` alone is a shortcut
for the shipped manifest. The Python helpers are `mas.library.kg.infra.neo4j_target` and
`resolve_neo4j`. For a shared Neo4j instance, copy the file and change `uri`, `database`
and `password_env`.

---

<a id="cli-and-steps"></a>
## `mas-lab kg` commands and pipeline steps

```bash
# Print the Cypher without connecting (needs neither Neo4j nor the neo4j extra)
mas-lab kg push kg.jsonld --dry-run

# Push, replacing any previous copy of the session
mas-lab kg push kg.jsonld --infra library-kg/infra/local-neo4j.yaml --clear-session

# Fetch a session back into a file
mas-lab kg dump --session-id SESSION_ID --infra library-kg/infra/local-neo4j.yaml -o dumped.jsonld
```

`neo4j-push` is an alias of `push`. `push` takes `--uri`, `--username`, `--password-env`,
`--database`, `--infra`, `--app-name`, `--batch-size`, `--enable-extension-layer
experiment`, `--clear-session` and `--dry-run`. `dump` takes exactly one of
`--session-id` / `--run-id`.

The same operations exist as step functions ([`run_neo4j_push`](steps.md#run_neo4j_push),
[`run_neo4j_dump`](steps.md#run_neo4j_dump)) and as pipeline steps `neo4j_push` and
`neo4j_dump`, with the bundled `pipelines/native-to-neo4j.yaml` and
`pipelines/neo4j-dump.yaml`.

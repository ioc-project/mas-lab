# KG query layer

Four pure-Python classes that load, filter, index and search a knowledge graph (KG) in
memory: `KGIndex`, `FacetQuery`, `KGSource` and `KGView`. Use them in notebooks,
evaluation scripts and CI to slice a `kg.jsonld` file without Neo4j.

**Module:** `mas.library.kg.core.query`.
**Import:** `from mas.library.kg import KGIndex, FacetQuery, KGSource, KGView`.

A `kg.jsonld` file records one multi-agent system (MAS) run as a `{nodes, edges}` JSON
document: every `AgentCall`, `LLMCall`, `ToolCall` and supporting node, plus the edges for
containment, delegation and state transitions. The query layer works on these raw dicts
with the standard library only (`KGSource.from_file` also reads the file through
`KGArtifact`). It opens no network connection.

Smallest example:

```python
from mas.library.kg import KGSource, KGIndex, FacetQuery

sub = KGSource.from_file("kg.jsonld").subgraph(FacetQuery(agent_ids=["planner"]))
idx = KGIndex.from_doc(sub)
print(len(idx.llm_calls), "LLM calls by planner")
```

| Class | Use it to |
|-------|-----------|
| `KGSource` | Apply a `FacetQuery` to a document and get a filtered subgraph. |
| `KGIndex` | Traverse edges: parents, children, delegation chains, the root call. |
| `KGView` | Search nodes of one type by attribute values. |
| `FacetQuery` | Describe a filter as a JSON-serializable value. |

Contents: [Edge format note](#edge-format-note) · [KGIndex](#kgindex) ·
[FacetQuery](#facetquery) · [KGSource](#kgsource) · [KGView](#kgview) ·
[Common patterns](#common-patterns) · [Document structure](#full-kgjsonld-document-structure)

---

## Edge format note

`KGIndex` and `KGSource` accept three key conventions for edges, so older or hand-built
files still load. Files written by the current pipeline use the canonical names.

| Canonical (preferred) | Legacy alias | Second legacy alias |
|-----------------------|--------------|---------------------|
| `from_id`             | `source`     | `from`              |
| `to_id`               | `target`     | `to`                |
| `edge_type`           | `type`       | none                |

`KGView` reads only nodes. When you build your own dicts or tests, use `from_id`,
`to_id` and `edge_type`. Node types are read from `node_type` only (not `nodeType` or
`type`). To filter by edge type, pass the `edge_type` string, for example `"hasLLMCall"`.

Edge types come from the vocabulary of the Observe and eXplain Platform (OXP, the
open-source observability platform whose `oxp-ontology` package is a hard dependency of
this library and ships the ontology and SHACL shapes). Common edge types in a generated
KG, verified on `examples/qa-agent/kg-native.jsonld` (direction is `from_id` to `to_id`):

| Edge type | Meaning |
|-----------|---------|
| `hasMASCall` | Session to MASCall |
| `hasAgentCall` | MASCall (or a parent AgentCall) to AgentCall |
| `hasLLMCall` | AgentCall to LLMCall |
| `hasToolCall` | Parent call to ToolCall |
| `hasProcessingCall` | Parent call to ProcessingCall |
| `hasMemoryCall`, `hasSkillCall`, `hasRAGQuery` | Parent call to memory, skill or RAG call |
| `annotates` | CallAnnotation to the call it annotates |
| `contributesTo` | ContextContribution to the call it flows into |
| `hasState` | Agent or call to State |
| `hasInitialState`, `hasFinalState` | Call to its first or last State |
| `leadsTo` | State to the next State (via a Transition) |
| `inputTo` | State to the Transition that consumes it |
| `correspondsToTransition` | Call or annotation to the Transition it corresponds to |
| `executes`, `executesAgent`, `executesLLM`, `executesMAS`, `executesSession` | Execution record to the entity it runs |
| `usesLLM` | AgentCall to LLM catalog node |
| `callsAgent` | ToolCall (delegation) to target Agent |

The edge types present depend on the layers in the run. List the ones in your graph with
`collections.Counter(e["edge_type"] for e in kg_doc["edges"])`.

---

## `KGIndex`

An in-memory graph index with O(1) lookups. It indexes nodes by `id` and `node_type`, and
edges by source and target, so typed lookups and adjacency queries never scan the lists.
Use it to walk parent and child relationships, follow delegation chains and find the root
of an execution tree.

`KGIndex` does not filter. It holds every node and edge you pass in. To filter first, use
`KGSource` and pass the result to `KGIndex.from_doc`.

| Constructor | Description |
|-------------|-------------|
| `KGIndex(nodes: list[dict], edges: list[dict])` | Build from raw lists. |
| `KGIndex.from_doc(doc: dict)` | Build from a `kg.jsonld` dict (reads `doc["nodes"]` and `doc["edges"]`). |

| Property | Type | Description |
|----------|------|-------------|
| `session` | `dict \| None` | The single `Session` node, or `None`. |
| `agent_calls` | `list[dict]` | All `AgentCall` nodes. |
| `llm_calls` | `list[dict]` | All `LLMCall` nodes. |
| `tool_calls` | `list[dict]` | All `ToolCall` nodes. |
| `calls_by_time` | `list[dict]` | All AgentCall, LLMCall, ToolCall and ProcessingCall nodes sorted by `startTime` ascending. |

| Method | Description |
|--------|-------------|
| `nodes_of_type(ntype: str) -> list[dict]` | All nodes whose `node_type` equals `ntype`; `[]` if none. |
| `node(nid: str) -> dict \| None` | Exact lookup by `id`. |
| `out_neighbors(nid, edge_type=None) -> list[dict]` | Nodes reachable from `nid`, optionally restricted to one `edge_type`. |
| `in_neighbors(nid, edge_type=None) -> list[dict]` | Nodes with an edge into `nid`, optionally restricted to one `edge_type`. |
| `find_root_agent_call(agent_id=None) -> dict \| None` | Outermost AgentCall. Without `agent_id`, the AgentCall with no `parentCallId`. With `agent_id`, the earliest AgentCall of that agent, falling back to the globally earliest AgentCall if the agent is not found. |

**Load and inspect** (values are what the bundled `qa-agent` example prints):

```python
import json
from mas.library.kg import KGIndex

with open("library-kg/examples/qa-agent/kg-native.jsonld") as f:
    kg_doc = json.load(f)

idx = KGIndex.from_doc(kg_doc)
print("Session ID:", idx.session["sessionId"] if idx.session else "none")  # session-t8-native
print("AgentCalls:", len(idx.agent_calls))   # 1
print("LLMCalls:  ", len(idx.llm_calls))     # 1
print("ToolCalls: ", len(idx.tool_calls))    # 0
```

**Walk the containment tree.** `out_neighbors` takes one edge type, so a helper collects
children over the containment edge types:

```python
CONTAINS = ("hasAgentCall", "hasLLMCall", "hasToolCall", "hasProcessingCall")

def children(nid: str) -> list[dict]:
    return [c for et in CONTAINS for c in idx.out_neighbors(nid, edge_type=et)]

def walk(nid: str, depth: int = 0) -> None:
    node = idx.node(nid)
    if node is None:
        return
    print("  " * depth + f"{node['node_type']}  {node.get('agentId', '')}  id={nid}")
    for child in children(nid):
        walk(child["id"], depth + 1)

root = idx.find_root_agent_call()
if root:
    walk(root["id"])
```

**Find the parent of every ToolCall:**

```python
for tc in idx.tool_calls:
    parents = idx.in_neighbors(tc["id"], edge_type="hasToolCall")
    print(f"{tc.get('toolName', '?'):30s}  parent={parents[0]['id'] if parents else '-'}")
```

---

## `FacetQuery`

A mutable dataclass (treat it as a value) that describes a filter for a `kg.jsonld`
document. `KGSource.subgraph()` and `KGSource.load()` consume it. Every field is optional;
`None` means no constraint on that dimension.

```python
@dataclass
class FacetQuery:
    session_id:  str | None              = None
    run_id:      str | None              = None
    agent_ids:   list[str] | None        = None
    call_types:  list[str] | None        = None
    node_types:  list[str] | None        = None
    edge_types:  list[str] | None        = None
    time_range:  tuple[float, float] | None = None
```

| Field | Description |
|-------|-------------|
| `session_id` | Keep nodes whose `sessionId` matches (case-insensitive). Nodes without a `sessionId` are kept. |
| `run_id` | Keep nodes whose `runId` matches (case-insensitive). Nodes without a `runId` are kept. |
| `agent_ids` | Keep call nodes whose `agentId` is in the list (case-insensitive). Non-call nodes are kept. |
| `call_types` | Node-type whitelist restricted to the known call types (below); other names are ignored. `["LLMCall", "ToolCall"]` keeps only those two types and drops every other node, including non-call nodes. |
| `node_types` | Explicit node-type whitelist (any type). Unioned with `call_types` when both are set. |
| `edge_types` | Keep edges whose `edge_type` is in the list. Applied after node filtering; dangling edges are always removed. |
| `time_range` | `(t0, t1)` in Unix epoch seconds. Keep nodes whose window `[startTime, endTime]` overlaps `[t0, t1]`. A missing `endTime` falls back to `startTime`; a missing `startTime` counts as 0, so such nodes are dropped unless `t0 <= 0`. |

| Method | Description |
|--------|-------------|
| `to_dict() -> dict` | Serialize to a plain dict; `None` fields are omitted. |
| `FacetQuery.from_dict(d: dict)` | Deserialize. Accepts `snake_case` and `camelCase` keys. |

### `call_types` vs `node_types`

The recognised call types are `AgentCall`, `LLMCall`, `ToolCall`, `SkillCall`,
`ProcessingCall`, `ThinkingCall`, `MITMCall`, `MASCall` and `TaskCall`. With
`call_types=["LLMCall", "ToolCall"]` the type whitelist is exactly those two types, so
Session, State, Transition, Agent and the other call types are removed. To keep
structural nodes, add them through `node_types`:

```python
FacetQuery(call_types=["LLMCall"], node_types=["Session", "Agent"])
```

Passing only unrecognised names in `call_types` (for example `["Foo"]`) gives an empty
whitelist and an empty subgraph. `agent_ids` is the opposite: it filters call nodes only
and keeps every non-call node that survived the type whitelist.

```python
q = FacetQuery(
    session_id="session-abc-123",
    agent_ids=["orchestrator", "analyst"],
    call_types=["LLMCall", "ToolCall"],
    time_range=(1_716_000_000.0, 1_716_000_060.0),
)

# Round trip; both key styles load (a JavaScript client may send camelCase)
q1 = FacetQuery(session_id="s1", agent_ids=["cra"], time_range=(0.0, 100.0))
d = q1.to_dict()   # {'session_id': 's1', 'agent_ids': ['cra'], 'time_range': (0.0, 100.0)}
assert FacetQuery.from_dict(d) == q1                       # time_range stays a tuple; json.dump writes a list
q2 = FacetQuery.from_dict({"sessionId": "s1", "agentIds": ["cra"], "timeRange": [0.0, 100.0]})
```

To store a preset, `json.dump` a dict such as
`{"sessionId": "my-session", "callTypes": ["AgentCall", "LLMCall"]}` and reload it with
`FacetQuery.from_dict(json.load(f))`.

---

## `KGSource`

The data-access layer. It applies a `FacetQuery` to a `kg.jsonld` dict and returns a
filtered subgraph with the same top-level keys as the input (`@context`, `nodes`,
`edges`, `metadata`; `nodes` and `edges` replaced). Use it to narrow a full execution
trace to a relevant slice before analysis or indexing. It builds no index and is cheap to
construct.

| Constructor | Description |
|-------------|-------------|
| `KGSource(kg: dict)` | Wrap an already-loaded `kg.jsonld` dict. |
| `KGSource.from_file(path: str \| Path)` | Read a `*.jsonld` file via `KGArtifact` (see [steps.md](steps.md#kgartifact)). Other suffixes raise `ValueError`. Expands `~` and resolves symlinks. |

| Method | Description |
|--------|-------------|
| `subgraph(query: FacetQuery \| None = None) -> dict` | Filtered copy of the document. With `query=None`, a shallow copy of the full document. |
| `load(query: FacetQuery \| None = None) -> tuple[list[dict], list[dict]]` | Returns `(nodes, edges)` after applying the query. |

**Filtering order.** A node must survive every active filter. Edges are filtered last, and
an edge whose source or target was removed is dropped.

1. `node_types` / `call_types`: node-type whitelist.
2. `agent_ids`: call nodes must match `agentId` (case-insensitive).
3. `session_id`: nodes with a `sessionId` must match; nodes without one are kept.
4. `run_id`: nodes with a `runId` must match; nodes without one are kept.
5. `time_range`: `startTime` / `endTime` must overlap the window.
6. `edge_types`: edge-type whitelist.
7. Dangling edge removal.

```python
from mas.library.kg import KGSource, FacetQuery

src = KGSource.from_file("kg.jsonld")

# All active filters apply in one pass
nodes, edges = src.load(FacetQuery(
    agent_ids=["orchestrator"],
    call_types=["LLMCall"],
    time_range=(1_716_000_010.0, 1_716_000_050.0),
))

# Structural nodes only
sub = src.subgraph(FacetQuery(node_types=["Session", "Agent", "State", "Transition"]))

# One edge type; every remaining edge has edge_type == "hasLLMCall"
sub = src.subgraph(FacetQuery(edge_types=["hasLLMCall"]))
for e in sub["edges"][:5]:
    print(e["from_id"], "->", e["to_id"])
```

---

## `KGView`

An equality-based search over a flat list of nodes: "all nodes of type X where field Y
equals Z". Use it to explore node attributes; combine it with `KGIndex` to traverse
edges. It indexes nodes by `node_type` and `id` at construction time.

| Constructor | Description |
|-------------|-------------|
| `KGView(nodes: list[dict])` | Build from a raw list of node dicts. |
| `KGView.from_kg(kg: dict)` | Build from a `kg.jsonld` dict (reads `kg["nodes"]`). |

| Method | Description |
|--------|-------------|
| `query(node_type: str, **field_filters) -> list[dict]` | Nodes of `node_type` matching every keyword filter, sorted by `startTime` ascending. |
| `get(node_id: str \| None) -> dict \| None` | Node by `id`. `None` returns `None`. |
| `types() -> list[str]` | The `node_type` values present in the view. |

Each keyword filter of `query()` is matched against the node field, and all filters must
match (logical AND). With no filters, `query()` returns every node of the type.

| Filter value | Matching |
|--------------|----------|
| `str` | Case-insensitive equality: `str(field_value).lower() == filter_value.lower()`. |
| `None` | The field is absent or an empty string. |
| Any other type (int, float, bool, list, ...) | Strict equality. Callables are not predicates; filter in Python instead. |

```python
from mas.library.kg import KGView

view = KGView.from_kg(kg_doc)

for call in view.query("LLMCall"):                       # chronological
    print(call["id"], call.get("modelName"), call.get("startTime"))

sre_tools = view.query("ToolCall", agentId="sre")        # "SRE" and "sre" both match

# Predicates run in Python: LLMCalls with a prompt over 500 characters
long_inputs = [n for n in view.query("LLMCall")
               if isinstance(n.get("prompt"), str) and len(n["prompt"]) > 500]

for t in sorted(view.types()):                           # node types present
    print(f"{t:25s}  {len(view.query(t)):4d}")

node = view.get("agentcall:t8-native:agent:agent-u1-exec")   # a node id, not a callId
```

**Root AgentCalls.** `parentCallId` holds the parent's `callId`, not its node `id`. In a
generated KG every AgentCall sits under a MASCall, so its `parentCallId` is the MASCall's
`callId`. `view.query("AgentCall", parentCallId=None)` therefore returns an empty list on
generated KGs and is useful on hand-built or older ones. Use
`KGIndex.find_root_agent_call()` for the outermost AgentCall in either case.

---

## Common patterns

**Filter with `KGSource`, then index with `KGIndex`.** `KGSource` narrows the data and
`KGIndex` makes it traversable. The hand-off is a plain subgraph dict.

```python
from mas.library.kg import KGSource, KGIndex, FacetQuery

sub = KGSource.from_file("kg.jsonld").subgraph(
    FacetQuery(agent_ids=["orchestrator"], call_types=["AgentCall", "LLMCall", "ToolCall"])
)
idx = KGIndex.from_doc(sub)
root = idx.find_root_agent_call("orchestrator")
print(len(idx.out_neighbors(root["id"], edge_type="hasLLMCall")), "direct LLM calls")
```

**Filter, then search attributes with `KGView`.** `KGIndex` and `KGView` can share one
subgraph: `KGIndex` for edges, `KGView` for node lookups.

```python
from mas.library.kg import KGSource, KGIndex, KGView, FacetQuery

sub = KGSource.from_file("kg.jsonld").subgraph(
    FacetQuery(time_range=(1_716_000_000.0, 1_716_000_300.0))
)
idx, view = KGIndex.from_doc(sub), KGView(sub["nodes"])

print(len(view.query("ToolCall", toolName="search")), "search calls in the window")
for agent_call in view.query("AgentCall"):
    llm_children = idx.out_neighbors(agent_call["id"], edge_type="hasLLMCall")
    print(f"{agent_call['agentId']:20s}  LLM calls: {len(llm_children)}")
```

**Compare agents.** Slice per agent and count calls:

```python
src = KGSource.from_file("kg.jsonld")
all_agents = {n.get("agentId") for n in KGView.from_kg(src.subgraph()).query("AgentCall")
              if n.get("agentId")}

for agent_id in sorted(all_agents):
    nodes, _ = src.load(FacetQuery(agent_ids=[agent_id],
                                   call_types=["AgentCall", "LLMCall", "ToolCall"]))
    v = KGView(nodes)
    print(f"{agent_id:20s}  AgentCalls={len(v.query('AgentCall'))}  "
          f"LLMCalls={len(v.query('LLMCall'))}  ToolCalls={len(v.query('ToolCall'))}")
```

**Apply one stored filter to many runs:**

```python
import json
from pathlib import Path

with open("experiment-filter.json") as f:
    q = FacetQuery.from_dict(json.load(f))   # e.g. {"agentIds": ["cra"], "callTypes": ["LLMCall"]}

for run_path in sorted(Path("runs/").glob("*/kg.jsonld")):
    nodes, edges = KGSource.from_file(run_path).load(q)
    print(f"{run_path.parent.name:30s}  nodes={len(nodes):4d}  edges={len(edges):4d}")
```

**Build a delegation tree** (nested AgentCall hierarchy) with `find_root_agent_call` and
recursive `out_neighbors` over `hasAgentCall` edges:

```python
import json

def build_tree(nid: str) -> dict:
    node = idx.node(nid) or {}
    return {
        "id": nid,
        "agentId": node.get("agentId", ""),
        "node_type": node.get("node_type", ""),
        "children": [build_tree(c["id"])
                     for c in idx.out_neighbors(nid, edge_type="hasAgentCall")],
    }

root = idx.find_root_agent_call()
if root:
    print(json.dumps(build_tree(root["id"]), indent=2))
```

**Check that a filter leaves no dangling edges.** `KGSource` always removes them; this
check works as a sanity test in your own pipelines:

```python
sub = KGSource.from_file("kg.jsonld").subgraph(FacetQuery(node_types=["AgentCall", "LLMCall"]))
kept = {n["id"] for n in sub["nodes"]}
dangling = [e for e in sub["edges"] if e.get("from_id") not in kept or e.get("to_id") not in kept]
print("Dangling edges (expect 0):", len(dangling))
```

---

## Full `kg.jsonld` document structure

A minimal document that all four classes accept. Documents written by the pipeline carry
more fields: `@context`, a `metadata` object with counts and ontology information,
`canonicalId`, `@type`, `spanId` and others. In generated KGs, node `id`s are prefixed
(for example `agentcall:<run>:<agent>:<callId>`), `callId` is the bare call identifier,
and `parentCallId` refers to the parent's `callId`. The short ids below are for
readability.

```json
{
  "metadata": {"events": 12, "nodes": 4, "edges": 3, "source": "events.jsonl"},
  "nodes": [
    {"id": "sess-1", "node_type": "Session", "sessionId": "sess-1", "runId": "my-run-001",
     "startTime": 1716000000.0, "endTime": 1716000060.0,
     "inputQuery": "What is the weather in Paris?", "finalResponse": "The weather is sunny."},
    {"id": "ac-1", "node_type": "AgentCall", "callId": "ac-1", "agentId": "orchestrator",
     "agentName": "orchestrator", "sessionId": "sess-1", "runId": "my-run-001",
     "parentCallId": "", "startTime": 1716000001.0, "endTime": 1716000055.0},
    {"id": "llm-1", "node_type": "LLMCall", "callId": "llm-1", "agentId": "orchestrator",
     "sessionId": "sess-1", "runId": "my-run-001", "parentCallId": "ac-1",
     "startTime": 1716000002.0, "endTime": 1716000010.0, "modelName": "azure/gpt-4o",
     "prompt": "What is the weather in Paris?", "completion": "I will call the weather tool."},
    {"id": "tc-1", "node_type": "ToolCall", "callId": "tc-1", "agentId": "orchestrator",
     "sessionId": "sess-1", "runId": "my-run-001", "parentCallId": "llm-1",
     "startTime": 1716000011.0, "endTime": 1716000020.0, "toolName": "get_weather",
     "toolArguments": "{\"city\": \"Paris\"}", "toolOutput": "Sunny, 22°C"}
  ],
  "edges": [
    {"edge_type": "hasLLMCall",  "from_id": "ac-1",  "to_id": "llm-1"},
    {"edge_type": "hasToolCall", "from_id": "llm-1", "to_id": "tc-1"},
    {"edge_type": "hasMASCall",  "from_id": "sess-1", "to_id": "ac-1"}
  ]
}
```

### Common node fields

| Field | Present on | Description |
|-------|-----------|-------------|
| `id` | All nodes | Unique node identifier. |
| `node_type` | All nodes | Node type string (see below). |
| `sessionId`, `runId` | Most nodes | Session and run identifiers. |
| `agentId` | Call nodes | Agent that owns the call. |
| `parentCallId` | Call nodes | `callId` of the enclosing call; `""` or absent for root calls. |
| `startTime`, `endTime` | Call nodes | Unix epoch, float seconds. |
| `toolName` | ToolCall | Name of the tool invoked. |
| `modelName` | LLMCall | Model identifier (for example `"azure/gpt-4o"`). |
| `inputContent` / `outputContent` | AgentCall, ProcessingCall | Input and output payload (may be truncated). |
| `toolArguments` / `toolOutput` | ToolCall | JSON-encoded arguments and the tool result. |
| `prompt` / `completion` | LLMCall | Prompt and completion text. |

### Common node types

| `node_type` | Category | Description |
|-------------|----------|-------------|
| `Session` | Structural | Top-level session record |
| `Run` | Structural | Run metadata |
| `Agent` | Structural | Agent catalog entry |
| `AgentCall` | Call | One invocation of an agent |
| `LLMCall` | Call | One call to a language model |
| `ToolCall` | Call | One tool or function call |
| `ProcessingCall` | Call | A processing step (design patterns, etc.) |
| `SkillCall` | Call | A skill invocation |
| `MASCall` | Call | One run of the whole MAS |
| `ThinkingCall` | Call | An extended-thinking step |
| `State` | State machine | State in the agent state machine |
| `Transition` | State machine | Transition between two State nodes |
| `ContextContribution` | Annotation | Context segment contributed to a call |
| `CallAnnotation` | Annotation | Annotation attached to a call |
| `Tool` | Catalog | Tool catalog entry |
| `LLM` | Catalog | LLM catalog entry |
| `MAS` | Catalog | MAS entry |

# KG spec injection

`mas.library.kg.core.spec` adds the design-time specification of a multi-agent system (MAS), meaning its declared agents, tools and skills, to a knowledge graph (KG) built from a run. It links each runtime call node to the spec node that declared it. Use it to check what ran against what was intended.

Public API: `from mas.library.kg import build_spec_nodes, merge_spec_into_kg`.

```python
from mas.library.kg import merge_spec_into_kg

mas_spec = {"agents": [{"id": "triage"}], "tools": [{"id": "search_kb"}]}
merge_spec_into_kg(kg_doc, mas_spec)   # kg_doc: a KG document dict, modified in place
```

**Contents:** [Concepts](#conceptual-overview) · [`mas_spec` shape](#the-mas_spec-dictionary-shape) · [`build_spec_nodes`](#build_spec_nodes) · [`merge_spec_into_kg`](#merge_spec_into_kg) · [Diagram](#ascii-diagram) · [Stable IDs](#stable-ids) · [Extending](#extending-the-spec)

## Conceptual overview

A KG document from a run holds two kinds of information.

| Layer | Produced | Contains |
|-------|----------|----------|
| Runtime | During execution | `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, ... nodes that record what happened |
| Spec (design time) | At system setup | `IntentSpec`, `AgentSpec`, `ToolSpec`, `SkillSpec` nodes that record what was intended |

This module creates the spec nodes and links runtime nodes to them with *conformance edges* (`instanceOf`, `invokes`).

The spec sub-graph works as the system's blueprint, shared by every run of one configuration. The same `mas_spec` always yields the same node IDs ([Stable IDs](#stable-ids)), so the sub-graph can be *interned*: a node is added only if no node with the same `id` exists. `merge_spec_into_kg` interns per document. Storing the spec sub-graph once across many run KGs is up to the caller.

Interning has two benefits:

- A vector index (embedding lookup) built over the `_text` of `AgentSpec` and `ToolSpec` nodes survives KG regeneration. It does not grow with the number of runs.
- Cross-run comparison uses ID lookups instead of fuzzy text matching.

Conformance edges enable:

- **Compliance checking**: walk from a runtime call to its spec and compare inputs and outputs with `in_schema` / `out_schema`.
- **Root-cause analysis**: when a `ToolCall` fails, follow `invokes` to the `ToolSpec` to read the expected I/O contract.
- **Capability coverage**: count which `AgentSpec` nodes (and their declared `capabilities`) runs exercised.
- **Semantic search**: embed `AgentSpec._text` to answer "which agents can handle X?".

## The `mas_spec` dictionary shape

All top-level keys are optional; the module skips absent sections. Agent `skills` and `tools` entries must be the bare `id` of a `skills` / `tools` entry. The edge target is built as `spec:skill:<value>` / `spec:tool:<value>`, so `"tool:search_kb"` would point at the nonexistent `spec:tool:tool:search_kb`.

| Key | Type | Meaning |
|-----|------|---------|
| `version` | str | Version tag, for example `"v1.2.0"` or a git SHA. Default: `sha256(repr(mas_spec))[:12]`. |
| `intent` / `goal` | str | Purpose of the system. `intent` wins when both are present. |
| `agents` | list[dict] | Per entry: `id` (required), `role`, `intent`, `capabilities` (list[str]), `skills` (skill ids), `tools` (tool ids). |
| `tools` | list[dict] | Per entry: `id` (required), `description`, `in_schema`, `out_schema` (JSON Schema fragments). |
| `skills` | list[dict] | Per entry: `id` (required), `description`, `requires` (list[str] preconditions), `concludes` (str postcondition). |

```python
mas_spec = {
    "version": "v1.0.0",
    "intent": "Resolve customer support tickets using a two-agent pipeline.",
    "agents": [
        {
            "id": "triage",
            "role": "Triage Agent",
            "intent": "Classify incoming tickets and route to the right specialist.",
            "capabilities": ["intent_classification", "priority_scoring"],
            "skills": ["summarise"],
            "tools": ["search_kb"],
        },
        {
            "id": "resolver",
            "role": "Resolver Agent",
            "intent": "Generate a resolution for classified tickets.",
            "capabilities": ["answer_generation", "policy_lookup"],
            "skills": [],
            "tools": ["search_kb", "submit_resolution"],
        },
    ],
    "tools": [
        {
            "id": "search_kb",
            "description": "Search the knowledge base.",
            "in_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"},
                               "top_k": {"type": "integer", "default": 5}},
                "required": ["query"],
            },
            "out_schema": {"type": "array", "items": {"type": "string"}},
        },
        {
            "id": "submit_resolution",
            "in_schema": {
                "type": "object",
                "properties": {"ticket_id": {"type": "string"},
                               "resolution": {"type": "string"}},
                "required": ["ticket_id", "resolution"],
            },
            "out_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
        },
    ],
    "skills": [
        {
            "id": "summarise",
            "description": "Summarise a text.",
            "requires": ["text_input"],
            "concludes": "summary_output",
        },
    ],
}
```

## `build_spec_nodes`

```python
def build_spec_nodes(mas_spec: dict) -> tuple[list[dict], list[dict]]:
```

`mas_spec` is the design-time specification above. The function returns `(nodes, edges)`.

**`nodes`** is a flat list of dicts. Fields by node type:

| Field | Type | Present on |
|-------|------|-----------|
| `id`, `node_type`, `version`, `content` | str | all nodes |
| `_text` | str | all nodes (same as `content`; used for embedding) |
| `intent`, `role` | str | `AgentSpec` |
| `capabilities`, `skills` | list[str] | `AgentSpec` (`skills` are the ids as given in the spec) |
| `in_schema`, `out_schema` | dict | `ToolSpec` |
| `requires` | list[str] | `SkillSpec` |
| `concludes` | str | `SkillSpec` |

`content` / `_text` is the entry's text:

- `IntentSpec`: the intent string.
- `AgentSpec`: its `intent`, else `role`, else `description`, else `id`.
- `ToolSpec` and `SkillSpec`: the `description`, else the `id`.

The `AgentSpec` node does not carry its `tools` list; the `mayInvoke` edges do.

**`edges`** is a flat list of dicts with `from_id`, `to_id` and `edge_type`:

| `edge_type` | Direction | Meaning |
|-------|-----------|---------|
| `declaresSkill` | `AgentSpec` to `SkillSpec` | Agent declares it can execute the skill |
| `mayInvoke` | `AgentSpec` to `ToolSpec` | Agent declares it may call the tool |

### Example output

For the `mas_spec` above, `nodes, edges = build_spec_nodes(mas_spec)` returns six nodes (abridged here to the first three) and four edges.

```json
[
  {"id": "spec:intent:mas", "node_type": "IntentSpec", "version": "v1.0.0",
   "content": "Resolve customer support tickets using a two-agent pipeline.",
   "_text": "Resolve customer support tickets using a two-agent pipeline."},
  {"id": "spec:agent:triage", "node_type": "AgentSpec", "version": "v1.0.0",
   "role": "Triage Agent",
   "intent": "Classify incoming tickets and route to the right specialist.",
   "capabilities": ["intent_classification", "priority_scoring"],
   "skills": ["summarise"],
   "content": "Classify incoming tickets and route to the right specialist.",
   "_text": "Classify incoming tickets and route to the right specialist."},
  {"id": "spec:tool:submit_resolution", "node_type": "ToolSpec", "version": "v1.0.0",
   "in_schema": {"type": "object", "properties": {"ticket_id": {"type": "string"},
                 "resolution": {"type": "string"}}, "required": ["ticket_id", "resolution"]},
   "out_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
   "content": "submit_resolution", "_text": "submit_resolution"}
]
```

The other nodes are `spec:agent:resolver`, `spec:tool:search_kb` and `spec:skill:summarise`. The edges:

```json
[
  {"from_id": "spec:agent:triage",   "to_id": "spec:skill:summarise",        "edge_type": "declaresSkill"},
  {"from_id": "spec:agent:triage",   "to_id": "spec:tool:search_kb",         "edge_type": "mayInvoke"},
  {"from_id": "spec:agent:resolver", "to_id": "spec:tool:search_kb",         "edge_type": "mayInvoke"},
  {"from_id": "spec:agent:resolver", "to_id": "spec:tool:submit_resolution", "edge_type": "mayInvoke"}
]
```

### Example: intent only

```python
nodes, edges = build_spec_nodes({"intent": "Summarise a document and return key points."})

assert len(nodes) == 1 and edges == []
assert nodes[0]["node_type"] == "IntentSpec"
assert nodes[0]["id"] == "spec:intent:mas"
assert len(nodes[0]["version"]) == 12      # auto-generated fingerprint
```

### Example: two agents sharing a tool

```python
spec = {
    "agents": [
        {"id": "planner", "role": "Planning Agent",
         "intent": "Decompose the user task into sub-tasks.",
         "capabilities": ["task_decomposition"], "skills": [], "tools": ["web_search"]},
        {"id": "executor", "role": "Execution Agent",
         "intent": "Execute sub-tasks produced by the planner.",
         "capabilities": ["code_execution"], "skills": [], "tools": ["web_search", "code_runner"]},
    ],
    "tools": [
        {"id": "web_search",  "in_schema": {"type": "object"}, "out_schema": {"type": "string"}},
        {"id": "code_runner", "in_schema": {"type": "object"}, "out_schema": {"type": "object"}},
    ],
}
nodes, edges = build_spec_nodes(spec)

assert len(nodes) == 4          # 2 AgentSpec + 2 ToolSpec; no IntentSpec (no intent key)
rels = [(e["from_id"], e["edge_type"], e["to_id"]) for e in edges]
assert ("spec:agent:planner",  "mayInvoke", "spec:tool:web_search")  in rels
assert ("spec:agent:executor", "mayInvoke", "spec:tool:web_search")  in rels
assert ("spec:agent:executor", "mayInvoke", "spec:tool:code_runner") in rels
assert len(edges) == 3
```

## `merge_spec_into_kg`

```python
def merge_spec_into_kg(kg_doc: dict, mas_spec: dict) -> dict:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `kg_doc` | `dict` | Existing KG document, modified in place. Missing `"nodes"`, `"edges"` and `"metadata"` keys are created. |
| `mas_spec` | `dict` | Specification, same shape as for `build_spec_nodes`. |

It returns the same `kg_doc` dict. Steps:

1. Call `build_spec_nodes(mas_spec)`.
2. Intern the spec nodes: append each to `kg_doc["nodes"]` only if no node with the same `id` exists. Existing runtime nodes are never overwritten.
3. Add conformance edges (below).
4. Set `kg_doc["metadata"]["spec_injected"] = True`.
5. Set `kg_doc["metadata"]["spec_node_count"]` to the number of nodes `build_spec_nodes` returned, not the number added.

The spec-internal edges from `build_spec_nodes` (`declaresSkill`, `mayInvoke`) are not added to `kg_doc["edges"]`; only conformance edges are. Append the `build_spec_nodes` edges yourself if you want them in the document.

### Conformance edge rules

The function scans every node in `kg_doc["nodes"]`, including pre-existing runtime nodes:

| Runtime node type | Matched field(s) | Edge added | To |
|-------------------|-----------------|-----------|-----|
| `AgentCall`, `LLMCall` | `agentId` or `agent_id` | `instanceOf` | `spec:agent:{id}` |
| `ToolCall` | `toolId`, `tool_id`, `toolName`, or `tool_name` | `invokes` | `spec:tool:{id}` |

- Conformance edges are not deduplicated. Calling `merge_spec_into_kg` twice on one document appends them twice; spec nodes stay interned. Guard with `kg_doc.get("metadata", {}).get("spec_injected")` if a document may be merged more than once.
- `agentId` is matched against the last `:`-separated segment of the spec node id, so spec agent ids that contain `:` do not match.
- A runtime node with no matching spec node gets no edge and raises no error.

### Example: merge one agent call and one tool call

```python
from mas.library.kg import merge_spec_into_kg

kg_doc = {
    "metadata": {},
    "nodes": [
        {"id": "call:agent:run-001", "node_type": "AgentCall", "agentId": "triage",
         "input": "Ticket #42: login fails"},
        {"id": "call:tool:run-001-1", "node_type": "ToolCall", "toolId": "search_kb",
         "input": {"query": "login failure SSO"}},
    ],
    "edges": [
        {"from_id": "call:agent:run-001", "to_id": "call:tool:run-001-1", "edge_type": "hasToolCall"}
    ],
}
mas_spec = {
    "version": "v1.0.0",
    "agents": [{"id": "triage", "role": "Triage Agent", "intent": "Classify tickets.",
                "capabilities": ["intent_classification"], "skills": [], "tools": ["search_kb"]}],
    "tools": [{"id": "search_kb",
               "in_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                             "required": ["query"]},
               "out_schema": {"type": "array", "items": {"type": "string"}}}],
}

result = merge_spec_into_kg(kg_doc, mas_spec)

spec_ids = {n["id"] for n in result["nodes"]}
assert "spec:agent:triage" in spec_ids and "spec:tool:search_kb" in spec_ids

edge_rels = {(e["from_id"], e["edge_type"], e["to_id"]) for e in result["edges"]}
assert ("call:agent:run-001",  "instanceOf", "spec:agent:triage")   in edge_rels
assert ("call:tool:run-001-1", "invokes",    "spec:tool:search_kb") in edge_rels

assert result["metadata"]["spec_injected"] is True
assert result["metadata"]["spec_node_count"] == 2
```

### Example: snake_case fields and re-injection

```python
kg_doc = {
    "metadata": {},
    "nodes": [
        {"id": "c1", "node_type": "ToolCall", "tool_name": "search_kb"},
        {"id": "c2", "node_type": "LLMCall",  "agent_id":  "resolver"},
    ],
    "edges": [],
}
spec = {
    "version": "v2.0.0",
    "agents": [{"id": "resolver", "role": "Resolver", "intent": "Resolve.",
                "capabilities": [], "skills": [], "tools": []}],
    "tools":  [{"id": "search_kb", "in_schema": {}, "out_schema": {}}],
}

merge_spec_into_kg(kg_doc, spec)
nodes_1, edges_1 = len(kg_doc["nodes"]), len(kg_doc["edges"])

merge_spec_into_kg(kg_doc, spec)
assert len(kg_doc["nodes"]) == nodes_1          # spec nodes are interned
assert len(kg_doc["edges"]) == 2 * edges_1      # conformance edges are appended again

edge_rels = {(e["from_id"], e["edge_type"], e["to_id"]) for e in kg_doc["edges"]}
assert ("c1", "invokes",    "spec:tool:search_kb") in edge_rels
assert ("c2", "instanceOf", "spec:agent:resolver") in edge_rels
```

<a id="ascii-diagram"></a>

## Diagram

A single-run KG document after `merge_spec_into_kg`. Spec nodes (left) are shared across runs of the same spec version. Runtime nodes (right) record one execution.

```
SPEC SUB-GRAPH (shared / interned)          RUNTIME SUB-GRAPH (per run)

 IntentSpec  spec:intent:mas                (no conformance edge)

 AgentSpec   spec:agent:triage   <--instanceOf--   AgentCall  call:agent:r42:1
    | mayInvoke                                        | hasToolCall
    v                                                  v
 ToolSpec    spec:tool:search_kb <--invokes------   ToolCall   call:tool:r42:2

 AgentSpec   spec:agent:resolver <--instanceOf--   LLMCall    llm:r42:3
    | mayInvoke
    v
 ToolSpec    spec:tool:submit_resolution <--invokes-- ToolCall call:tool:r42:4

 SkillSpec   spec:skill:summarise           (no runtime match in this run)
```

- `instanceOf`: conformance edge from a runtime `AgentCall` / `LLMCall` to an `AgentSpec`.
- `invokes`: conformance edge from a runtime `ToolCall` to a `ToolSpec`.
- `mayInvoke`: declaration edge inside the spec sub-graph. `build_spec_nodes` returns it; `merge_spec_into_kg` does not add it.
- `hasToolCall`: ordinary runtime containment edge already in the KG.

## Stable IDs

| Node type | ID pattern |
|-----------|-----------|
| `IntentSpec` | `spec:intent:mas` (singleton per KG) |
| `AgentSpec` | `spec:agent:{agent["id"]}` |
| `ToolSpec` | `spec:tool:{tool["id"]}` |
| `SkillSpec` | `spec:skill:{skill["id"]}` |

IDs stay stable while the `"id"` values in the spec entries stay constant. The `version` field changes with content; the node `id` does not. Embeddings and cross-document references therefore survive version bumps.

Stable IDs give you:

- **Deduplication**: `merge_spec_into_kg` interns with an `id`-set membership check, with no hashing of node content.
- **Embedding reuse**: a vector store keyed by node `id` needs re-embedding only for spec nodes whose `version` changed.
- **Cross-run lookup**: for any `AgentCall.agentId = "triage"`, the spec node is at `spec:agent:triage`, with no join table.
- **Traversal entry points**: tools can start at `spec:intent:mas` or `spec:agent:<id>` and follow `mayInvoke` edges (once you have appended the `build_spec_nodes` edges).

## Extending the spec

### Custom fields

The spec dict accepts extra keys at any level. `build_spec_nodes` ignores them; they stay in your spec dict.

```python
spec = {
    "version": "v1.1.0",
    "agents": [{
        "id": "researcher", "role": "Research Agent",
        "intent": "Gather evidence from external sources.",
        "capabilities": ["web_search", "pdf_parse"], "skills": [], "tools": ["browser"],
        "owner": "platform-team", "sla_ms": 3000,       # custom: not copied to KG nodes
    }],
    "tools": [{"id": "browser", "in_schema": {"type": "object"}, "out_schema": {"type": "string"}}],
}
```

To store custom fields in KG nodes, post-process the returned list:

```python
nodes, edges = build_spec_nodes(spec)
id_to_extra = {
    f"spec:agent:{a['id']}": {k: a[k] for k in a if k not in
        ("id", "role", "intent", "capabilities", "skills", "tools")}
    for a in spec.get("agents", [])
}
for node in nodes:
    if node["id"] in id_to_extra:
        node.update(id_to_extra[node["id"]])
```

### Runtime nodes with no spec match

`merge_spec_into_kg` skips conformance edges for runtime nodes that match no spec entry, without a warning. Runtime graphs can contain ad-hoc calls from debugging or experiments. The missing edge is itself a signal:

```python
with_invokes = {e["from_id"] for e in kg_doc["edges"] if e["edge_type"] == "invokes"}
undeclared_calls = [
    n for n in kg_doc["nodes"]
    if n["node_type"] == "ToolCall" and n["id"] not in with_invokes
]
```

### `goal` instead of `intent`

`"goal"` behaves like `"intent"`; when both are present, `"intent"` wins.

```python
nodes, _ = build_spec_nodes({"goal": "Classify and route support tickets."})
assert nodes[0]["node_type"] == "IntentSpec"
assert nodes[0]["content"] == "Classify and route support tickets."
```

### Auto-generated version fingerprint

Without `"version"`, the module uses a 12-character hex fingerprint of `repr(spec)`:

```python
import hashlib

spec = {"intent": "Do something useful."}
fingerprint = hashlib.sha256(repr(spec).encode()).hexdigest()[:12]
nodes, _ = build_spec_nodes(spec)
assert nodes[0]["version"] == fingerprint
```

The fingerprint changes when any part of the spec changes. `repr` output can differ across Python versions or platforms for floats and complex objects, so supply an explicit `"version"` if you need stability across them.

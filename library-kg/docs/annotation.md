# KG annotation

`annotate_kg_nodes` in `mas.library.kg` attaches computed data (an embedding, a score, a label) to nodes of a knowledge graph (KG) without changing the original document. Use it to enrich a KG from `mas-lab kg normalize` before querying it or pushing it to Neo4j.

```python
import json
from mas.library.kg import annotate_kg_nodes

doc = json.load(open("kg.jsonld"))
enriched = annotate_kg_nodes(
    doc,
    node_type="LLMCall",
    edge_name="hasScore",
    value_fn=lambda node: {"score": 1.0} if node.get("completion") else None,
)
```

**Contents:** [Concept](#concept) · [API reference](#api-reference) · [Stable IDs](#stable-ids-and-idempotency) · [Examples](#examples) · [Neo4j](#pushing-annotations-to-neo4j) · [Custom types and prefixes](#custom-annotation_node_type-and-id_prefix) · [Built-in State embeddings](#built-in-state-embeddings)

## Concept

A KG document (`kg.jsonld`, loaded as a dict) has two top-level lists:

```python
{
    "nodes": [...],   # each with "id" and "node_type"
    "edges": [...],   # each with "from_id", "to_id", "edge_type"
    # other top-level keys ("@context", "metadata") are preserved as-is
}
```

Annotation attaches a computed value to existing nodes. The result is a new document with the original nodes and edges plus annotation nodes and the edges linking them to their sources.

- **Immutability.** The input `doc` is never modified. When at least one annotation is added, `annotate_kg_nodes` returns a new top-level dict. The `nodes` and `edges` lists are new, but node dicts are shared with the input, so do not mutate them. You can pass the original to several annotation calls.
- **Idempotency.** Annotation node IDs derive only from the source node's `id` and the `edge_name` (SHA-256). Re-running an annotation on the same original document gives identical IDs, so a Neo4j `MERGE` creates no duplicates. The function does not check for existing IDs: annotating a document that already holds the annotations adds a second copy. Annotate the original, not an enriched document.
- **Separation of concerns.** `annotate_kg_nodes` owns graph structure (nodes, edges, IDs). You own the computation (an embedding API, a scorer, a regex). The boundary is the `value_fn` callable.

Typical uses:

| Use case | `node_type` | `edge_name` | Annotation fields |
|---|---|---|---|
| Semantic embeddings | `State` | `hasEmbedding` | `vector`, `model`, `dimensions` |
| Response quality score | `LLMCall` | `hasScore` | `score`, `scorer`, `rationale` |
| Tool output classification | `ToolCall` | `hasLabel` | `label`, `confidence` |
| Toxicity / sentiment | `LLMCall` | `hasToxicity` | `toxicity`, `sentiment` |
| Length bucketing | `LLMCall` | `hasLengthBucket` | `bucket`, `char_count` |
| Custom metrics | any | any valid identifier | arbitrary fields |

## API reference

### `annotate_kg_nodes`

```python
from mas.library.kg import annotate_kg_nodes
# or
from mas.library.kg.core.annotate import annotate_kg_nodes
```

```
annotate_kg_nodes(
    doc,
    *,
    node_type,
    edge_name,
    value_fn,
    annotation_node_type="Annotation",
    id_prefix="urn:mas:annotation:",
) -> dict
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `doc` | `dict` | yes | KG document: `{"nodes": [...], "edges": [...], ...}` |
| `node_type` | `str` | yes | Process only nodes where `node["node_type"] == node_type` (keyword-only) |
| `edge_name` | `str` | yes | `edge_type` of the new edges, for example `"hasEmbedding"` (keyword-only) |
| `value_fn` | `Callable[[dict], dict \| None]` | yes | Called once per matching node. Returns a dict of fields for the annotation node, or `None` to skip the node. Returned keys are merged last and override `id`, `node_type` and `source_node_id` if they reuse those names; avoid that. |
| `annotation_node_type` | `str` | no | `node_type` of each new node. Default `"Annotation"` |
| `id_prefix` | `str` | no | Prefix of generated annotation IDs. Default `"urn:mas:annotation:"` |

**Returns** a new `dict` shaped like `doc`, with the annotation nodes and edges appended to `"nodes"` and `"edges"`. Other top-level keys (`"@context"`, `"metadata"`) are preserved. If `value_fn` returns `None` for every node, or no node matches, the original `doc` object is returned unchanged (not a copy).

**Raises** `TypeError` if `value_fn` returns anything other than `dict` or `None`.

Each annotation node has these fields, plus everything `value_fn` returned:

```python
{
    "id":             "<id_prefix><16-hex-chars>",
    "node_type":      annotation_node_type,
    "source_node_id": "<id of the source node>",
}
```

Each edge:

```python
{
    "from_id":   "<source node id>",
    "to_id":     "<annotation node id>",
    "source":    "<source node id>",       # duplicate of from_id
    "target":    "<annotation node id>",   # duplicate of to_id
    "edge_type": edge_name,
}
```

`source` and `target` duplicate `from_id` and `to_id` for readers that use the older edge key names (see the [edge format note](kg-query.md#edge-format-note)).

### `run_annotate` (step wrapper)

`run_annotate` loads a `*.jsonld` file (or takes a `KGArtifact`), calls `annotate_kg_nodes`, and optionally writes the result. See also [steps.md](steps.md#run_annotate).

```python
from mas.library.kg.steps.annotate import run_annotate

enriched = run_annotate(
    "kg.jsonld",        # KGArtifact, str or Path
    value_fn,
    node_type="State",
    edge_name="hasEmbedding",
    annotation_node_type="Annotation",
    output_path=None,   # None + path input: overwrite the input file
    dry_run=False,      # True: compute only, write nothing
)
# enriched is a KGArtifact; enriched.node_count includes the annotation nodes
```

It returns the enriched `KGArtifact`, not a dict. It has no `id_prefix` parameter (the default prefix is used). Paths must end in `.jsonld`. A `KGArtifact` input with no `output_path` writes nothing.

## Stable IDs and idempotency

`mas.library.kg.core.annotate` computes annotation IDs like this:

```python
import hashlib, json

content_hash = hashlib.sha256(
    json.dumps({"source": source_id, "edge": edge_name}, sort_keys=True).encode()
).hexdigest()[:16]

annotation_id = f"{id_prefix}{content_hash}"
```

The inputs are the source node's `id` and the `edge_name`. The `value_fn` output does not affect the ID. Consequences:

1. Running the same annotation twice on one document gives the same IDs. A second push with `MERGE` updates properties instead of creating duplicates.
2. Different `edge_name` values give different IDs for one source node, so `hasEmbedding`, `hasScore` and `hasLabel` annotations do not collide.
3. `id_prefix` is part of the ID string but is not hashed. Changing it changes the ID and breaks idempotency. Keep it stable for the lifetime of the dataset.
4. A source node without an `"id"` gets `uuid.uuid4()`, which is not deterministic. Make sure source nodes carry an `"id"`.

## Examples

### Example 1: embed State nodes

Each `State` node is a snapshot of the agent's context at one point in the run. Embedding its `content` enables semantic search and trajectory clustering. The example needs the `openai` package (not a dependency of `mas.library.kg`); the OpenAI client reads its credentials from its default configuration.

```python
import json
from pathlib import Path

import openai
from mas.library.kg import annotate_kg_nodes

client = openai.OpenAI()


def embed_fn(node: dict) -> dict | None:
    """Embed the content field; skip nodes with no text."""
    content = (node.get("content") or "").strip()
    if not content:
        return None
    resp = client.embeddings.create(model="text-embedding-3-small", input=[content])
    vector = resp.data[0].embedding
    return {"vector": vector, "model": "text-embedding-3-small", "dimensions": len(vector)}


kg_path = Path("output/session-abc/kg.jsonld")
doc = json.loads(kg_path.read_text())

enriched = annotate_kg_nodes(
    doc,
    node_type="State",
    edge_name="hasEmbedding",
    value_fn=embed_fn,
    annotation_node_type="StateEmbedding",
    id_prefix="urn:mas:emb:",
)
kg_path.with_name("kg.annotated.jsonld").write_text(json.dumps(enriched, indent=2))
print(f"Added {len(enriched['nodes']) - len(doc['nodes'])} StateEmbedding nodes")
```

Each new node looks like:

```json
{
  "id": "urn:mas:emb:3f8a1c9e72b04d51",
  "node_type": "StateEmbedding",
  "source_node_id": "state-agent-agent-u1-exec-initial",
  "vector": [0.023, -0.114, ...],
  "model": "text-embedding-3-small",
  "dimensions": 1536
}
```

For large sessions, batch the API calls: embed all contents first, then look vectors up in `value_fn`.

```python
state_nodes = [n for n in doc["nodes"] if n.get("node_type") == "State"]
non_empty = [(n["id"], (n.get("content") or "").strip()) for n in state_nodes]
non_empty = [(nid, c) for nid, c in non_empty if c]

vector_map = {}
if non_empty:
    resp = client.embeddings.create(model="text-embedding-3-small",
                                    input=[c for _, c in non_empty])
    vector_map = {nid: resp.data[i].embedding for i, (nid, _) in enumerate(non_empty)}


def embed_fn(node: dict) -> dict | None:
    vec = vector_map.get(node.get("id") or "")
    if vec is None:
        return None
    return {"vector": vec, "model": "text-embedding-3-small", "dimensions": len(vec)}
```

### Example 2: score LLMCall nodes

Generated `LLMCall` nodes store the model output in `completion` and the input in `prompt`. Returning `None` skips a node: no annotation node and no edge are created, and the totals grow only by the number of non-`None` returns. Replace the scoring block with your own logic.

```python
import re
from mas.library.kg import annotate_kg_nodes


def score_llm_call(node: dict) -> dict | None:
    output = (node.get("completion") or "").strip()
    if not output:
        return None  # no response to score (for example an errored call)

    word_count = len(output.split())
    has_reasoning = bool(re.search(r"\bbecause\b|\btherefore\b|\bthus\b", output, re.I))
    score = min(1.0, word_count / 200.0)
    if has_reasoning:
        score = min(1.0, score + 0.15)

    return {"score": round(score, 4), "scorer": "heuristic-v1",
            "word_count": word_count, "has_reasoning": has_reasoning}


enriched = annotate_kg_nodes(
    doc,
    node_type="LLMCall",
    edge_name="hasScore",
    value_fn=score_llm_call,
    annotation_node_type="QualityScore",
    id_prefix="urn:mas:score:",
)
```

Result:

```json
{
  "id": "urn:mas:score:a17f3b8c9d012e44",
  "node_type": "QualityScore",
  "source_node_id": "llmcall-007",
  "score": 0.8700,
  "scorer": "heuristic-v1",
  "word_count": 154,
  "has_reasoning": true
}
```

### Example 3: annotate without a model

`value_fn` can be plain Python. This one buckets each `LLMCall` by response length and skips `ToolCall` nodes with no output in the same way (return `None`).

```python
def length_bucket(node: dict) -> dict | None:
    char_count = len((node.get("completion") or "").strip())
    if char_count == 0:
        return None
    for limit, bucket in ((100, "xs"), (500, "sm"), (2000, "md"), (8000, "lg")):
        if char_count < limit:
            return {"bucket": bucket, "char_count": char_count}
    return {"bucket": "xl", "char_count": char_count}


enriched = annotate_kg_nodes(
    doc,
    node_type="LLMCall",
    edge_name="hasLengthBucket",
    value_fn=length_bucket,
    annotation_node_type="LengthBucket",
    id_prefix="urn:mas:len:",
)
```

Computed annotations are cheap to regenerate and work as pre-filters before expensive model calls, for example skipping the `xs` bucket before embedding. For a classifier, read `toolOutput` on `ToolCall` nodes the same way and return `{"label": ..., "confidence": ...}`.

## Pushing annotations to Neo4j

`push_kg_to_neo4j` assumes a self-contained session graph. Before writing, it runs a denormalize step that stamps every node and edge with `sessionId`, `appName` and `source` (nodes also get `block`); see [steps.md](steps.md#run_neo4j_push).

Annotation edges can point at nodes already in Neo4j that are absent from the annotation document, so stamping a session onto them would mislead. `push_annotations_to_neo4j` calls `push_kg_to_neo4j` with `skip_denormalize=True` and uses `MERGE`:

- Annotation nodes are upserted, so re-runs are idempotent.
- Edges `MERGE` both endpoints. A referenced source node that is not yet in Neo4j is created as a minimal stub (`id` and label only) and filled in if you push it later. Push the session graph first to avoid stubs.

```python
import json
from pathlib import Path

from mas.library.kg import annotate_kg_nodes
from mas.library.kg.neo4j.push import push_annotations_to_neo4j

doc = json.loads(Path("output/session-abc/kg.jsonld").read_text())


def embed_fn(node: dict) -> dict | None:
    content = (node.get("content") or "").strip()
    if not content:
        return None
    # ... call your embedding API here ...
    return {"vector": [0.0] * 1536, "model": "text-embedding-3-small", "dimensions": 1536}


annotations_doc = annotate_kg_nodes(
    doc, node_type="State", edge_name="hasEmbedding",
    value_fn=embed_fn, annotation_node_type="StateEmbedding", id_prefix="urn:mas:emb:",
)

# The full enriched doc is accepted: original session nodes are MERGEd too
# (unchanged, since denormalization is skipped).
result = push_annotations_to_neo4j(
    annotations_doc,
    uri="bolt://localhost:7687",
    username="neo4j",
    password="<your-neo4j-password>",
    database="neo4j",
)
print(result)
# {"nodes_pushed": ..., "edges_pushed": ..., "dry_run": False, "rows": ...}
# The counts cover every node and edge in the pushed document, not only annotations.
```

Pass `dry_run=True` to write nothing and make no Neo4j connection. `push_annotations_to_neo4j` returns only `{nodes_pushed, edges_pushed, dry_run, rows}` and logs the Cypher statements (`logger.info`) instead of returning them. To get them as strings, call `push_kg_to_neo4j(..., skip_denormalize=True, dry_run=True)` and read `result["statements"]`.

Because IDs are deterministic, pushing twice with the same inputs is safe: `MERGE` finds each annotation node by `id` and overwrites its properties. You can re-run an annotation pipeline after changing the model or scorer.

## Custom `annotation_node_type` and `id_prefix`

**`annotation_node_type`** sets the `node_type` of each new node and becomes the Neo4j label on push.

- Use `PascalCase`, like the other KG node types.
- Be specific: `StateEmbedding` is better than `Annotation` when all annotations embed State nodes. Use the default `"Annotation"` only for ad-hoc or mixed documents you will not query by annotation type.
- Do not reuse one type for different annotations (for example `"LLMAnnotation"` for both a score and a label).
- Examples: `StateEmbedding`, `QualityScore`, `ToolCallLabel`, `SafetyAnnotation`, `LengthBucket`.

**`id_prefix`** is prepended to the 16-character hash.

- Use URN-style prefixes to avoid collisions: `"urn:mas:emb:"`, `"urn:mas:score:"`, `"urn:mas:label:"`.
- Add a version token if you may change the hash inputs: `"urn:mas:emb:v2:"`.
- Keep it stable. Changing it changes all IDs, so Neo4j `MERGE` creates new nodes instead of updating existing ones.
- The prefix is stored in `id`, so it works as a namespace when debugging: `MATCH (n) WHERE n.id STARTS WITH 'urn:mas:emb:' RETURN n`.

## Built-in State embeddings

For `State` nodes, `mas.library.kg.embeddings.state_embeddings` ships helpers that do not use `annotate_kg_nodes`:

```python
from mas.library.kg.embeddings.state_embeddings import (
    attach_embeddings_to_kg,
    default_openai_embed_fn,
)

# embed_fn: Sequence[str] -> list[list[float] | None], one vector per text
embed_fn = default_openai_embed_fn(model="text-embedding-3-small", api_key="<your-api-key>")
enriched = attach_embeddings_to_kg(doc, embed_fn=embed_fn, model="text-embedding-3-small")
```

Differences from `annotate_kg_nodes`:

- Only `State` nodes with non-empty `content` are embedded (text capped at 4096 characters), in batches of `batch_size` (default 64).
- New nodes have `node_type="VectorEmbedding"` and ids `emb-<16 hex>` derived from the state id and model. They carry `stateNodeId`, `model`, `dim`, `vector`, `computedAt`, `contentHash`, `runId`. The edge is `hasEmbedding` (State to VectorEmbedding).
- `skip_existing=True` (default) skips states that already have a `hasEmbedding` edge.
- `metadata["embeddings"]` records `{model, state_count, attached}`.
- A batch whose API call fails yields no vectors for its states; the failure is logged, not raised.

Use `annotate_kg_nodes` for another node type, edge name or field layout. `default_openai_embed_fn` needs the `openai` package.

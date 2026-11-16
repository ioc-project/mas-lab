# MAS knowledge graph ontology

The ontology defines the node classes, edge types, attributes and constraints of the knowledge graph (KG) that `mas.library.kg` builds from a multi-agent system (MAS) run. It comes from the SCOPE `ioc-scope-ontology` package plus a MAS Lab extension file. Read it when you write queries, validate a KG, or add event kinds. Pipeline mechanics are in [normalization.md](normalization.md).

Smallest example: build a KG, then check it against the ontology.

```bash
mas-lab kg normalize library-kg/examples/qa-agent/events.jsonl --run-id demo -o kg.jsonld
mas-lab kg validate kg.jsonld
```

See also: [normalization.md](normalization.md), [steps.md](steps.md), [neo4j.md](neo4j.md), [kg-spec.md](kg-spec.md).

## Table of contents

1. [Overview](#1-overview)
2. [Layer overview](#2-layer-overview)
3. [Common attributes](#3-common-attributes)
4. [Node ID construction](#4-node-id-construction)
5. [Core layer](#5-core-layer) (5.1 catalog nodes, 5.2 attribute tables, 5.3 edges, 5.4 constraints, 5.5 example)
6. [Trajectory layer](#6-trajectory-layer) (6.1 semantics, 6.2 attributes, 6.3 edges, 6.4 constraints, 6.5 example)
7. [Provenance layer](#7-provenance-layer) (7.1 semantics, 7.2 attributes, 7.3 constraints)
8. [Infrastructure layer](#8-infrastructure-layer)
9. [Governance layer](#9-governance-layer) (9.1 classes, 9.2 attributes, 9.3 kinds, 9.4 edges and constraints)
10. [Global constraints](#10-global-constraints)
11. [URN scope conventions](#11-urn-scope-conventions)
12. [Verification](#12-verification)

## 1. Overview

### What the ontology is

The KG represents a MAS run as a property graph: typed nodes (what ran, what it said, what state it was in) and typed directed edges (who contained whom, in what order). An ontology is the schema of those node and edge types. Normalization ([normalization.md](normalization.md)) turns a run's `events.jsonl` into this graph, stored as a `kg.jsonld` document or projected into Neo4j ([neo4j.md](neo4j.md)).

### Where it lives

The vocabulary comes from `ioc-scope-ontology` (SCOPE, a required dependency), in the `https://ioc-project.github.io/scope-ontology/mas#` namespace: `mas-ontology.ttl` (classes and properties), `mas-shapes.ttl` and `mas-shapes-custom.ttl` (SHACL shapes, the W3C constraint language for RDF graphs), and the generated Python models `scope_ontology.models.*`. The native fold emits exactly that vocabulary: every node type is a SCOPE class and every edge type a SCOPE property.

`mas.library.kg.ontology` resolves the TTL paths, and adds `ontology/extensions/mas-kg-native-extensions.ttl` for the classes an installed SCOPE older than 1.2 does not declare yet. The validation step ([section 12](#12-verification)) loads them into one RDF graph.

### Class hierarchy

```
Element
|-- StructuralElement   (types: what the MAS is made of)
|     MAS, Agent, Worker, Capability -> LLM, Tool, Processing, Skill, Memory
|-- ExecutionElement    (instances: what actually ran; one *Call per trace unit)
|     Session, MASCall, AgentCall, ThinkingCall, CapabilityCall
|       CapabilityCall -> LLMCall, ToolCall, ProcessingCall, SkillCall, MemoryCall -> RAGQuery
|-- TrajectoryElement   (content flow between steps)
|     State, Transition
|-- CallAnnotation      (point-in-time event; direct subclass of Element)
|     GovernanceEvent, ContextContribution
```

MAS Lab has no `TaskCall`, `ParallelGroup`, `Branch`, `Run` or `Application`: a task is a `MASCall` (`mas_call_*`), a parallel fan-out is expressed by the trajectory (one Transition per member reading the same State, joined by an `aggregate` ProcessingCall), and a run or application is not a SCOPE class. `ThinkingCall` is declared by SCOPE but the fold emits it only from a real thinking start/end event pair; the runtime emits none, so no `ThinkingCall` is produced.

### How it is used

| Purpose | How it helps |
|---|---|
| Normalization target | One schema for traces from any framework or runtime. |
| Query contract | KG compare, query and annotation steps use the attribute and edge names defined here. |
| Validation | SHACL shapes and structural checks run against the vocabulary (section 12). |

### Design goals

- **Core by default**: the default output holds the core classes; you add opt-in categories per run (section 2).
- **Stable ids**: ids are deterministic functions of the trace (section 4), so re-normalizing creates no duplicates.
- **Execution versus structure**: structural (catalog) nodes describe types (`Tool`, `LLM`, ...); execution nodes describe instances and point at their catalog node through `executes*` edges.

## 2. Layer overview

The default output is the core layer. Opt-in categories (option `categories`; see [normalization.md](normalization.md#8-categories-and-control)) add classes on top and never change a core node. The tags `L0` to `L5` are labels, not an ordering, and no layer uses L3.

| Layer | Tag | Default | Events | Node classes |
|---|---|---|---|---|
| Core | L0 | always on | `tool_call`, `network_call`, `llm_call`, `execution`, `mas_call`, `processing_call`, `workflow_transition` pairs | `Session`, `MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, catalog nodes |
| Capabilities | | **off** (`categories=capabilities`) | `memory_*`, `rag_query_*`, `skill_execution_*` pairs | `MemoryCall`, `RAGQuery`, `SkillCall`, `Memory`, `Skill` |
| Infrastructure | L1 | **off** (`categories=worker`) | `infrastructure_info` | `Worker` |
| Thinking | | **off** (`categories=thinking`) | `thinking_start` / `thinking_end` | `ThinkingCall` |
| Annotation | L2 | **off** (`categories=annotations`) | `routing*`, `context_assembled`, `state_update_*`, `observability_*_execute_*`, `user_input`, `client_response`, `tool_result_injected`, `wait_state_*`, ... | `CallAnnotation` |
| Provenance | L4 | **off** (`categories=annotations`) | `context_part_contributed` | `ContextContribution` |
| Governance | L5 | **off** (`categories=annotations`) | `audit`, `policy_*`, `budget_event`, `transformation_event`, `control_intervention`, `hitl_*`, `governance_decision`, `governance_event`, and the outcome a check interval states | `GovernanceEvent` |

`State` and `Transition` nodes are not tied to any category: they are derived from every core call that has content. `parallel_group_*` markers map to nothing. Default KG attributes are the installed SCOPE model fields (`all_attributes=False`). `callsAgent` is not emitted until A2A observability lands.

**Defaults.** Provenance adds about one node per context part (often hundreds per session). Governance has content only when a policy engine is connected.

## 3. Common attributes

Attribute names are camelCase in the graph. The ontology declares these datatype properties:

| Applies to | Attribute | Type | Notes |
|---|---|---|---|
| every `Element` | `id` | string | required (SHACL violation if missing) |
| every `Element` | `name` | string | required (warning) |
| `StructuralElement` | `declared` | boolean | `false` for catalog nodes inferred from a trace |
| `ExecutionElement` | `startTime`, `endTime` | number | epoch seconds in native output; required |
| `ExecutionElement` | `duration` | number | milliseconds; required |
| `ExecutionElement` | `spanId`, `parentSpanId`, `success` | string, string, boolean | optional (OTel linkage) |
| `ExecutionElement`, `TrajectoryElement` | `sessionId` | string | required on trajectory elements |
| `LLM` | `provider` | string | required (warning) |
| `LLMCall` | `promptTokenCount`, `completionTokenCount`, `totalTokenCount`, `cacheReadTokenCount`, `finishReason`, `responseId`, `temperature` | | required (warnings) |
| `State` | `content` | string | required |

The native builder adds identity and linkage attributes to execution nodes: `callId`, `executionId` (`exec-{Class}-{callId}`), `agentId`, `parentCallId` (empty for roots), `runId`, `kindBase`, `masUri`, `spanLevel`, `status` and `sourceRecordIds`. Default output (`all_attributes=False`) keeps only SCOPE model fields. The full per-class list is in [normalization.md](normalization.md#6-node-attributes-reference).

### Fields added at Neo4j push

`denormalize` copies three session-scoped fields onto every node and edge, and fills in `block` when missing:

| Field | Source |
|---|---|
| `sessionId` | the `Session` node (falls back to a `run_id`) |
| `appName` | the `app_name` argument, else inferred from the session id |
| `source` | defaults to `mas-lab`; overridable per push |

These are not part of the ontology but support the Neo4j index strategy ([neo4j.md](neo4j.md)).

## 4. Node ID construction

Node ids must be stable across re-normalization so that `MERGE` in Neo4j never duplicates nodes. Patterns (full table and examples in [normalization.md](normalization.md#9-node-id-stability)):

| Node type | ID pattern |
|---|---|
| Call nodes (`MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, ...) | `{type lowercased}:{run slug}:{agent slug}:{callId}` |
| `CallAnnotation`, `GovernanceEvent` | `ann-{16 hex}` derived from run id, agent id, event kind and timestamp |
| `ContextContribution` | `cpr-{part_id}` |
| `State` | `state:{run_id}:{state id the runtime wrote}` |
| `Transition` | `trans:{element node id}` |
| `Session` | `session-{run_id}` (or the session UUID or hierarchical run id; see normalization.md) |
| `Agent` | the agent id |
| `Tool`, `Skill`, `Processing`, `LLM` catalog | `catalog:tool:{name}`, `catalog:skill:{name}`, `catalog:processing:{name}`, `catalog:model:{name}` |
| `MAS` | `catalog:mas:{mas_id or app_name}` |

### Spec nodes (separate from catalog nodes)

`merge_spec_into_kg` ([kg-spec.md](kg-spec.md)) adds *declared* design-time nodes with ids `spec:{kind}:{key}` (`spec:agent:planner`, `spec:tool:search_kb`, `spec:skill:...`), of types `IntentSpec`, `AgentSpec`, `ToolSpec`, `SkillSpec`, and conformance edges (`instanceOf`, `invokes`, `declaresSkill`, `mayInvoke`). These are not in the ontology TTLs and are distinct from the `catalog:` nodes derived from a trace.

## 5. Core layer

The core layer is always on. It defines what the system is made of (catalog nodes) and what actually executed (execution nodes).

### 5.1 Structural catalog nodes

Catalog nodes are `StructuralElement` instances. They are created from the trace (not declared by the user), so `declared = false`.

| Node type | Identity | Description |
|---|---|---|
| `MAS` | `name` (app name) | The multi-agent system. One per trace; target of `Session` and `MASCall` `executes*` edges. |
| `Agent` | `agentId` | An agent role. One per agent id, not per invocation. |
| `Capability` | | Abstract parent of `LLM`, `Tool`, `Processing`. |
| `LLM` | `name` (model), `provider` | A language model used by `LLMCall`s. |
| `Tool` | `name` | A tool invoked by `ToolCall`s. |
| `Processing` | `name` | A named internal processing step. |
| `Skill` | `name` | A skill executed by `SkillCall`s. |
| `Memory` | `name`, `memoryType` | A store read or written by `MemoryCall`s and `RAGQuery`s; named by `memory_name`, else `collection`, else `memory_type`. |
| `Worker` | `name`, `processId`, `durableBackend` | A process an execution ran on (`infrastructure_info`). |

`Tool`, `LLM`, `Skill` and `Processing` nodes also carry `callCount`, the number of calls referencing them.

### 5.2 Execution node attribute tables

Every execution node carries the common attributes of [section 3](#3-common-attributes). Class-specific attributes (emitted by the native path):

| Class | Attributes |
|---|---|
| `Session` | `sessionId`, `runId`, `appName`, `inputQuery`, `finalResponse` |
| `MASCall` | `masName`, `masType` |
| `AgentCall` | `agentName`, `agentType`, `agentSequence`, `inputContent`, `outputRaw` |
| `LLMCall` | `llmName`, `modelName`, `provider`, `prompt`, `completion`, `toolCalls`, token counts, `finishReason`, `responseId`, `temperature` |
| `ToolCall` | `toolName`, `toolCallId`, `toolArguments` (JSON string), `toolOutput`, optional `barrierId` |
| `ProcessingCall` | `processingName`, `processingType`, `inputContent`, `processingOutput` |
| `SkillCall` | common attributes only; the skill is the `Skill` it `executesSkill` |
| `MemoryCall`, `RAGQuery` | `memoryOperation` (`store` or `retrieve`), `memoryQuery`, `resultCount`; `RAGQuery` also `topK` |

Source events and fallbacks are in [normalization.md](normalization.md#6-node-attributes-reference). Failure categories and similar details are not modelled; they stay on the source events.

### 5.3 Core edges

Edge names are the ontology's. Domain and range are from `mas-ontology.ttl`.

| Edge | From -> To | Meaning |
|---|---|---|
| `hasMASCall` | `Session` -> `MASCall` | session's top-level call |
| `hasAgentCall` | `MASCall` -> `AgentCall` | MAS call spawns agent calls (the native builder also emits it `AgentCall` -> `AgentCall` for time-enclosed delegations) |
| `belongsToMASCall` | `AgentCall` -> `MASCall` | inverse of `hasAgentCall` |
| `hasLLMCall`, `hasToolCall` | `AgentCall` -> `LLMCall` / `ToolCall` | containment |
| `hasProcessingCall` | `AgentCall` or `MASCall` -> `ProcessingCall` | containment (shortcut) |
| `hasMemoryCall`, `hasSkillCall` | `AgentCall` -> `MemoryCall` or `RAGQuery` / `SkillCall` | containment (a `RAGQuery` is a `MemoryCall`) |
| `executes` | `ExecutionElement` -> `StructuralElement` | super-property of every `executes*` edge; the fold emits only the specific edge and SHACL reads `executes` through RDFS inference |
| `executesSession` | `Session` -> `MAS` | |
| `executesMAS`, `executesAgent`, `executesLLM`, `executesTool`, `executesProcessing`, `executesSkill`, `executesMemory` | call -> `MAS` / `Agent` / `LLM` / `Tool` / `Processing` / `Skill` / `Memory` | typing link; the last five also check the target class |
| `belongsToMAS` | `Agent` -> `MAS` | agent membership |
| `usesLLM`, `usesTool` | `Agent` -> `LLM` / `Tool` | from an `llm_call_start` / `tool_call_*` record that names the agent and the model or tool |
| `usesCapability`, `usesProcessing` | `Agent` -> capability | declared in SCOPE; not emitted |
| `runsOn` | `ExecutionElement` -> `Worker` | from the `infrastructure_info` record under that execution |
| `annotates` | `CallAnnotation` (and subclasses) -> call | the call the record names |

Containment is what `parent_call_id` says: the AgentCall it names contains the call, through the specific `has*Call` property (all sub-properties of `containsCall`, which the fold does not emit). Nothing is derived from time ([normalization.md](normalization.md#5-synthesised-nodes-and-edges)).

### 5.4 Core constraints

From the SHACL property shapes in `mas-ontology.ttl` (violation unless noted):

| Constraint | Applies to |
|---|---|
| `id` present (violation); `name` present (warning) | every `Element` |
| `startTime`, `endTime`, `duration` present | every `ExecutionElement` |
| at least one `executes*` edge (`executes` by RDFS inference) | every `ExecutionElement` |
| `hasMASCall` present | every `Session` |
| `executesSession` present | every `Session` |
| `executesMAS` / `executesAgent` present | `MASCall` / `AgentCall` |
| `executesLLM` / `executesTool` / `executesProcessing`, range `LLM` / `Tool` / `Processing` | `LLMCall` / `ToolCall` / `ProcessingCall` |
| `belongsToMASCall` present | every `AgentCall` |
| `belongsToMAS` present | every `Agent` |
| `hasLLMCall`, `hasToolCall` on `AgentCall`; `hasAgentCall` on `MASCall` (all warnings) | `AgentCall`, `MASCall` |
| `provider` (warning) | `LLM` |
| token counts, `finishReason`, `responseId`, `temperature` (warning) | `LLMCall` |
| `ExecutionElement` must not be instantiated directly (use a concrete subclass) | all |

The native fold and `norm` both emit the `executes*`, `belongsTo*` and topology edges and the `name`, `duration`, `declared`, `provider` and `LLMCall` attributes, so that these shapes can be satisfied. `mas.library.kg` does not align the graph afterwards.

### 5.5 Core layer example

A trimmed, illustrative excerpt of the output shape for a `mas-lab kg normalize ... --run-id demo` run (selected attributes only, ids shortened):

```json
{
  "nodes": [
    {"node_type": "Session", "id": "session-demo", "startTime": 1716200000.0, "endTime": 1716200010.0},
    {"node_type": "MASCall", "id": "mascall:demo", "status": "success"},
    {"node_type": "AgentCall", "id": "agentcall:demo:tp-exec-001",
     "agentId": "planner", "parentCallId": "tp-mas-001"},
    {"node_type": "LLMCall", "id": "llmcall:demo:tp-llm-001",
     "agentId": "planner", "parentCallId": "tp-exec-001", "modelName": "gemini-2.5-flash"},
    {"node_type": "Agent", "id": "planner", "agentId": "planner"},
    {"node_type": "LLM", "id": "catalog:model:gemini-2.5-flash", "name": "gemini-2.5-flash"},
    {"node_type": "MAS", "id": "catalog:mas:trip-planner"}
  ],
  "edges": [
    {"edge_type": "hasMASCall", "from_id": "session-demo", "to_id": "mascall:demo"},
    {"edge_type": "hasAgentCall", "from_id": "mascall:demo", "to_id": "agentcall:demo:tp-exec-001"},
    {"edge_type": "hasLLMCall", "from_id": "agentcall:demo:tp-exec-001", "to_id": "llmcall:demo:tp-llm-001"},
    {"edge_type": "executesAgent", "from_id": "agentcall:demo:tp-exec-001", "to_id": "planner"},
    {"edge_type": "executesLLM", "from_id": "llmcall:demo:tp-llm-001", "to_id": "catalog:model:gemini-2.5-flash"},
    {"edge_type": "belongsToMAS", "from_id": "planner", "to_id": "catalog:mas:trip-planner"}
  ]
}
```

## 6. Trajectory layer

### 6.1 State and Transition semantics

The trajectory records content flow through the run. `State` and `Transition` are mapped by `core/state_transition.py` from the `state_in` / `state_out` fields the runtime writes on each element record (see [native-telemetry.md](native-telemetry.md#state-identity)); nothing is inferred:

- A **State** is a piece of content named by an id the runtime wrote. The same id on two records is one State.
- A **Transition** is the step one element performs: it consumes the States the element reads and produces the one it writes, and *represents* that element (`representsExecution`). A parallel group's join is one ProcessingCall whose Transition has one input State per member.
- The States are chained by the runtime's own ids: the output State of one element is the input State of the next, as the emitter wrote it, not as the fold ordered siblings. The result is a Mealy-style view of a run: a Mealy machine is a finite automaton whose outputs are attached to the transitions between states, not to the states.
- An element or record without state fields has no State and no Transition.

State ids are scoped to the run, but `State.contentHash` (SHA-256 of the content) is identical for identical content across sessions, so content can be compared or embedded once.

A parallel fan-out needs no class of its own: the members read the same State and each writes its own, and the group's `aggregate` ProcessingCall is the join.

### 6.2 Trajectory node attribute tables

| Node | Attributes |
|---|---|
| `State` | `stateNodeId`, `contentHash`, `content`, `sessionId`, `layer = "normalized"` |
| `Transition` | `transitionId`, `realizesCallId`, `transitionTimestamp`, `sessionId`, `layer` |

### 6.3 Trajectory edges

| Edge | From -> To | Meaning |
|---|---|---|
| `hasInitialState`, `hasFinalState` | `ExecutionElement` -> `State` | the States an element reads and writes (one per input of a join); also from `Session` and `MASCall` to the boundary States `mas_call_end` names (the same States for both) |
| `hasState` | `ExecutionElement` -> `State` | every state of the call |
| `inputTo` | `State` -> `Transition` | state consumed by a step |
| `leadsTo` | `Transition` -> `State` | state produced by a step |
| `representsExecution` | `Transition` -> `ExecutionElement` | which call the step is |
| `correspondsToTransition` | `ExecutionElement` -> `Transition` | inverse of `representsExecution`; also from `Session` and `MASCall` to their own Transition |

`inputTo` and `leadsTo` require exactly the endpoint class shown; `hasInitialState`, `hasFinalState`, `hasState`, `correspondsToTransition`, `representsExecution` are required by SHACL.

Cypher for one call's trajectory:

```cypher
MATCH (c:AgentCall {callId: $call_id})-[:hasInitialState]->(s0:State)
MATCH (c)-[:correspondsToTransition]->(t:Transition)-[:leadsTo]->(s1:State)
RETURN s0.content AS input, s1.content AS output
```

### 6.4 Trajectory constraints

From the SHACL shapes:

| Constraint |
|---|
| every `State` has `content`; every `TrajectoryElement` has `sessionId` |
| every `ExecutionElement` has `hasInitialState`, `hasFinalState`, `hasState` and `correspondsToTransition` |
| every `Transition` has `representsExecution`; every `State` has `inputTo`; every `Transition` has `leadsTo` |
| a `Session`'s initial and final `State` must also be the initial / final state of at least one other `ExecutionElement` (custom shapes) |
| only `TrajectoryElement` nodes may carry `layer`, and the value must be `normalized` (structural check `layer_vocabulary`) |

Records without state fields get no State/Transition and therefore violate the first two rows when validated with SHACL. A State that no later element reads (the final State of the root visit) has no `inputTo`.

### 6.5 Trajectory example

One LLM call's trajectory, as the runtime named its States (`tp-exec-001#0` is the visit's first State):

```json
{"node_type": "State", "id": "state:demo:tp-exec-001#0", "content": "Plan a 3-day trip to Paris."},
{"node_type": "State", "id": "state:demo:tp-exec-001#1", "content": "Trip planned: AF101 NYC to CDG."},
{"node_type": "Transition", "id": "trans:llmcall:demo:tp-llm-001", "realizesCallId": "tp-llm-001"}

{"edge_type": "hasInitialState", "from_id": "llmcall:demo:tp-llm-001", "to_id": "state:demo:tp-exec-001#0"},
{"edge_type": "hasFinalState", "from_id": "llmcall:demo:tp-llm-001", "to_id": "state:demo:tp-exec-001#1"},
{"edge_type": "inputTo", "from_id": "state:demo:tp-exec-001#0", "to_id": "trans:llmcall:demo:tp-llm-001"},
{"edge_type": "leadsTo", "from_id": "trans:llmcall:demo:tp-llm-001", "to_id": "state:demo:tp-exec-001#1"},
{"edge_type": "representsExecution", "from_id": "trans:llmcall:demo:tp-llm-001", "to_id": "llmcall:demo:tp-llm-001"}
```

`parallel_group_*` records are plain markers and map to nothing; a parallel group's join is a ProcessingCall of the agent visit.

## 7. Provenance layer

The provenance layer is **off** by default (`--categories annotations`). Enable it to see which pieces of content went into each LLM prompt, for example for retrieval audits or context-budget tuning.

### 7.1 ContextContribution semantics

A `ContextContribution` (a `CallAnnotation`) records one part of the context assembled for an LLM call, from a `context_part_contributed` event. It `annotates` the `LLMCall` named by `llm_call_id`, else the call named by `parent_call_id`; no call is looked up by time. The text itself is in that LLM call's input State; the contribution is its provenance.

### 7.2 Provenance node attributes

| Attribute | Source (`event` field) |
|---|---|
| `partId` | `part_id` (derived hash when absent) |
| `contextSource` | `source` |
| `contributionMechanism` | `access_mechanism` |
| `tokenEstimate` | `token_estimate` |
| `retained` | `retained` |
| `eventKind`, `eventTime` | `kind`, `timestamp` |
| `payload` | JSON object of `parents`, `section_id`, `source_type`, `cause`, `cause_type`, `sensitivity`, `eviction_reason`, `content`, for those the event carries |

There is no `derivedFrom` edge (SCOPE has none): the parents stay in `payload`.

### 7.3 Provenance constraints

| Constraint | Checked by |
|---|---|
| `annotates` present (violation), `partId` and `contextSource` present (warning) | SHACL |

An event that names neither `llm_call_id` nor `parent_call_id` gives a contribution with no `annotates` edge, which SHACL reports; fix it in the emitter.

Example (event field names; graph attributes are camelCase):

```json
{"kind": "context_part_contributed", "part_id": "part-hotel-rag-001", "source": "rag_result",
 "access_mechanism": "vector_search", "token_estimate": 142, "retained": true,
 "llm_call_id": "tp-llm-002", "parents": []}
```

becomes a node `cpr-part-hotel-rag-001` with `contributionMechanism = "vector_search"` and an `annotates` edge to `tp-llm-002`'s `LLMCall`.

## 8. Infrastructure layer

`infrastructure_info` events map to the `Worker` class (`StructuralElement`) when the `worker` category is on. The record's `worker_id` names the node, `worker_pid` gives `processId` and `durable_backend` gives `durableBackend`. When the record carries a `parent_call_id`, that execution `runsOn` the Worker; a record with no parent gives the node alone.

## 9. Governance layer

The governance layer is **off** by default (`--categories annotations`). Enable it to record policy decisions, audit events, budget events and human-in-the-loop gates.

### 9.1 GovernanceEvent and CallAnnotation

Both are point-in-time nodes with no `call_id`, attached to the call their record names by `annotates` (annotation -> call), so governance data never changes the execution node it describes.

- `GovernanceEvent` is the class for every governance kind in `KIND_TO_CLASS`.
- `CallAnnotation` is the class for routing and the opt-in annotation kinds (context assembly, state updates, checkpoints, user input and output, `observability_*_execute_*`, ...).

Both are SCOPE classes (`GovernanceEvent` is a subclass of `CallAnnotation`).

### 9.2 Attributes

Common to both: `eventKind` (the original event kind), `eventTime` (the timestamp), `sessionId` and `payload`, a JSON object holding the fields the record carries among `source_agent_id`, `target_agent_id`, `task`, `correlation_id`, `status`, `segments`, `total_tokens`, `operation`, `target`, `from`, `to`, `message_type`, `denied_call_id`, `budget_scope`, `amount`, `intervention_type`. A `GovernanceEvent` also has `decision` (`decision`), `policyName` (`policy_name`, else `policy_id`) and `decisionReason` (`reason`).

### 9.3 Governance event kinds

| Kind | Meaning |
|---|---|
| `audit` | a call was examined by a policy or monitoring component |
| `policy_denial`, `policy_allow` | a policy rule blocked or explicitly allowed a call |
| `budget_event` | a token or cost budget threshold was reached (`budgetScope`, `amount`) |
| `transformation_event` | content was transformed (redacted, rewritten) by governance |
| `control_intervention` | a control component intervened (`interventionType`) |
| `hitl_gate`, `hitl_request` | a human-in-the-loop gate or request |
| `governance_denied`, `governance_checked` | a governance check denied or permitted a call (`policyId`, `decision`, `reason`, `deniedCallId`) |
| `governance_decision`, `governance_event`, `governance_policy` | decision, generic and policy records |
| `governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_authorize_*`, `obs_wrap_gov_validate_*` | start/end records of the authorize and validate steps, and the runtime wrapper events around them: one `ProcessingCall` per pair (core class), named by the kind, contained by the call named in `parent_call_id` |


### 9.4 Governance edges and constraints

| Edge | From -> To |
|---|---|
| `annotates` | `GovernanceEvent` / `CallAnnotation` -> call (required by SHACL) |

Notes on validation:

- Zero or more governance nodes may annotate one call. A node whose record names no call has no `annotates` edge, and SHACL reports it.

## 10. Global constraints

There is no separate constraint numbering in the code. Constraints are enforced in two ways:

**Structural checks** (`core/verifier.py`, always available):

| Check | Rule |
|---|---|
| `unknown_node_types` | every `node_type` is a class declared in the ontology graph (`ioc-scope-ontology`) |
| `unknown_edge_types` | every `edge_type` is an object property declared there |
| `block_vocabulary` | `block`, when present, is one of `structural`, `execution`, `trajectory`, `governance` and matches the node's class (`structural` for `StructuralElement` subclasses, `trajectory` for `TrajectoryElement` subclasses, `execution` otherwise) |
| `layer_vocabulary` | `layer` appears only on `TrajectoryElement` nodes, with value `normalized` |
| `orphaned_edges` | every edge endpoint id exists in the node list |
| `experiment_extension` (optional) | experiment, scenario, test item and run label attributes on `Session`, when `--enable-extension-layer experiment` is set |

**SHACL** (`shacl` check): `mas-ontology.ttl` property shapes plus `mas-shapes.ttl` and `mas-shapes-custom.ttl` from `ioc-scope-ontology`, evaluated with `pyshacl` against the graph serialised as RDF. The constraints are listed in sections 5.4 and 6.4. Severity is violation (error), warning or info per shape; `strict` promotes warnings to errors.

**Stable ids.** Id stability is by construction (deterministic id functions; [normalization.md](normalization.md#9-node-id-stability)), not checked at validation time. Two things break it: duplicate point events in the input (each produces a node with the same id) and any pre-processing that changes timestamps, since annotation ids hash the event timestamp.

## 11. URN scope conventions

Every node and edge carries a `canonicalId` URN, assigned in `core/graph_builder.py`. The scope is part of the identity:

| Scope | Pattern | Used for |
|---|---|---|
| session | `urn:mas:session:{sessionId}:call:{callId}`, `...:state:{stateNodeId}`, `...:transition:{transitionId}`, `...:annotation:{annotationId}`, `...:edge:{edgeType}:{sha1[:16]}` | call, state, transition, annotation nodes and all edges: ephemeral, one per trajectory |
| session | `urn:mas:session:{sessionId}` | the `Session` node |
| global | `urn:mas:global:{type}:{slug}` | catalog nodes (`Agent`, `MAS`, `LLM`, `Tool`, `Processing`, `Skill`): stable across sessions |
| node | `urn:mas:node:{type}:{slug}` | fallback for nodes with no other identity (for example `ContextContribution`) |

Two sessions with identical state content get different session-scoped URNs but the same `contentHash`. For Neo4j-only use, the short `id` values from [section 4](#4-node-id-construction) are the node keys; URNs matter for RDF export and SHACL validation, which serialise the graph under the `mas:` namespace `https://ioc-project.github.io/scope-ontology/mas#`.

## 12. Verification

### What `run_validate_kg` checks

`run_validate_kg(artifact, ...)` (see [steps.md](steps.md); CLI `mas-lab kg validate KG_PATH`) runs the structural checks and the SHACL check from [section 10](#10-global-constraints):

| Check | Default |
|---|---|
| `unknown_node_types`, `unknown_edge_types`, `block_vocabulary`, `layer_vocabulary`, `orphaned_edges` | on |
| `shacl` | on; reported as **skipped** (not passed) unless `pyshacl` and `rdflib` are installed (`library-kg[validation]`) |
| `experiment_extension` | off; enabled with `--enable-extension-layer experiment` |

It returns a report dict:

```python
{"error_count": 0, "warning_count": 0, "skipped_count": 1,
 "results": [{"check": "unknown_node_types", "status": "pass", "detail": ""}, ...]}
```

`status` is one of `pass`, `warning`, `error`, `skipped`. When `error_count` is nonzero the function raises `KGValidationError`, whose `report` attribute carries the full report (there is no option to suppress this). Use `--list-checks`, `--check NAME` and `--skip-check NAME` on the CLI to choose checks.

### Known results on the shipped examples

- `library-kg/examples/qa-agent` with the default categories validates with zero errors (SHACL skipped when the extra is not installed).
- Traces that carry annotation, governance or provenance events with neither `call_id` nor `parent_call_id` produce SHACL `annotates` violations: the record names no call.

### What must be checked outside `run_validate_kg`

| Check | Approach |
|---|---|
| Re-ingestion stability | normalize the same input twice and compare with `run_compare_kg` ([steps.md](steps.md)); ids should match |
| Spec conformance (declared tools and agents versus what ran) | `merge_spec_into_kg` then compare ([kg-spec.md](kg-spec.md)) |
| Events-side schema (before normalization) | `mas-lab kg verify EVENTS_PATH` / `run_verify_events` ([steps.md](steps.md)) |

### Running validation

```python
from mas.library.kg.steps import run_validate_kg
from mas.library.kg.steps.validate_kg import KGValidationError

try:
    report = run_validate_kg("runs/001/kg.jsonld")
except KGValidationError as exc:
    for row in exc.report["results"]:
        if row["status"] == "error":
            print(f"[ERROR] {row['check']}: {row['detail']}")
    raise
```

In a pipeline, run validation immediately after `run_normalize` and stop on `KGValidationError` before annotation or the Neo4j push.

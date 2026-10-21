# MAS knowledge graph ontology

The ontology defines the node classes, edge types, attributes and constraints of the knowledge graph (KG) that `mas.library.kg` builds from a multi-agent system (MAS) run. It comes from the `oxp-ontology` package plus a MAS Lab extension file. Read it when you write queries, validate a KG, or add event kinds. Pipeline mechanics are in [normalization.md](normalization.md).

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
7. [Provenance layer](#7-provenance-layer) (7.1 semantics, 7.2 attributes, 7.3 `derivedFrom`, 7.4 constraints)
8. [Infrastructure layer](#8-infrastructure-layer)
9. [Governance layer](#9-governance-layer) (9.1 classes, 9.2 attributes, 9.3 kinds, 9.4 edges and constraints)
10. [Global constraints](#10-global-constraints)
11. [URN scope conventions](#11-urn-scope-conventions)
12. [Verification](#12-verification)

## 1. Overview

### What the ontology is

The KG represents a MAS run as a property graph: typed nodes (what ran, what it said, what state it was in) and typed directed edges (who contained whom, in what order). An ontology is the schema of those node and edge types. Normalization ([normalization.md](normalization.md)) turns a run's `events.jsonl` into this graph, stored as a `kg.jsonld` document or projected into Neo4j ([neo4j.md](neo4j.md)).

### Where it lives

There is no ontology module in `mas.library.kg`. The vocabulary comes from two places, both in the `https://outshift-open.github.io/oxp-ontology/mas#` namespace:

| Source | Contents |
|---|---|
| `oxp-ontology` (PyPI, a required dependency; part of the open-source Observe and eXplain Platform, OXP) | `mas-ontology.ttl` (core classes and properties), `mas-shapes.ttl` and `mas-shapes-custom.ttl` (SHACL shapes, the W3C constraint language for RDF graphs), and the generated Python models `oxp_ontology.models.*`. |
| `library-kg/ontology/extensions/mas-kg-native-extensions.ttl` | Classes and properties the native `events.jsonl` path emits that `oxp-ontology` does not declare yet: `RAGQuery`, `MemoryCall`, `SkillCall`, `Skill`, `ThinkingCall`, `CallAnnotation`, `GovernanceEvent`, `ContextContribution`, `ParallelGroup`, `Branch`, `Worker`, `Run`, `Application`, and the properties `annotates`, `contributesTo`, `callsAgent`, `hasMemoryCall`, `hasSkillCall`. It is not a second ontology; entries are removed once they land in `oxp-ontology`. |

`mas.library.kg.ontology` resolves the TTL paths. The validation step ([section 12](#12-verification)) loads both sources into one RDF graph.

### Class hierarchy

```
Element
|-- StructuralElement   (types: what the MAS is made of)
|     MAS, Agent, Capability -> LLM, Tool, Processing, Skill
|     Application, Run, Worker (extension)
|-- ExecutionElement    (instances: what actually ran; one *Call per trace unit)
|     Session, MASCall, AgentCall, CapabilityCall
|       CapabilityCall -> LLMCall, ToolCall, ProcessingCall -> SkillCall, ThinkingCall
|       CapabilityCall -> RAGQuery, MemoryCall (extension)
|-- TrajectoryElement   (content flow between steps)
|     State, Transition, ParallelGroup, Branch (extension)
|-- CallAnnotation, GovernanceEvent, ContextContribution (extension; direct subclasses of Element)
```

`TaskCall` is emitted by the native builder (an `execution_*` event with `boundary == "TaskCall"`) but is not declared in either TTL.

### How it is used

| Purpose | How it helps |
|---|---|
| Normalization target | One schema for traces from any framework or runtime. |
| Query contract | KG compare, query and annotation steps use the attribute and edge names defined here. |
| Validation | SHACL shapes and structural checks run against the vocabulary (section 12). |

### Design goals

- **Layered**: you switch optional layers on or off per run (section 2).
- **Stable ids**: ids are deterministic functions of the trace (section 4), so re-normalizing creates no duplicates.
- **Execution versus structure**: structural (catalog) nodes describe types (`Tool`, `LLM`, ...); execution nodes describe instances and point at their catalog node through `executes*` edges.

## 2. Layer overview

A layer is a group of event kinds that is included or excluded as a unit (flags `include_infrastructure`, `include_trajectory`, `include_provenance`, `include_governance`; see [normalization.md](normalization.md#8-layer-defaults-and-control)). The tags `L0` to `L5` are labels, not an ordering, and no layer uses L3.

| Layer | Tag | Default | Events | Node classes |
|---|---|---|---|---|
| Core | L0 | always on | `tool_call`, `network_call`, `llm_call`, `execution`, `mas_call`, `rag_query`, `memory_*`, `processing_call`, `workflow_transition`, `skill_execution` pairs; annotation kinds such as `context_assembled`, `state_update_*`, `checkpoint_*`, `user_input` | `Session`, `MASCall`, `AgentCall`, `TaskCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `SkillCall`, `MemoryCall`, `RAGQuery`, `ThinkingCall`, `CallAnnotation`, catalog nodes, `Run`, `Agent` |
| Infrastructure | L1 | **off** | `infrastructure_info` | `Worker` |
| Trajectory | L2 | **on** for `ParallelGroup`/`Branch`/routing; `State` and `Transition` are always built | `parallel_group_*`, `branch_*`, `routing`, `routing_result` | `ParallelGroup`, `Branch`, routing `CallAnnotation`s |
| Provenance | L4 | **off** | `context_part_contributed` | `ContextContribution` |
| Governance | L5 | **off** | `audit`, `policy_*`, `budget_event`, `transformation_event`, `control_intervention`, `hitl_*`, `governance_*`, `obs_wrap_gov_*` | `GovernanceEvent` |

`State` and `Transition` nodes are not tied to any flag: they are derived from every call that has content, whatever the layers. The `include_trajectory` flag controls only `ParallelGroup`, `Branch` and routing annotations.

**Defaults.** Provenance adds about one node per context part (often hundreds per session). Governance has content only when a policy engine is connected. Infrastructure currently produces no nodes ([section 8](#8-infrastructure-layer)).

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

The native builder adds identity and linkage attributes to execution nodes: `callId`, `executionId` (`exec-{Class}-{callId}`), `agentId`, `parentCallId` (empty for roots), `runId`, `kindBase`, `masUri`, `spanLevel`, `status` and `sourceRecordIds`. The full per-class list is in [normalization.md](normalization.md#6-node-attributes-reference).

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
| `State` | `state-{agent\|llm\|tool\|proc}-{callId}-{suffix}` |
| `Transition` | `trans-{agent\|llm\|tool\|proc}-{callId}` |
| `Session` | `session-{run_id}` (or the session UUID or hierarchical run id; see normalization.md) |
| `Run` | the run id |
| `Agent` | the agent id |
| `Tool`, `Skill`, `Processing`, `LLM` catalog | `catalog:tool:{name}`, `catalog:skill:{name}`, `catalog:processing:{name}`, `catalog:model:{name}` |
| `MAS` | `catalog:mas:{appName or mas}` |

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
| `Skill` (extension) | `name` | A skill invoked by `SkillCall`s. |
| `Application` (extension) | `appName` | Optional; groups `Session`s via `hasSession`. |
| `Run` (extension) | `runId` | One per run id. Present as a node, with no edges. |

`Tool`, `LLM`, `Skill` and `Processing` nodes also carry `callCount`, the number of calls referencing them.

### 5.2 Execution node attribute tables

Every execution node carries the common attributes of [section 3](#3-common-attributes). Class-specific attributes (emitted by the native path):

| Class | Attributes |
|---|---|
| `Session` | `sessionId`, `runId`, `appName`, `inputQuery`, `finalResponse` |
| `MASCall` | `masName`, `masType` |
| `AgentCall` | `agentName`, `agentType`, `agentSequence`, `inputContent`, `outputRaw`, `outputParts`, `outputContent` |
| `TaskCall` | `taskName`, `taskType` |
| `LLMCall` | `llmName`, `modelName`, `provider`, `prompt`, `completion`, `toolCalls`, token counts, `finishReason`, `responseId`, `temperature` |
| `ToolCall` | `toolName`, `toolCallId`, `toolArguments` (JSON string), `toolOutput`, optional `barrierId` |
| `ProcessingCall` | `processingName`, `processingType`, `inputContent`, `processingOutput` |
| `SkillCall` | `skillName`, `skillInput`, `skillOutput`, optional `skillVersion` |
| `MemoryCall`, `RAGQuery` | common attributes only |
| `ThinkingCall` | `thinkingContent` |

Source events and fallbacks are in [normalization.md](normalization.md#6-node-attributes-reference). Failure categories, memory operations, retrieval result counts and similar details are not modelled; they stay on the source events.

### 5.3 Core edges

Edge names are the ontology's. Domain and range are from `mas-ontology.ttl` unless marked (extension).

| Edge | From -> To | Meaning |
|---|---|---|
| `hasMASCall` | `Session` -> `MASCall` | session's top-level call |
| `hasAgentCall` | `MASCall` -> `AgentCall` | MAS call spawns agent calls (the native builder also emits it `AgentCall` -> `AgentCall` for time-enclosed delegations) |
| `belongsToMASCall` | `AgentCall` -> `MASCall` | inverse of `hasAgentCall` |
| `hasLLMCall`, `hasToolCall` | `AgentCall` -> `LLMCall` / `ToolCall` | containment |
| `hasProcessingCall` | `AgentCall` or `MASCall` -> `ProcessingCall` | containment (shortcut) |
| `hasMemoryCall`, `hasSkillCall` (extension) | call -> `MemoryCall` / `SkillCall` | containment |
| `hasRAGQuery` | call -> `RAGQuery` | containment; not declared in either TTL |
| `executes` | `ExecutionElement` -> `StructuralElement` | generic typing link |
| `executesSession` | `Session` -> `MAS` | |
| `executesMAS`, `executesAgent`, `executesLLM`, `executesTool`, `executesProcessing` | `MASCall` / `AgentCall` / `LLMCall` / `ToolCall` / `ProcessingCall` -> `MAS` / `Agent` / `LLM` / `Tool` / `Processing` | typing link; `executesLLM`, `executesTool`, `executesProcessing` also check the target class |
| `belongsToMAS` | `Agent` -> `MAS` | agent membership |
| `usesCapability`, `usesLLM`, `usesTool`, `usesProcessing` | `Agent` -> capability | declared in the ontology; not emitted by the native builder |
| `callsAgent` (extension) | call -> `Agent` | delegation (`delegate_to_*` tool) |
| `annotates` (extension) | `CallAnnotation` / `GovernanceEvent` -> call | |

Containment is derived from the `parentCallId` links and from time enclosure; a call may be contained by any enclosing call, so edges such as `ParallelGroup -> ToolCall` occur ([normalization.md](normalization.md#5-synthesised-nodes-and-edges)).

### 5.4 Core constraints

From the SHACL property shapes in `mas-ontology.ttl` (violation unless noted):

| Constraint | Applies to |
|---|---|
| `id` present (violation); `name` present (warning) | every `Element` |
| `startTime`, `endTime`, `duration` present | every `ExecutionElement` |
| at least one `executes` edge | every `ExecutionElement` |
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

The alignment step in `mas.library.kg` adds the `executes*`, `belongsTo*` and topology edges, and fills `name`, `duration`, `declared`, `provider` and the `LLMCall` defaults, so that these shapes can be satisfied; see `core/ontology_align.py`.

### 5.5 Core layer example

A trimmed excerpt of `mas-lab kg normalize library-kg/examples/trip-planner/events.jsonl --run-id demo` (selected attributes only):

```json
{
  "nodes": [
    {"node_type": "Session", "id": "session-demo", "startTime": 1716200000.0, "endTime": 1716200010.0},
    {"node_type": "MASCall", "id": "mascall:demo:mas:tp-mas-001", "agentId": "mas", "status": "success"},
    {"node_type": "AgentCall", "id": "agentcall:demo:planner:tp-exec-001",
     "agentId": "planner", "parentCallId": "tp-mas-001"},
    {"node_type": "LLMCall", "id": "llmcall:demo:planner:tp-llm-001",
     "agentId": "planner", "parentCallId": "tp-exec-001", "modelName": "gemini-2.5-flash"},
    {"node_type": "Agent", "id": "planner", "agentId": "planner"},
    {"node_type": "LLM", "id": "catalog:model:gemini-2.5-flash", "name": "gemini-2.5-flash"},
    {"node_type": "MAS", "id": "catalog:mas:mas"}
  ],
  "edges": [
    {"edge_type": "hasMASCall", "from_id": "session-demo", "to_id": "mascall:demo:mas:tp-mas-001"},
    {"edge_type": "hasAgentCall", "from_id": "mascall:demo:mas:tp-mas-001", "to_id": "agentcall:demo:planner:tp-exec-001"},
    {"edge_type": "hasLLMCall", "from_id": "agentcall:demo:planner:tp-exec-001", "to_id": "llmcall:demo:planner:tp-llm-001"},
    {"edge_type": "executesAgent", "from_id": "agentcall:demo:planner:tp-exec-001", "to_id": "planner"},
    {"edge_type": "executesLLM", "from_id": "llmcall:demo:planner:tp-llm-001", "to_id": "catalog:model:gemini-2.5-flash"},
    {"edge_type": "belongsToMAS", "from_id": "planner", "to_id": "catalog:mas:mas"}
  ]
}
```

## 6. Trajectory layer

### 6.1 State and Transition semantics

The trajectory records content flow through the run. `State` and `Transition` are built by `core/state_transition.py` for every `AgentCall`, `LLMCall`, `ToolCall` and `ProcessingCall` that has input or output content:

- A **State** is a snapshot of content at one point: a call's input (prompt, tool arguments, agent input) or output (completion, tool result, agent output).
- A **Transition** is one step that consumes an input State and produces an output State, and *represents* the call that performed it (`representsExecution`).
- The result is a Mealy-style view of a run: a Mealy machine is a finite automaton whose outputs are attached to the transitions between states, not to the states. Here each Transition carries `actionType` (`agent_delegation`, `llm_call`, `tool_call`, a context-management name) and `appliedOperator`, and a call's input is its preceding sibling's output, so siblings under one parent form a chain `State -> Transition -> State -> Transition -> ...`.
- Parallel tool calls with a shared `barrierId` produce Transitions with `edgeType = "parallel"` and `appliedOperator = "parallel_merge"`.

Per-run states are session-scoped, but `State.contentHash` (SHA-256 of the content) is identical for identical content across sessions, so content can be compared or embedded once.

`ParallelGroup` and `Branch` are `TrajectoryElement` subclasses for explicit fan-out; they come from `parallel_group_*` / `branch_*` events.

### 6.2 Trajectory node attribute tables

| Node | Attributes |
|---|---|
| `State` | `stateNodeId`, `contentHash`, `content` (truncated to 8000 characters), `semanticType` (`initial` or `final`), `sessionId`, `layer = "normalized"`; tool and processing states add `deltaType`; processing states add `contextWindowSize` |
| `Transition` | `transitionId`, `fromState`, `toState`, `realizesCallId`, `edgeType` (`sequential` or `parallel`), `actionType`, `appliedOperator` (`sequential_compose`, `parallel_merge`, `context_transform`), `transitionTimestamp`, `transitionDuration` (seconds), `sessionId`, `layer`; processing transitions add `transitionDeltaType`, `contextTokensIn`, `contextTokensOut`, `contextTokensDelta`, `compressionRatio`, `contextStrategy` |
| `ParallelGroup`, `Branch` | common call attributes (no `sessionId`), `layer = "normalized"`. `group_id`, `branch_count` and branch indexes stay on the source event; they are not copied to the node. |

### 6.3 Trajectory edges

| Edge | From -> To | Meaning |
|---|---|---|
| `hasInitialState`, `hasFinalState` | `ExecutionElement` -> `State` | a call's input and output state; also from `Session` and `MASCall` to the first and last top-level state |
| `hasState` | `ExecutionElement` -> `State` | every state of the call |
| `inputTo` | `State` -> `Transition` | state consumed by a step (also emitted for the output state, as the declared inverse of `leadsTo`) |
| `leadsTo` | `Transition` -> `State` | state produced by a step |
| `representsExecution` | `Transition` -> `ExecutionElement` | which call the step is |
| `correspondsToTransition` | `ExecutionElement` -> `Transition` | inverse of `representsExecution`; also from `Session` and `MASCall` to the first top-level transition |

`inputTo` and `leadsTo` require exactly the endpoint class shown; `hasInitialState`, `hasFinalState`, `hasState`, `correspondsToTransition`, `representsExecution` are required by SHACL.

Cypher for one call's trajectory:

```cypher
MATCH (c:AgentCall {callId: $call_id})-[:hasInitialState]->(s0:State)
MATCH (c)-[:correspondsToTransition]->(t:Transition)-[:leadsTo]->(s1:State)
RETURN s0.content AS input, t.actionType, s1.content AS output
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

Calls without any input or output content get no State/Transition and therefore violate the first two rows when validated with SHACL.

### 6.5 Trajectory example

From the same trip-planner run, one LLM call's trajectory:

```json
{"node_type": "State", "id": "state-llm-tp-llm-001-prompt", "semanticType": "initial"},
{"node_type": "State", "id": "state-llm-tp-llm-001-completion", "semanticType": "final"},
{"node_type": "Transition", "id": "trans-llm-tp-llm-001", "actionType": "llm_call",
 "fromState": "state-llm-tp-llm-001-prompt", "toState": "state-llm-tp-llm-001-completion",
 "realizesCallId": "tp-llm-001"}

{"edge_type": "hasInitialState", "from_id": "llmcall:demo:planner:tp-llm-001", "to_id": "state-llm-tp-llm-001-prompt"},
{"edge_type": "hasFinalState", "from_id": "llmcall:demo:planner:tp-llm-001", "to_id": "state-llm-tp-llm-001-completion"},
{"edge_type": "inputTo", "from_id": "state-llm-tp-llm-001-prompt", "to_id": "trans-llm-tp-llm-001"},
{"edge_type": "leadsTo", "from_id": "trans-llm-tp-llm-001", "to_id": "state-llm-tp-llm-001-completion"},
{"edge_type": "representsExecution", "from_id": "trans-llm-tp-llm-001", "to_id": "llmcall:demo:planner:tp-llm-001"}
```

A parallel group with its calls (`ParallelGroup` -> `ToolCall` and `RAGQuery` via `hasToolCall` / `hasRAGQuery`):

```json
{"node_type": "ParallelGroup", "id": "parallelgroup:demo:itinerary-agent:tp-pg-001"},
{"edge_type": "hasToolCall", "from_id": "parallelgroup:demo:itinerary-agent:tp-pg-001", "to_id": "toolcall:demo:itinerary-agent:tp-tc-001"}
```

## 7. Provenance layer

The provenance layer is **off** by default (`--include-provenance`). Enable it to see which pieces of content went into each LLM prompt, for example for retrieval audits or context-budget tuning.

### 7.1 ContextContribution semantics

A `ContextContribution` node records one part of the context assembled for an LLM call, from a `context_part_contributed` event. It is linked to the `LLMCall` it contributed to (`contributesTo`), by `llm_call_id` when the event names one, else to the call of the same agent whose time window contains the event. The `parents` list names the parts it was derived from.

### 7.2 Provenance node attributes

| Attribute | Source (`event` field) | Default |
|---|---|---|
| `partId` | `part_id` | derived hash |
| `parents` | `parents` or `provenance.parents` | `[]` |
| `source` | `source` | `""` |
| `sectionId` | `section_id` | `""` |
| `sourceType` | `source_type` | `unknown` |
| `accessMechanism`, `mechanism` | `access_mechanism` | `inject` |
| `cause` | `cause` | `context_manager` |
| `causeType` | `cause_type` | `deterministic` |
| `tokenEstimate` | `token_estimate` | `0` |
| `retained` | `retained` | `true` |
| `evictionReason` | `eviction_reason` (only when `retained` is false) | `budget_exceeded` |
| `sensitivity` | `sensitivity` | absent |
| `content`, `contentPreview` | `content` or `content_preview`; preview is the first 200 characters | absent |
| `agentId`, `timestamp`, `runId` | | |

The values of `sourceType`, `accessMechanism`, `cause` and `causeType` are free strings; the defaults above are the only ones the normalizer assigns.

### 7.3 The derivedFrom graph

`derivedFrom` links a `ContextContribution` to each parent listed in its `parents` that is present in the trace, forming a directed graph of how context parts were derived (summarised, extracted, transformed) from earlier ones.

| Edge | From -> To |
|---|---|
| `contributesTo` | `ContextContribution` -> `LLMCall` |
| `derivedFrom` | `ContextContribution` -> `ContextContribution` (child to parent) |

### 7.4 Provenance constraints

| Constraint | Checked by |
|---|---|
| `ContextContribution` and `contributesTo` are declared in the extension TTL | `unknown_node_types`, `unknown_edge_types` |
| `derivedFrom` is **not** declared in any TTL | `unknown_edge_types` reports it when present |
| the `derivedFrom` graph should be acyclic | no automated check; see [section 12](#12-verification) |

Example (event field names; graph attributes are camelCase):

```json
{"kind": "context_part_contributed", "part_id": "part-hotel-rag-001", "source": "rag_result",
 "access_mechanism": "vector_search", "token_estimate": 142, "retained": true,
 "llm_call_id": "tp-llm-002", "parents": []}
```

becomes a node `cpr-part-hotel-rag-001` with `accessMechanism = "vector_search"` and an edge `contributesTo` to `tp-llm-002`'s `LLMCall`.

## 8. Infrastructure layer

`infrastructure_info` events map to the `Worker` class (`StructuralElement`) when `include_infrastructure` is on. The current native handlers produce no `Worker` node: there is no dispatch handler for `Worker`, and the generic call handler skips events without a `call_id`. Enabling the layer therefore has no effect on the graph today. No `Worker` edge type is defined in either TTL.

## 9. Governance layer

The governance layer is **off** by default (`--include-governance`). Enable it to record policy decisions, audit events, budget events and human-in-the-loop gates.

### 9.1 GovernanceEvent and CallAnnotation

Both are point-in-time nodes with no `call_id`, attached to the enclosing call by `annotates` (annotation -> call), so governance data never changes the execution node it describes.

- `GovernanceEvent` is the class for every governance kind in `KIND_TO_CLASS`.
- `CallAnnotation` is the class for the always-on annotation kinds (routing, context assembly, state updates, checkpoints, user input and output, `observability_*_execute_*`, ...). Checkpoint kinds are tagged like governance events (`annotationKind`, `block = "governance"`) but are `CallAnnotation` nodes.

Both are declared in the extension TTL as direct subclasses of `Element`.

### 9.2 Attributes

Common to both: `annotationId` (= `id`), `agentId` (falls back to `source_agent_id`), `kind` (the original event kind), `timestamp`, `runId`. Payload fields are copied only when the event carries them:

`sourceAgentId`, `targetAgentId`, `task`, `correlationId`, `status`, `segments`, `totalTokens`, `operation`, `target`, `from`, `to`, `messageType`, `policyId`, `decision`, `reason`, `deniedCallId`, `budgetScope`, `amount`, `interventionType`.

For governance kinds the handler also sets `annotationKind` (= `kind`) and `block = "governance"` (see 9.3).

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
| `governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_authorize_*`, `obs_wrap_gov_validate_*` | start/end records of the authorize and validate steps, and the runtime wrapper events around them |

`annotationKind` and `block = "governance"` are set for every kind above except `hitl_request` and the `obs_wrap_gov_*` wrapper kinds, which become `GovernanceEvent` nodes without those two attributes.

### 9.4 Governance edges and constraints

| Edge | From -> To |
|---|---|
| `annotates` | `GovernanceEvent` / `CallAnnotation` -> call |

Notes on validation:

- `block = "governance"` is accepted by the `block_vocabulary` check only on `CallAnnotation` nodes whose `annotationKind` starts with `governance`; on `GovernanceEvent` nodes the check currently reports it as invalid (expected `execution`). Run governance traces through `mas-lab kg validate` with that in mind.
- Zero or more governance nodes may annotate one call; absence is never an error.

## 10. Global constraints

There is no separate constraint numbering in the code. Constraints are enforced in two ways:

**Structural checks** (`core/verifier.py`, always available):

| Check | Rule |
|---|---|
| `unknown_node_types` | every `node_type` is a class declared in the ontology graph (`oxp-ontology` plus the extension TTL) |
| `unknown_edge_types` | every `edge_type` is an object property declared there |
| `block_vocabulary` | `block`, when present, is one of `structural`, `execution`, `trajectory`, `governance` and matches the node's class (`structural` for `StructuralElement` subclasses, `trajectory` for `TrajectoryElement` subclasses, `execution` otherwise) |
| `layer_vocabulary` | `layer` appears only on `TrajectoryElement` nodes, with value `normalized` |
| `orphaned_edges` | every edge endpoint id exists in the node list |
| `experiment_extension` (optional) | experiment, scenario, test item and run label attributes on `Session`, when `--enable-extension-layer experiment` is set |

**SHACL** (`shacl` check): `mas-ontology.ttl` property shapes plus `mas-shapes.ttl` and `mas-shapes-custom.ttl` from `oxp-ontology`, evaluated with `pyshacl` against the graph serialised as RDF. The constraints are listed in sections 5.4 and 6.4. Severity is violation (error), warning or info per shape; `strict` promotes warnings to errors.

**Stable ids.** Id stability is by construction (deterministic id functions; [normalization.md](normalization.md#9-node-id-stability)), not checked at validation time. Two things break it: duplicate point events in the input (each produces a node with the same id) and any pre-processing that changes timestamps, since annotation ids hash the event timestamp.

## 11. URN scope conventions

Every node and edge carries a `canonicalId` URN, assigned in `core/graph_builder.py`. The scope is part of the identity:

| Scope | Pattern | Used for |
|---|---|---|
| session | `urn:mas:session:{sessionId}:call:{callId}`, `...:state:{stateNodeId}`, `...:transition:{transitionId}`, `...:annotation:{annotationId}`, `...:edge:{edgeType}:{sha1[:16]}` | call, state, transition, annotation nodes and all edges: ephemeral, one per trajectory |
| session | `urn:mas:session:{sessionId}` | the `Session` node |
| run | `urn:mas:run:{runId}` | the `Run` node |
| global | `urn:mas:global:{type}:{slug}` | catalog nodes (`Agent`, `MAS`, `LLM`, `Tool`, `Processing`, `Skill`): stable across sessions |
| node | `urn:mas:node:{type}:{slug}` | fallback for nodes with no other identity (for example `ContextContribution`) |

Two sessions with identical state content get different session-scoped URNs but the same `contentHash`. For Neo4j-only use, the short `id` values from [section 4](#4-node-id-construction) are the node keys; URNs matter for RDF export and SHACL validation, which serialise the graph under the `mas:` namespace `https://outshift-open.github.io/oxp-ontology/mas#`.

## 12. Verification

### What `run_validate_kg` checks

`run_validate_kg(artifact, ...)` (see [steps.md](steps.md); CLI `mas-lab kg validate KG_PATH`) runs the structural checks and the SHACL check from [section 10](#10-global-constraints):

| Check | Default |
|---|---|
| `unknown_node_types`, `unknown_edge_types`, `block_vocabulary`, `layer_vocabulary`, `orphaned_edges` | on |
| `shacl` | on; reported as **skipped** (not passed) unless `pyshacl` and `rdflib` are installed (`mas-library-kg[validation]`) |
| `experiment_extension` | off; enabled with `--enable-extension-layer experiment` |

It returns a report dict:

```python
{"error_count": 0, "warning_count": 0, "skipped_count": 1,
 "results": [{"check": "unknown_node_types", "status": "pass", "detail": ""}, ...]}
```

`status` is one of `pass`, `warning`, `error`, `skipped`. When `error_count` is nonzero the function raises `KGValidationError`, whose `report` attribute carries the full report (there is no option to suppress this). Use `--list-checks`, `--check NAME` and `--skip-check NAME` on the CLI to choose checks.

### Known results on the shipped examples

- `library-kg/examples/qa-agent` with default layers validates with zero errors (SHACL skipped when the extra is not installed).
- Enabling governance produces `block_vocabulary` errors for `GovernanceEvent` nodes (see [section 9.4](#94-governance-edges-and-constraints)).
- Traces that contain a `RAGQuery` (the trip-planner example, default layers) produce an `unknown_edge_types` error for `hasRAGQuery`; `hasThinking`, `derivedFrom` and `hasSession` behave the same, and `TaskCall` is reported by `unknown_node_types`. These names are produced by the native builder but are not declared in the TTLs yet.

### What must be checked outside `run_validate_kg`

| Check | Approach |
|---|---|
| Re-ingestion stability | normalize the same input twice and compare with `run_compare_kg` ([steps.md](steps.md)); ids should match |
| `derivedFrom` acyclicity | after a Neo4j push, run `MATCH path = (c:ContextContribution)-[:derivedFrom*]->(c) RETURN path LIMIT 1`; any result is a cycle |
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

# Normalization pipeline

Normalization turns a run's native event log (`events.jsonl`) or OpenTelemetry (OTel) spans into a knowledge graph (KG): typed nodes (entities such as an agent call) and directed edges (relations between them), stored as `kg.jsonld`. This page is the reference for that conversion in `mas.library.kg`. Read it when you need to know which event produces which node, or why an id or edge looks the way it does.

```bash
mas-lab kg normalize path/to/events.jsonl -o kg.jsonld
```

Code covered: `mas.library.kg.pipeline`, `core/graph_builder.py`, `core/native_graph_builder.py`, `core/event_mappings.py`, `core/state_transition.py`, `core/scope_models.py`, `observability/native/handlers/`.

See also: [ontology.md](ontology.md), [steps.md](steps.md), [neo4j.md](neo4j.md).

## Table of contents

1. [Overview](#1-overview)
2. [Normalization modes and controls](#2-normalization-modes-and-controls)
3. [Runtime properties](#3-runtime-properties)
4. [Event to KG mapping table](#4-event-kg-mapping-table)
5. [Nodes and edges the mapping produces](#5-nodes-and-edges-the-mapping-produces)
6. [Node attributes reference](#6-node-attributes-reference)
7. [Edge type reference](#7-edge-type-reference)
8. [Categories and control](#8-categories-and-control)
9. [Node ID stability](#9-node-id-stability)

## 1. Overview

Normalization reads native observability events of a multi-agent system (MAS), from an `events.jsonl` file or a list of dicts, and writes a `kg.jsonld` document with the top-level keys `@context`, `nodes`, `edges` and `metadata`.

Terms used below:

- **Call node**: a node for one execution step, created from a `*_start` / `*_end` event pair (`AgentCall`, `LLMCall`, `ToolCall`, ...). Call nodes carry a `callId`.
- **Annotation node**: a point-in-time event with no `call_id` (`CallAnnotation`, `GovernanceEvent`, `ContextContribution`).
- **State / Transition**: trajectory nodes. A `State` holds content that flows through the system and is named by an id the runtime writes on the event; a `Transition` connects the States an element reads and writes and *represents* that element (see [section 5](#5-nodes-and-edges-the-mapping-produces)).
- **Category**: a group of classes you can switch on (`capabilities`, `annotations`, `worker`, `thinking`; see [section 8](#8-categories-and-control)).

OTel telemetry is input, not a node layer: normalization never emits `Span` nodes. Call nodes carry `sourceRecordIds`, the `event_id` of each native event that created or updated the node. Validation uses `mas-ontology.ttl` from the `ioc-scope-ontology` package (the SCOPE ontology) (see [ontology.md](ontology.md)). MAS Lab ships no ontology file of its own.

The native pipeline has two stages. The second is a plain mapping: each event gives nodes and edges by itself, and nothing is inferred.

```
events.jsonl
  -> normalize_events        (annotate: class lookup, category filter, call_id check)
  -> extract_graph           (back-compat wrapper over NativeGraphBuilder)
       process_event x N     (map one event to its nodes and edges: handlers/*.py)
       finalize()            (merge by id, drop edges to nodes that never arrived, SCOPE projection)
  -> kg.jsonld document
```

### Stage 1: annotation (`core/graph_builder.py:normalize_events`)

`normalize_events(events, ontology, run_id, categories=None, strict=False)` iterates the list once and, per event:

1. Looks up the ontology class in `KIND_TO_CLASS` (`core/event_mappings.py`) via `_class_for_event`. Kinds mapped to `None` are suppressed and receive `mas_class = "ExecutionElement"`, which `process_event` skips. Kinds absent from the table raise `UnknownSpanBoundaryError`.
2. Drops the event when that class belongs to a category that is not enabled (`skipped_classes(categories)`, built from `CATEGORY_CLASSES`). The realtime `NativeGraphBuilder` applies the same filter, so both folds drop the same events, and a dropped event raises no gap error even under `strict`.
3. Annotates the event with `mas_class`, `mas_uri`, `span_level` and `mas_icon` from the ontology index.
4. Checks that structural events carry a `call_id` (a missing one raises `MissingCallIdError`). `ProcessingCall` events without one get a deterministic `agent:parent:type` key instead. Annotation, governance, provenance and infrastructure events legitimately have none.
5. Sets `run_id` and copies `event_id` to `record_id`.

Both errors are observability gaps, not fatal: with the default `strict=False` the event is dropped, a deduplicated `WARNING` is logged and normalization continues. `strict=True` raises instead (useful in CI checks of the mapping table).

### Stage 2: graph construction (`core/native_graph_builder.py`)

`extract_graph(normalized, ...)` in `core/graph_builder.py` is a thin wrapper: it calls `build_graph_from_normalized`, which feeds each event to `NativeGraphBuilder.process_event` (in the order given; the order does not matter) and then calls `finalize()`.

- `process_event` dispatches on `mas_class` through `HANDLERS` (`observability/native/handlers/__init__.py`): `agent.py` (AgentCall), `chat.py` (LLMCall), `tool.py` (ToolCall, SkillCall), `processing.py`, `memory.py` (MemoryCall and RAGQuery), `session.py` (MASCall and the Session), `annotation.py` (CallAnnotation, GovernanceEvent, ContextContribution), `structural.py` (Worker). A class with no handler (a suppressed kind) adds nothing. Call handlers build the node one record says (`NativeGraphBuilder.call_partial`) and add it with its containment and trajectory edges (`emit_call`); the builder merges the start and the end record of a call by node id. `process_event` returns the nodes and edges that event contributes.
- `finalize()` is the only whole-graph step and it only: merges nodes by id; names each `Session` from the run and the ids the events carry; resolves the call ids an annotation names; drops edges whose endpoint never arrived; fills the attributes a node derives from its own merged fields (`name`, `duration`, `contentHash`, defaults); assigns `id` / `canonicalId` to edges; and applies `core/scope_models.py:map_graph` / `restrict_graph` (the SCOPE attribute projection). The graph is ordered by id. It can be called at any point and again later.

`build_kg_document` (`pipeline.py`) wraps both stages and adds `metadata` (event/node/edge counts, `ontology_path`, `categories`, `plot_events`).

## 2. Normalization modes and controls

`library-kg` has two entry modes:

1. **Native events** (`events.jsonl`)
   - Entry points: `build_kg_document(...)`, `build_kg_from_events_path(...)`, `run_normalize(...)`, `mas-lab kg normalize EVENTS_PATH`.
   - Code path: `normalize_events` -> `extract_graph` as in [section 1](#1-overview).

2. **OTel spans**
   - Entry points: `build_kg_from_otel_spans(...)`, `run_normalize_otel(...)`, `mas-lab kg otel-to-kg`, `mas-lab kg normalize --otel FILE`.
   - Code path: `observability/otel_via_norm.py:normalize_otel`. It detects ClickHouse-shaped rows (`SpanName` / `SpanAttributes`) versus OTel SDK span JSON (`name` / `context` / `attributes`), reshapes SDK spans mechanically, and feeds them to `norm.normalize` from the SCOPE OTel normalizer, then completes and aligns the graph (`otel_completion`, `align_graph`). Dispatch and span handlers live in `norm`, not here; no events are reconstructed.
   - `norm` is the optional `[norm]` extra (installed from PyPI as `ioc-scope-norm`). Without it, the OTel entry points log a warning and return an empty graph.

`pipeline.normalize(raw_input)` detects the shape (`kind` field means native; `SpanName` or `name`+`context`/`attributes` means OTel) and returns `(nodes, edges)`.

### 2.1 Controls

| Control | Where | Meaning |
|---|---|---|
| `strict` | `normalize_events`, `build_kg_document`, `run_normalize` | Raise on observability gaps instead of logging and skipping the event. |
| `categories` | same, `--categories` (comma-separated) and the `normalize_events` step | Opt-in categories added to the core output: `capabilities`, `annotations`, `worker`, `thinking`, or `all`; default none ([section 8](#8-categories-and-control)). |
| `all_attributes` | `build_kg_document`, `--all-attributes` | Keep native-only fields. Default keeps SCOPE model attributes. |
| `split_session_id` | `build_kg_document`, `--split-session-id` | Split a `<appName>_<uuid>` run id into `Session.appName` and `Session.sessionId`; default on. |
| `app_name` | `build_kg_document`, `--app-name` | Override `Session.appName`. |
| `session_id_override` | `extract_graph`, `--session-id` | Rename the session of a single-run trace (ignored with a warning when the trace has several run ids). |
| `include_normalization_provenance` | `build_kg_document`, `build_kg_from_otel_spans` | Add `normalization_path`, `provenance_mode`, `heuristics_applied` to `metadata` (default off). |

### 2.2 No inference

Neither path reconstructs missing telemetry. The native path maps the fields on each event and nothing else: no timestamp enclosure, no sibling order, no content comparison, no routing guess, no fabricated node, no placeholder text. A record without `state_in` / `state_out` gets no State and no Transition. A call whose `parent_call_id` names an agent visit that never arrives gets no containment edge. A call that never ended keeps no `endTime` and no `success`. The only text derived from a record is its own rendering: `LLMCall.completion` (the response text followed by its rendered tool calls) and `ProcessingCall.processingOutput` (the assembled messages), both read off that one record.

### 2.3 Provenance fields

- `sourceRecordIds` is the sorted list of `record_id`s that created or updated a call node. On the native path `record_id = event_id`.
- Document-level provenance in `metadata` is opt-in (`include_normalization_provenance`) to avoid payload growth.

## 3. Runtime properties

| Property | Verdict | Detail |
|---|---|---|
| Per-event processing | Yes | `NativeGraphBuilder.process_event` maps one event at a time and returns what that event contributes. |
| Real-time, complete graph | Yes | The accumulation is a merge by id; `finalize()` returns the whole graph at any point and does not change it. |
| Order-agnostic | Yes | Ids come from what an event carries; two records of one node merge commutatively. Permuting the events gives the same graph. |
| Stateless across traces | Yes | State lives in the builder instance. |
| Duplicate events | Yes | Duplicates merge into the same node or edge. |
| Out-of-order events | Yes | No ordering is read. An edge may name a node that arrives later. |

### Realtime and batch are one mapping

The batch fold is `process_event` looped over the events, then `finalize()`. `build_graph_realtime(builder, event)` feeds one event to a long-lived `NativeGraphBuilder` and returns the nodes and edges that event contributes (a node may be partial: the other record of its call says the rest). `build_kg_document(..., realtime=True)` folds the same way and gives the same graph as the batch path.

Classification and handler dispatch are O(1) per event (a dict lookup in `KIND_TO_CLASS`, then one handler call). `finalize()` is linear in the nodes and edges.

### Merging by id

Nodes with the same id merge field by field, and the result does not depend on the order of the records:

- an empty value (`None`, `""`, `[]`) never overwrites anything and is not stored;
- `startTime` keeps the earliest value, `endTime` and the call's `status` come from the end record, `sourceRecordIds` is a sorted union;
- two different non-empty values for one field keep the one that sorts first. For a `State` this is its `content`: the emitter guarantees equal content for a same id, so a difference is logged as a warning and never raises.

Edges are keyed by `(edge_type, from_id, to_id)`; a repeat adds nothing.

### Statelessness

Normalization keeps no state across invocations. The only module-level state is `_ontology_cache` in `core/graph_builder.py`, a read-only cache of parsed ontology indexes keyed by resolved TTL path. The per-trace accumulators (`nodes`, `edges`, `run_ids` and the session ids and app names the events carry) belong to the `NativeGraphBuilder` instance.

### Duplicates

A repeated `*_start` or `*_end` merges into the call node (the same values change nothing). Duplicate point events (annotations, governance, context contributions) have the same identity key and merge into one node.

## 4. Event → KG mapping table

The authoritative mapping is `core/event_mappings.py:KIND_TO_CLASS`. A kind mapped to `None` is silently dropped; a kind that is absent is an observability gap (warning, dropped; error under `strict`). "Start/end" kinds update one node; "point" kinds create one node per event. Opt-in classes are enabled by the `categories` of [section 8](#8-categories-and-control).

### Call nodes (start/end pairs)

| Event kind (`_start` / `_end`) | KG class | Fields read | Notes |
|---|---|---|---|
| `tool_call`, `network_call` | `ToolCall` | `tool_name`, `tool_call_id`, `tool_arguments` / `arguments` / `parameters`, `output` / `result` | A `delegate_to_*` tool is an ordinary `ToolCall` of the caller. |
| `llm_call` | `LLMCall` | `model`, `llm_name`, `input` / `prompt` / `messages`, `response` (`content`, `usage`, `id`, `choices`, `tool_calls`), `temperature` | |
| `execution` | `AgentCall` | `agent_id`, `agent_type`, `input`, `output`, `agent_sequence` | |
| `mas_call` | `MASCall` | `mas_name`, `mas_type`, `mas_id` | `mas_call_end` also names the boundary States of the Session and the MASCall. |
| `rag_query` | `RAGQuery` | `memory_name` / `collection` / `memory_type`, `query` / `query_text`, `result_count` / `results_count` / `retrieved_doc_count`, `top_k` | A `MemoryCall` that retrieves; `memoryOperation` is `retrieve`. Category `capabilities`. |
| `memory_call`, `memory_store`, `memory_retrieve` | `MemoryCall` | the same, `operation`, `key` | `memoryOperation` is `store` or `retrieve` from the kind, else the record's `operation`. The `Memory` it executes is named by `memory_name`, else `collection`, else `memory_type`; a record naming none gives no `Memory` and no `executesMemory` edge. Category `capabilities`; the agent `usesMemory` the `Memory`. |
| `processing_call` | `ProcessingCall` | `processing_name`, `processing_type`, `input`, `messages`, `output`, `state_in`, `state_out` | Context assembly, the `delegate` / `reply` agent-boundary steps and the `aggregate` join are ProcessingCalls. The end event's `messages` list is rendered as `role: content` lines into `processingOutput`; otherwise `output` is used. |
| `workflow_transition` | `ProcessingCall` | as `processing_call` | No dedicated ontology class. |
| `governance_authorize`, `governance_validate`, `obs_wrap_gov_authorize`, `obs_wrap_gov_validate`, `checkpoint` (`_start` / `_end`) | `ProcessingCall` | `call_id` (the interval's own key), `parent_call_id` (the call it brackets), `state_in`, `state_out`, `decision` / `decision_type` / `outcome` / `resolution` | Timing bracket of a check: name and `processingType` are the kind without its suffix. The call named in `parent_call_id` contains it. A stated decision on the end record also gives a `GovernanceEvent` (`annotations`) that annotates the bracket. |
| `skill_execution` | `SkillCall` | `skill_name` | The `Skill` it executes; the agent `usesSkill` it. Category `capabilities`. |
| `thinking` | `ThinkingCall` | `model`, `reasoning_tokens` (end), `state_in`, `state_out` | The reasoning interval of one LLM call: `call_id` is `<llm_call_id>#think`, `parent_call_id` the LLM call, which `hasThinkingCall` it; `executesLLM` the model. Category `thinking`. |
| `parallel_group` | none | | Plain markers, suppressed: the trajectory carries the fan-out and the `aggregate` join. `branch_*` is not a known kind. |

"Common fields" are set by `call_partial` for every call node: `callId`, `executionId`, `agentId`, `parentCallId` (from the start record), `kindBase`, `masUri`, `spanLevel`, `runId`, `startTime`, `endTime`, `status`, `success`, `sourceRecordIds`. Every element record may also carry `state_in` / `state_out` and their content ([section 5](#5-nodes-and-edges-the-mapping-produces)).

### Annotation nodes (point events)

Each becomes a `CallAnnotation` (or `GovernanceEvent`) and is linked by `annotates` to the call its record names in `call_id` (else `parent_call_id`), when that call is in the trace. `routing` and `routing_result` use `source_agent_id` as the agent when `agent_id` is null.

| Event kinds | KG class | Category |
|---|---|---|
| `routing`, `routing_result` | `CallAnnotation` | `annotations` |
| `context_assembled`, `state_update_start/end`, `agent_communication_start/end`, `observability_pre_execute_start/end`, `observability_post_execute_start/end`, `user_input`, `user_output`, `client_response`, `tool_result_injected`, `boundary_ingress`, `boundary_egress`, `context_steer`, `compaction`, `wait_state_start/end` | `CallAnnotation` | `annotations` |
| `audit`, `policy_denial`, `policy_allow`, `budget_event`, `transformation_event`, `control_intervention`, `hitl_gate`, `hitl_request`, `governance_denied`, `governance_checked`, `governance_decision`, `governance_event`, `governance_policy` | `GovernanceEvent` | `annotations` |
| `context_part_contributed` | `ContextContribution` | `annotations` |
| `infrastructure_info` | `Worker` | `worker` |

Annotation payload fields copied from the event (`_ANN_KEY_MAP` in `handlers/annotation.py`): `source_agent_id`, `target_agent_id`, `task`, `correlation_id`, `status`, `segments`, `total_tokens`, `operation`, `target`, `from`, `to`, `message_type`, `policy_id`, `decision`, `reason`, `denied_call_id`, `budget_scope`, `amount`, `intervention_type` (stored camelCase, see [section 6](#6-node-attributes-reference)).

### Suppressed kinds (mapped to `None`)

`prompt_build_start/end`, `user_response` (the reply is on `execution_end`), `human_interaction`, `delegated_agent_output`, `skills_check`, `object_upsert`, `object_link`, `object_model_event`, `user_input_request`, `contract_call_start/end`. These produce no nodes; older traces that still emit them are tolerated.

A `llm_call_start` or `tool_call_start` event that carries a `content` field is treated as a `CallAnnotation` (the OTel round-trip representation of a duplicate start).

## 5. Nodes and edges the mapping produces

Every node and edge below is the mapping of fields on one event. Edge names are ontology names; the legacy names (`contains`, `hasCall`, `realizes`, `fromState`, `toState`) are not produced.

### Call nodes and their ids

One node per element: `execution_*` (`AgentCall`), `llm_call_*`, `tool_call_*`, `processing_call_*`, `memory_call_*`, `mas_call_*`, and so on. Its id is `{type lowercased}:{run_id}:{call_id}` and both records of the call carry the same one, so they merge. A `delegate_to_*` tool call is an ordinary `ToolCall` of the caller, and the callee's visit an ordinary `AgentCall`: there is no resumed visit and no sibling rewriting.

The MASCall of a session is the one no call started (no `parent_call_id`): its id is `mascall:{run_id}`, so every AgentCall can name it. A MAS call that a tool call started (a nested MAS) gets `mascall:{run_id}:{call_id}`.

### Session

- **Session**: one per distinct `run_id`, named in `finalize()`. The `id`/`sessionId` is `session-{run_id}`, except: with `split_session_id` (default) a `<appName>_<uuid>` run id gives `sessionId = <uuid>` and `appName = <appName>`, a hierarchical run id containing `/` is used as the session id with the first path segment as `appName`, and a plain run id takes the `session_id` the events carry (the smallest, when they differ) unless another run carries the same one. `startTime`, `endTime`, `success`, `inputQuery` (`state_in_content`) and `finalResponse` (`state_out_content`) come from the session's own MAS call (`mas_call_start` / `mas_call_end`); without one they are absent.

### Containment

Containment is what the records say, with the ontology names:

- `hasMASCall` (`Session` -> `MASCall`) from every `mas_call_*` record.
- `hasAgentCall` (`MASCall` -> `AgentCall`) and `belongsToMASCall` (`AgentCall` -> `MASCall`) for every `AgentCall`, a delegated visit too: its `parent_call_id` names the tool call that started it, but agent calls are contained by the MASCall of the session.
- `hasLLMCall`, `hasToolCall`, `hasProcessingCall`, `hasMemoryCall` (a `MemoryCall` or a `RAGQuery`), `hasSkillCall` from the AgentCall the record names in `parent_call_id`. The edge exists when that id is an agent visit that arrives; a call whose parent is another call, or never arrives, gets none. A `ProcessingCall` is contained by whatever call it names: `hasProcessingCall` from an `AgentCall` or `MASCall`, `containsCall` from any other call (a check interval brackets an LLM or tool call). The `call_id` of an interval annotation (`state_update_*`, `wait_state_*`) is its own key; the annotation `annotates` its `parent_call_id`. The `delegate` / `reply` steps name the caller's visit (`agent_id` is the caller).

### State and Transition nodes (`core/state_transition.py`)

The runtime says what flows through the system. `execution_start` / `execution_end`, `llm_call_*`, `tool_call_*`, `memory_call_*`, `processing_call_*` and `mas_call_end` carry `state_in`, `state_in_content`, `state_out` and `state_out_content` ([native-telemetry.md](native-telemetry.md#state-identity)). The fold maps them with no knowledge of any other record:

- A **State** node per id, `id` = `state:{run_id}:{state_id}`, with `stateNodeId`, `content`, `contentHash` (SHA-256 of the content), `sessionId`, `layer = "normalized"`. The same id on two records is one State; non-empty content beats empty and two different non-empty contents keep the smaller one and log a warning. `content` is the full text, not truncated.
- One **Transition** per element, `id` = `trans:{element node id}`, with `transitionId`, `realizesCallId`, `transitionTimestamp` (the earliest record's timestamp), `sessionId`, `layer`.
- Per element: `hasInitialState` and `hasState` to each input State (several for a join: `state_in` is a list and `state_in_content` a JSON array of the same length and order), `hasFinalState` and `hasState` to the output State, `inputTo` (each input State -> Transition), `leadsTo` (Transition -> output State), `correspondsToTransition` (element -> Transition) and `representsExecution` (Transition -> element).
- Which field is which: on `execution_start`, `state_out` is the visit's first State (its initial State); on `execution_end`, `state_in` = `state_out` is the visit's final State; on `mas_call_end`, `state_in` is the root visit's first State and `state_out` the last root visit's final State; on every other element, `state_in` is read and `state_out` written. A visit's start and end records are two halves of one element: the start gives the initial State and the Transition, the end the final State.
- The **Session** and its **MASCall** both get `hasInitialState` / `hasFinalState` / `hasState` to the same State nodes `mas_call_end` names (the ontology requires a Session's boundary States to be shared with another element) and each its own Transition.
- A record without state fields gets no State and no Transition. There is no placeholder content.
- Delegation chains through the caller's `delegate` and `reply` ProcessingCalls: the `delegate` step's output State is the callee's initial State, and the `reply` step's output State is the delegating tool call's output State. A parallel group's `aggregate` join is one ProcessingCall whose input States are the members' outputs.

### Catalog nodes

Catalog nodes come from the same records, with deterministic ids. `Agent` (`id` = `agent_id`, from `execution_*`), `LLM` (`catalog:model:{model}`, from `llm_call_start`, with the provider when `model` is `provider/name`), `Tool` (`catalog:tool:{tool_name}`), `Processing` (`catalog:processing:{processing_name}`), `Skill` (`catalog:skill:{skill_name}`), `Memory` (`catalog:memory:{name}`), `Worker` (`worker:{worker_id}`) and `MAS` (`catalog:mas:{mas_id}`, else `app_name`). Characters an IRI cannot hold are written `_` in these ids. Call nodes link to them by `executesAgent`, `executesLLM`, `executesTool`, `executesProcessing`, `executesMAS`, `executesSkill` and `executesMemory`; the generic `executes` is not written (it is their super-property, read through RDFS inference). `Session` gets `executesSession` to the `MAS`; `Agent` gets `belongsToMAS` to the `MAS`, `usesLLM` to each `LLM` its calls ran and `usesTool` to each `Tool`. A record that does not name the thing (no `model`, no `tool_name`, no `mas_id` or `app_name`) gives no node and no edge.

### Annotations

An annotation node is created from its own record. `annotates` (annotation -> call) uses the call id the record names; a `ContextContribution` `annotates` the `LLMCall` in `llm_call_id` (else the call in `parent_call_id`). An annotation carries `eventKind`, `eventTime` and a JSON `payload`; the SCOPE properties of its subclasses are in [ontology.md](ontology.md#9-governance-layer). A `Worker` from `infrastructure_info` is the target of `runsOn` from the call in `parent_call_id`. A target that is not in the trace gives no edge. No call is looked up by time.

## 6. Node attributes reference

Attribute names are camelCase. Which attributes exist depends on which events carried the source field; absent source fields mean absent attributes (or the defaults noted).

### Common to all nodes

| Attribute | Notes |
|---|---|
| `node_type`, `@type` | Class name. |
| `id` | See [section 9](#9-node-id-stability). |
| `canonicalId` | `urn:mas:session:{sessionId}:call:{callId}` (calls), `...:annotation:{annotationId}`, `...:state:...`, `...:transition:...`; `urn:mas:session:{id}` for Session, `urn:mas:global:{type}:{slug}` for catalog nodes. |
| `name` | Filled from `name` / `agentName` / `masName` / `llmName` / `toolName` / `processingName` / `skillName` / `appName` / `agentId`, else `"{Type} {id}"`; `State` and `Transition` have an empty name; a `MASCall` is `MASCall {session id}`. |

### Call nodes (`MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `SkillCall`, `MemoryCall`, `RAGQuery`)

| Attribute | Source |
|---|---|
| `callId`, `executionId` | `event.call_id`; `exec-{Type}-{callId}` |
| `agentId` | `event.agent_id` |
| `parentCallId` | `parent_call_id` of the start record; absent for roots |
| `kindBase` | event kind without `_start` / `_end` |
| `masUri`, `spanLevel` | ontology lookup (`mas:` IRI, `spanLevel` annotation) |
| `runId`, `sessionId` | `run_id`; session id of the run |
| `startTime`, `endTime` | `event.timestamp` of the start / end event (epoch seconds) |
| `status` | end event `status`, default `success`; absent while the call has no end |
| `sourceRecordIds` | sorted `event_id`s |
| `success` | from the end record's status; absent while the call has no end |
| `duration` | milliseconds, from `startTime` and `endTime` when both are present |

### Class-specific attributes

| Node | Attributes |
|---|---|
| `Session` | `sessionId`, `executionId` (`exec-Session-{run_id}`), `runId`, `appName`, `inputQuery`, `finalResponse`, `startTime`, `endTime`, `duration`, `success` (the last five from the session's MAS call) |
| `Agent` | `agentId`, `declared`, `description` |
| `MASCall` | `masName` (`mas_name`), `masType` |
| `AgentCall` | `agentName`, `agentType`, `agentSequence` (optional), `inputContent`, `outputRaw` (`execution_end.output`) |
| `LLMCall` | `llmName`, `modelName`, `provider` (`vertex_ai/gemini` splits to provider + model; else `unknown`), `prompt`, `completion`, `toolCalls` (structured list), `promptTokenCount`, `completionTokenCount`, `totalTokenCount`, `cacheReadTokenCount`, `finishReason`, `responseId`, `temperature` |
| `ToolCall` | `toolName`, `toolCallId`, `toolArguments` (JSON string), `toolOutput`, optional `barrierId` |
| `ProcessingCall` | `processingName`, `processingType`, `inputContent`, `processingOutput` |
| `SkillCall` | common call attributes only |
| `MemoryCall`, `RAGQuery` | common call attributes plus `memoryOperation`, `memoryQuery`, `resultCount`; `RAGQuery` also `topK` |
| `CallAnnotation` | `eventKind` (the event kind), `eventTime` (its timestamp), `sessionId`, `payload` (JSON of the `source_agent_id`, `target_agent_id`, `task`, `correlation_id`, `status`, `segments`, `total_tokens`, `operation`, `target`, `from`, `to`, `message_type`, `denied_call_id`, `budget_scope`, `amount`, `intervention_type` fields the record carries) |
| `GovernanceEvent` | as `CallAnnotation`, plus `decision`, `policyName` (`policy_name`, else `policy_id`), `decisionReason` (`reason`) |
| `ContextContribution` | `partId`, `contextSource` (`source`), `contributionMechanism` (`access_mechanism`), `tokenEstimate`, `retained`, `eventKind`, `eventTime`, `sessionId`, `payload` (JSON of `parents`, `section_id`, `source_type`, `cause`, `cause_type`, `sensitivity`, `eviction_reason`, `content`). The `id` is `cpr-{part_id}`. |
| `State` | `stateNodeId`, `contentHash`, `content`, `sessionId`, `layer` |
| `Transition` | `transitionId`, `realizesCallId`, `transitionTimestamp`, `sessionId`, `layer` |
| `Tool`, `LLM`, `Skill`, `Memory`, `Processing` (catalog) | `name`, `declared`; `LLM` adds `provider`, `Processing` adds `processingType`, `Memory` adds `memoryType` |
| `MAS` | `name`, `declared` |
| `Worker` | `name`, `processId`, `durableBackend` |

A catalog node has no `description`: the fold does not copy the name into it.

## 7. Edge type reference

The builder writes the ontology names directly; `finalize()` does not rename edges. The output document contains these edge types.

| Edge type | From -> To | Origin |
|---|---|---|
| `hasMASCall` | `Session` -> `MASCall` | `mas_call_*` |
| `hasAgentCall` | `MASCall` -> `AgentCall` | `execution_*` |
| `hasLLMCall`, `hasToolCall`, `hasProcessingCall`, `hasMemoryCall`, `hasSkillCall` | `AgentCall` -> contained call (named by the contained type; a `RAGQuery` is contained by `hasMemoryCall`) | `parent_call_id` of the contained call's record |
| `belongsToMASCall` | `AgentCall` -> `MASCall` | `execution_*` |
| `belongsToMAS` | `Agent` -> `MAS` | `execution_*` (`mas_id`) |
| `executesSession` | `Session` -> `MAS` | `mas_call_*` |
| `executesMAS`, `executesAgent`, `executesLLM`, `executesTool`, `executesProcessing`, `executesSkill`, `executesMemory` | call -> `MAS` / `Agent` / `LLM` / `Tool` / `Processing` / `Skill` / `Memory` | `mas_id`, `agent_id`, `model`, `tool_name`, `processing_name`, `skill_name`, `memory_name` / `collection` / `memory_type` |
| `runsOn` | call -> `Worker` | `infrastructure_info` under `parent_call_id` |
| `hasInitialState`, `hasFinalState`, `hasState` | call, `Session`, `MASCall` -> `State` | `state_in` / `state_out` |
| `inputTo` | `State` -> `Transition` | `state_in` (initial States of the element) |
| `leadsTo` | `Transition` -> `State` | `state_out` (final State of the element) |
| `representsExecution` | `Transition` -> element | one per element with States |
| `correspondsToTransition` | element, `Session`, `MASCall` -> `Transition` | one per element with States |
| `annotates` | `CallAnnotation` and subclasses -> call | `call_id` / `parent_call_id` of the annotation record; a `ContextContribution` uses `llm_call_id`, else `parent_call_id` |
| `usesLLM`, `usesTool` | `Agent` -> `LLM` / `Tool` | `llm_call_start`, `tool_call_*` |

Every edge type above is a SCOPE object property, and every node type a SCOPE class (`tests/kg/native/test_scope_vocabulary.py`). Neither path post-processes a graph after it is built; `restrict_graph` is the native fold's own SCOPE attribute projection.

## 8. Categories and control

The default output holds the core classes only: `Session`, `MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `State`, `Transition` and the `MAS` / `Agent` / `LLM` / `Tool` / `Processing` catalog. `categories` adds opt-in classes on top. A category never changes a core node: an event whose class belongs to a disabled category is dropped at the class lookup (`CATEGORY_CLASSES` in `core/event_mappings.py`), in the batch and the realtime fold alike, so the core graph is the same with or without such events in the input. The enabled set is recorded in `metadata.categories`.

| Category | Classes it adds | Event kinds | Default |
|---|---|---|---|
| `capabilities` | `Skill`, `SkillCall`, `Memory`, `MemoryCall`, `RAGQuery`; `executesSkill`, `executesMemory`, `usesSkill`, `usesMemory`, `hasSkillCall`, `hasMemoryCall` | `skill_execution_*`, `memory_*`, `rag_query_*` | off |
| `annotations` | `CallAnnotation`, `GovernanceEvent`, `ContextContribution`; `annotates` | `routing*`, `context_assembled`, `state_update_*`, `agent_communication_*`, `observability_*_execute_*`, `user_input` / `user_output`, `client_response`, `tool_result_injected`, `boundary_*`, `context_steer`, `compaction`, `wait_state_*`, `context_part_contributed`, and the `GovernanceEvent` kinds listed in [section 4](#4-event-kg-mapping-table) | off |
| `worker` | `Worker`; `runsOn` | `infrastructure_info` | off |
| `thinking` | `ThinkingCall`; `hasThinkingCall` | `thinking_start` / `thinking_end` | off |

`all` enables every category; the value is a list or a comma-separated string, and an unknown name raises `ValueError` (`click.UsageError` on the CLI). `all_attributes` (default `False`) keeps only SCOPE model fields on core nodes; native extras (`callId`, `agentId`, ...) need `all_attributes=True`. CLI: `mas-lab kg normalize --categories capabilities,annotations`. The names are those of the observe-sdk categories, which are off by default there too; `norm` itself has no category gate (it maps every span suffix it is given), so on the OTel path the gate is the observe-sdk's switches.

### 8.1 Terminology: layer, `block`, ontology `layer`

Three similarly named things exist.

- **Categories** (above): runtime filters over the classes of the output.
- **`block`** attribute on nodes: coarse partition used by validation. Allowed values are `structural`, `execution`, `trajectory`, `governance`. `check_block_vocabulary` expects `structural` for `StructuralElement` subclasses, `trajectory` for `TrajectoryElement` subclasses and `execution` for everything else. Catalog nodes carry `block = "structural"`; `GovernanceEvent` nodes set `governance`. The Neo4j push fills a missing `block` per node type (see [neo4j.md](neo4j.md)).
- **`layer`** attribute: only `TrajectoryElement` classes (`State`, `Transition`) carry it, with the single value `normalized` (`check_layer_vocabulary`).

### 8.2 Where classes are defined

| Source | Classes |
|---|---|
| `ioc-scope-ontology` (`mas-ontology.ttl`) | every class the fold emits: `Session`, `MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `SkillCall`, `MemoryCall`, `RAGQuery`, `State`, `Transition`, `CallAnnotation`, `GovernanceEvent`, `ContextContribution`, `MAS`, `Agent`, `LLM`, `Tool`, `Processing`, `Skill`, `Memory`, `Worker` |

MAS Lab declares no class. Normalization resolves the ontology through `mas.library.kg.ontology`, which loads `mas-ontology.ttl` from `ioc-scope-ontology`. If `ioc-scope-ontology` or `rdflib` cannot be loaded, `build_kg_document` logs a warning and continues without the ontology-derived `masUri` / `spanLevel` enrichment.

## 9. Node ID stability

Ids are deterministic from what an event carries, so re-normalizing the same trace is idempotent, any order of the events gives the same ids, and Neo4j `MERGE` deduplicates.

| Node type | ID pattern | Example |
|---|---|---|
| Call nodes (`AgentCall`, `LLMCall`, `ToolCall`, ...) | `{type lowercased}:{run_id}:{callId}` | `agentcall:demo:tp-exec-001` |
| `MASCall` | `mascall:{run_id}` (the session's own), `mascall:{run_id}:{callId}` (a nested MAS) | `mascall:demo` |
| `CallAnnotation`, `GovernanceEvent` | `ann-{uuid5(run_id\|agent_id\|kind\|timestamp)[:16]}` | `ann-a83e9a4a96ac55ae` |
| `ContextContribution` | `cpr-{part_id}` (fallback: the annotation hash) | `cpr-9bb51fd9-...` |
| `State` | `state:{run_id}:{state id the runtime wrote}` | `state:demo:planner-u1-exec#3` |
| `Transition` | `trans:{element node id}` | `trans:llmcall:demo:tp-llm-001` |
| `Session` | see [section 5](#5-nodes-and-edges-the-mapping-produces) | `session-demo` |
| `Agent` | the `agent_id` string | `itinerary_agent` |
| `Tool`, `Skill`, `Memory`, `Processing` catalog | `catalog:tool:{name}`, `catalog:skill:{name}`, `catalog:memory:{name}`, `catalog:processing:{name}` | `catalog:tool:search_flights` |
| `LLM` catalog | `catalog:model:{modelName}` | `catalog:model:gemini-2.5-flash` |
| `MAS` | `catalog:mas:{mas_id or app_name}` | `catalog:mas:trip-planner` |

A State is scoped to its run (the runtime's state ids repeat from run to run), so two runs never share a State node.

Every node also carries a `canonicalId` URN; edges carry `id = "{edge_type}:{from_id}->{to_id}"` and a `canonicalId` of the form `urn:mas:session:{session}:edge:{type}:{sha1[:16]}`.

### Content hash versus identity

`State.contentHash` is the SHA-256 of the state content. It is distinct from the node id: the same content in two sessions gives the same `contentHash` and different ids, so embeddings can be reused by content hash without merging state identities across traces.

### One node per call id

Start and end records of a call merge because they carry the same `call_id`. The id does not include the agent: a call id names one call.

For the ontology of these types, see [ontology.md](ontology.md). For the pipeline steps that wrap normalization (`run_normalize`, `run_normalize_otel`), see [steps.md](steps.md). For Neo4j push and query patterns, see [neo4j.md](neo4j.md).

# Native telemetry reference: `events.jsonl`

`events.jsonl` is the native trace format of MAS Lab: a newline-delimited JSON file where each line is one observability event emitted by the multi-agent system (MAS) runtime. `mas.library.kg` normalizes it into a knowledge graph (KG). Read this page to emit or hand-write valid events, to see which fields the normalizer reads, and to validate a trace.

Smallest example: validate a trace, then build the graph.

```bash
mas-lab kg verify events.jsonl --strictness recommended
mas-lab kg normalize events.jsonl --run-id demo -o kg.jsonld
```

See also: [normalization.md](normalization.md), [steps.md](steps.md#run_verify_events), [ontology.md](ontology.md), and [otel-telemetry.md](otel-telemetry.md) (the OpenTelemetry input path).

## Table of contents

1. [Overview](#1-overview)
2. [File format](#2-file-format)
3. [Global envelope (L1)](#3-global-envelope-l1)
4. [Interval events: start/end pairing (L2)](#4-interval-events-startend-pairing-l2)
5. [Kind reference](#5-kind-reference)
6. [Per-kind payload reference](#6-per-kind-payload-reference)
7. [Pairing rules and validation](#7-pairing-rules-and-validation)
8. [Session ID convention](#8-session-id-convention)
9. [Adding new event kinds](#9-adding-new-event-kinds)

## 1. Overview

Each line of `events.jsonl` is one event emitted by the runtime's observability plugin (`runtime/src/mas/runtime/boundary/obs/observability_plugin.py`). `run_normalize` (CLI: `mas-lab kg normalize`) converts the stream into a typed KG (`kg.jsonld`).

The format has three validation levels, each building on the previous. `EventValidator` reports them; they are defined in `schemas/events.spec.yaml`:

| Level | Name | Scope |
|-------|------|-------|
| L1 | Envelope | Fields present on every line (`kind`, `timestamp`, `run_id`, `agent_id`) |
| L2 | Interval | `call_id` (and recommended `parent_call_id`) on every `*_start` / `*_end` event |
| L3 | Semantic | Per-kind payload fields the spec requires or recommends |

Normalization runs in two phases ([normalization.md](normalization.md) has the details):

1. **Annotation** (`normalize_events` in `core/graph_builder.py`): drops events of disabled layers, maps each event's `kind` to an ontology class through `KIND_TO_CLASS` (`core/event_mappings.py`), and stamps `mas_class`, `mas_uri`, `run_id` and `call_id` on the event.
2. **Graph extraction** (`extract_graph`, which drives `NativeGraphBuilder` in `core/native_graph_builder.py`): dispatches each event to a per-class handler in `observability/native/handlers/`, upserts one node per `call_id` from the start/end pair, then runs post-passes (annotation edges, session nodes, containment edges, State/Transition synthesis).

### Native normalization contract

Native normalization is mechanical. The runtime should emit explicit values for:

- `run_id`
- `agent_id`
- `call_id` on every `*_start` / `*_end` event
- `parent_call_id` when a call is nested under another call
- the payload fields listed per kind below

Two small inference steps remain for traces that omit `parent_call_id`: a `routing` event that names a source and a target agent supplies the `parent_call_id` of the target agent's next `execution_start` (from the source agent's currently open `execution_start`), and the containment pass adds `contains` edges by timestamp enclosure.

### Unknown and suppressed kinds

- A kind that is absent from `KIND_TO_CLASS` is an observability gap. By default (`strict=False`) the normalizer logs a deduplicated warning and drops that event; with `strict=True` it raises `UnknownSpanBoundaryError`.
- A kind explicitly mapped to `None` is suppressed and silently skipped (see [5.6](#56-suppressed-kinds)).
- A structural event (anything that forms a call-tree node) without a `call_id` is dropped the same way (warning, or `MissingCallIdError` when strict). `ProcessingCall` events without a `call_id` are given a synthetic key `<agent_id>:<parent_call_id>:<processing_type|processing_name|part_id|kind>`.
- Extra fields on any event are tolerated (the JSON Schema sets `additionalProperties: true`); the normalizer reads only the fields listed in [Section 6](#6-per-kind-payload-reference).

## 2. File format

Format rules:

- One JSON object per line, UTF-8.
- Blank lines are ignored.
- `kind` and `run_id` are strings; `timestamp` is Unix epoch seconds as a number.
- The graph builder processes events in arrival order. Containment, annotation and routing inference use `timestamp` and arrival order together, so emit events in non-decreasing `timestamp` order.

A start/end pair:

```json
{"kind":"execution_start","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","session_id":"trip-planner/demo/baseline/item1/r1","boundary":"AgentCall","timestamp":1716200001.0,"input":"Plan a 3-day trip to Paris."}
{"kind":"execution_end","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","session_id":"trip-planner/demo/baseline/item1/r1","status":"success","output":"Trip planned: AF101 NYC to CDG.","timestamp":1716200009.5}
```

Real traces: `library-kg/examples/qa-agent/events.jsonl` (emitted by the runtime) and `library-kg/examples/trip-planner/events.jsonl` (hand-written). Both ship with an empty or absent `run_id`, so `mas-lab kg verify` reports `missing required field 'run_id'` on them; pass `--run-id` to `mas-lab kg normalize` to normalize them.

## 3. Global envelope (L1)

### Required fields

`events.spec.yaml` and `events.schema.json` require these four on every line:

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `string` | Event kind. Must be a key of `KIND_TO_CLASS` to reach the graph (see [Section 5](#5-kind-reference)). |
| `timestamp` | `number` | Unix epoch seconds; use a float for sub-second precision. |
| `run_id` | `string` | Run identifier grouping the events of one trace. The `run_id` passed to `run_normalize` / `--run-id` overrides this value on every event. |
| `agent_id` | `string` | Agent emitting the event. Used for agent attribution, containment inference and routing-based parent inference. |

### Recommended fields

Reported as warnings when `--strictness recommended` (or `complete`) is used:

| Field | Type | Description |
|-------|------|-------------|
| `mas_id` | `string` | Multi-agent system identifier. |
| `session_id` | `string` | Free-form session identifier (see [Section 8](#8-session-id-convention)). |
| `block` | `string` | Observability block, one of `structural`, `execution`, `context`, `trajectory`, `governance` (the schema enum). A coarse grouping of what the event describes. |
| `layer` | `string` | One of `structure`, `execution`, `semantic`, `provenance`, `governance` (the schema enum). Not the same thing as the optional normalization layers of [Section 5](#5-kind-reference). |
| `summand` | `string` | One of `model`, `tool`, `context`, `governance`, `orchestrator` (the schema enum): which component of the runtime's observability state machine produced the event. |
| `mealy_symbol` | `string` | Label of the runtime state-machine transition the event records, for example `RUN_START`, `AGENT_START`, `LLM_CALL`, `POLICY_CHECK`. Free-form in the schema; the normalizer does not read it. |

`block`, `layer`, `summand` and `mealy_symbol` are set by the runtime and are not read by the native normalizer.

## 4. Interval events: start/end pairing (L2)

Interval events are kinds whose name ends in `_start` or `_end`. They model one timed operation (a tool call, an LLM call, an agent turn) as a pair that the normalizer merges into one KG node:

- `*_start` creates the node and sets `startTime` plus start-phase payload.
- `*_end` updates the same node with `endTime`, `status` (default `success` when absent) and end-phase payload.

### Pairing contract

| Rule | Detail |
|------|--------|
| Same `call_id` | Both halves carry the identical `call_id`. This is the join key. It is a string, not necessarily a UUID. |
| Same base kind | The part before `_start`/`_end` must match. A `call_id` shared by different bases is reported by `check_interval_pairing` as `mixed interval bases`. |
| `call_id` required | The JSON Schema and `EventValidator` require `call_id` on every `*_start` and `*_end`. The normalizer drops a structural event without one. |
| `parent_call_id` recommended | Links the call to its enclosing call. Omit it (or set it to `null`) for root calls such as `mas_call_start`. |

### Resilience behaviour

Behaviour of `NativeGraphBuilder.upsert_call` (`core/native_graph_builder.py`):

| Scenario | Behaviour |
|----------|-----------|
| Start arrives before end | Normal case. |
| End arrives before start | The node is created from the end event (no `startTime`); `startTime` is set when the start arrives. A node with no `startTime` or no `endTime` gets no inferred `contains` edges. |
| Start with no matching end | The node keeps `startTime` and has no `endTime` (open call). |
| End with no matching start | The node is created from the end alone. |
| Second start while the first call is still open | The second start becomes a `CallAnnotation` point node; the original node is not overwritten. |
| Duplicate end | Last writer wins on `endTime`, `status` and output fields. |

## 5. Kind reference

`KIND_TO_CLASS` in `library-kg/src/mas/library/kg/core/event_mappings.py` has 85 entries: 73 map to an ontology class (tables below) and 12 map to `None` (suppressed, [5.6](#56-suppressed-kinds)). Class names are local names in the SCOPE ontology (`oxp-ontology`; see [ontology.md](ontology.md)).

**Optional layers.** `LAYER_KINDS` groups some kinds into four layers that `normalize_events` can drop. Defaults: `trajectory` on; `infrastructure`, `provenance`, `governance` off. CLI flags: `--include-infrastructure`, `--include-trajectory`, `--include-provenance`, `--include-governance` (each with a `--no-` form); Python: the same `include_*` keyword arguments of `run_normalize`. A dropped layer produces no nodes for its kinds.

| Layer | Kinds | Default |
|-------|-------|---------|
| `infrastructure` | `infrastructure_info` | off |
| `trajectory` | `parallel_group_*`, `branch_*`, `routing`, `routing_result` | on |
| `provenance` | `context_part_contributed` | off |
| `governance` | `audit`, `policy_denial`, `policy_allow`, `budget_event`, `transformation_event`, `control_intervention`, `hitl_gate`, `hitl_request`, `governance_denied`, `governance_checked`, `governance_decision`, `governance_event`, `governance_policy`, `governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_authorize_*`, `obs_wrap_gov_validate_*` | off |

The "Spec required" and "Spec recommended" columns below come from `events.spec.yaml` (what `mas-lab kg verify` checks). Every `*_start` / `*_end` kind additionally requires `call_id` (L2) and recommends `parent_call_id`. The "Normalizer reads" details are in [Section 6](#6-per-kind-payload-reference).

### 5.1 Call-tree kinds (start/end pairs)

| Kind | KG class | Spec required (L3) | Spec recommended (L3) |
|------|----------|--------------------|------------------------|
| `execution_start` | `AgentCall`, or `TaskCall` when `boundary="TaskCall"` or `agent_id` ends in `.task` | `input` | `context` |
| `execution_end` | same as its start | `status` | `output`, `output_parts` |
| `llm_call_start` | `LLMCall` | `model` | `messages`, `temperature`, `max_tokens` |
| `llm_call_end` | `LLMCall` | | `latency_ms`, `response`, `tool_calls`, `output_parts`, `finish_reason`, `tokens_used` |
| `tool_call_start` | `ToolCall` | `tool_name` | `arguments` |
| `tool_call_end` | `ToolCall` | `tool_name` | `latency_ms`, `result`, `output_parts`, `status` |
| `network_call_start` / `_end` | `ToolCall` | | |
| `mas_call_start` / `_end` | `MASCall` | | `mas_id`, `session_id` |
| `rag_query_start` / `_end` | `RAGQuery` | | |
| `memory_call_*`, `memory_store_*`, `memory_retrieve_*` | `MemoryCall` | | |
| `processing_call_start` / `_end` | `ProcessingCall` | | |
| `workflow_transition_start` / `_end` | `ProcessingCall` (no dedicated class) | | |
| `skill_execution_start` / `_end` | `SkillCall` | | |
| `parallel_group_start` / `_end` | `ParallelGroup` | | |
| `branch_start` / `_end` | `Branch` | | |

### 5.2 Annotation kinds (point events)

Annotation events become `CallAnnotation` nodes. They do not form call-tree nodes: each line (including each half of a `*_start`/`*_end` annotation pair) becomes its own annotation node, linked by an `annotates` edge to the tightest enclosing call of the same agent by timestamp.

| Kind | Notes |
|------|-------|
| `routing`, `routing_result` | Delegation between agents (trajectory layer); `routing` also feeds parent inference. |
| `context_assembled` | Context window assembled before an LLM call. |
| `state_update_start` / `_end` | Context state mutation. |
| `agent_communication_start` / `_end` | Agent-to-agent message. |
| `checkpoint_start` / `_end` | Checkpoint; the node gets `block="governance"`. |
| `observability_pre_execute_start` / `_end`, `observability_post_execute_start` / `_end` | Runtime wrapper checkpoints around an operation. |
| `user_input`, `user_output`, `client_response` | User-facing I/O. |
| `tool_result_injected` | A tool result fed back into the context. |
| `boundary_ingress`, `context_steer`, `compaction`, `wait_state_start` / `_end` | Runtime markers. |

### 5.3 Provenance (optional layer)

`context_part_contributed` becomes a `ContextContribution` node (one per context part). Spec recommended: `part_id`, `source`, `content`, `access_mechanism`, `cause`, `retained`, `parents`. Off by default; enable with `--include-provenance`.

### 5.4 Infrastructure (optional layer)

`infrastructure_info` maps to class `Worker`. Spec recommended: `worker_id`, `endpoint`. The native handlers currently produce no node for it (there is no dispatch handler for `Worker`, and without a `call_id` the generic call handler skips it), so enabling the layer has no effect on the graph today.

### 5.5 Governance (optional layer)

All governance kinds (listed in the layer table above) map to class `GovernanceEvent` and are handled like annotations: a `GovernanceEvent` node with an `annotates` edge. The handler sets `annotationKind` and `block="governance"` for every one except `hitl_request` and the `obs_wrap_gov_*` kinds. Off by default; enable with `--include-governance`.

Note: `events.spec.yaml` requires `call_id` for every kind with a `governance_` prefix (including point kinds such as `governance_checked` and `governance_decision`), so `mas-lab kg verify` flags them when `call_id` is missing, although the normalizer does not need it.

### 5.6 Suppressed kinds

Mapped to `None` in `KIND_TO_CLASS`: skipped by the normalizer and by `EventValidator`, with no warning.

| Kind | Reason |
|------|--------|
| `prompt_build_start`, `prompt_build_end` | Redundant with `llm_call_start.messages`. |
| `user_response` | Redundant with `execution_end.output`. |
| `human_interaction`, `delegated_agent_output` | Superseded; no mapping. |
| `skills_check` | Legacy governance extension. |
| `object_upsert`, `object_link`, `object_model_event` | Domain-KG extension only. |
| `user_input_request` | Not a call node. |
| `contract_call_start`, `contract_call_end` | Runtime wrapper spans; no KG representation. |

## 6. Per-kind payload reference

This section lists what the native handlers in `observability/native/handlers/` read. Fields the spec recommends but the handlers do not read are called out. Examples use the trip-planner scenario (`run_id` `r1`, agents `mas`, `planner`, `itinerary_agent`) and are illustrative.

**Common to every call-tree node** (`NativeGraphBuilder.upsert_call`): `call_id` -> `callId`, `agent_id` -> `agentId`, `parent_call_id` -> `parentCallId`, `run_id` -> `runId`, start `timestamp` -> `startTime`, end `timestamp` -> `endTime`, end `status` -> `status` (default `success`), and `event_id` (when present) -> `sourceRecordIds`.

### 6.1 `execution_start`

Creates an `AgentCall` (or `TaskCall`, see [5.1](#51-call-tree-kinds-startend-pairs)) node.

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `input` / `payload` | `inputContent` | AgentCall only. |
| `agent_type` | `agentType` | AgentCall only. |
| `agent_sequence` | `agentSequence` | AgentCall only; converted to int. |
| `agent_id` | `agentName` | AgentCall only. |
| `task_name`, `task_type` | `taskName`, `taskType` | TaskCall only. |
| `boundary` | node class | `"TaskCall"` selects `TaskCall`; anything else `AgentCall`. |
| `parent_call_id` | `parentCallId` | If absent, taken from a preceding `routing` event addressed to this agent. |

`context` is spec-recommended but not read by the handler.

```json
{"kind":"execution_start","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","boundary":"AgentCall","timestamp":1716200001.0,"input":"Plan a 3-day trip to Paris with flight booking and hotel."}
```

### 6.2 `execution_end`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `status` | `status` | Spec-required. Free-form string (the shipped examples use `success` and `ok`). |
| `output` / `payload` | `outputRaw` | AgentCall only. Rendered output (`outputContent`, `outputParts`) is derived afterwards from the call's children. |

The handler pairs the end with the agent's currently open `execution_start`, even when `call_id` differs.

```json
{"kind":"execution_end","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","status":"success","output":"Trip planned: AF101 NYC to CDG.","timestamp":1716200009.5}
```

### 6.3 `llm_call_start`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `model` | `modelName`, `provider` | Spec-required. `"provider/name"` is split on the first `/`; otherwise `provider` is `unknown`. |
| `llm_name` / `model` | `llmName` | |
| `input` / `prompt` / `messages` | `prompt` | `messages` is rendered as `[ROLE]\ncontent` blocks. |

`temperature` and `max_tokens` are spec-recommended but not read by the handler. Token counts and `finishReason` start at `0` / empty and are filled by the end event.

```json
{"kind":"llm_call_start","agent_id":"planner","call_id":"tp-llm-001","parent_call_id":"tp-exec-001","run_id":"r1","model":"vertex_ai/gemini-2.5-flash","timestamp":1716200002.0,"messages":[{"role":"system","content":"You are a travel planning agent."},{"role":"user","content":"Plan a 3-day trip to Paris."}],"temperature":0.7,"max_tokens":1024}
```

### 6.4 `llm_call_end`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `response.usage.prompt_tokens` / `input_tokens` | `promptTokenCount` | |
| `response.usage.completion_tokens` / `output_tokens` | `completionTokenCount` | |
| `response.usage.total_tokens` | `totalTokenCount` | |
| `response.usage.cache_read_input_tokens` | `cacheReadTokenCount` | |
| `response.model`, `response.id` | `modelName`, `responseId` | Only fill gaps left by the start event. |
| `response.choices[0].finish_reason` | `finishReason` | |
| `response.choices[0].message.{content,tool_calls}` or flat `response.{content,text,tool_calls}` | `completion`, `toolCalls` | Tool-use-only responses are rendered as `-> name(args)` lines. |
| `response.thinking` | child `ThinkingCall` node | Linked from the LLM call by a `hasThinking` edge. |
| `content` / `output` (flat) | `completion` | Used when there is no `response` object. |

Spec-recommended `latency_ms`, `finish_reason` and `tokens_used` at the top level of the event are not read by the handler; token counts must be under `response.usage` to reach the graph. The runtime's own traces (see `examples/qa-agent`) carry a flat `output` and a top-level `tokens_used` object instead.

```json
{"kind":"llm_call_end","agent_id":"planner","call_id":"tp-llm-001","parent_call_id":"tp-exec-001","run_id":"r1","status":"success","timestamp":1716200003.5,"latency_ms":1500,"response":{"id":"resp-abc123","model":"gemini-2.5-flash","choices":[{"message":{"role":"assistant","content":"Delegating to itinerary_agent."},"finish_reason":"stop"}],"usage":{"prompt_tokens":80,"completion_tokens":18,"total_tokens":98}}}
```

### 6.5 `tool_call_start` / `network_call_start`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `tool_name` | `toolName` | Spec-required on `tool_call_*`. |
| `tool_call_id` | `toolCallId` | Provider-assigned id. |
| `tool_arguments` / `arguments` / `parameters` | `toolArguments` | First present, JSON-encoded. |

**Delegation tools.** A tool named `delegate_to_<agent>` is treated as a hand-off, not a tool call: by default no `ToolCall` node is created; the builder emits a `callsAgent` edge to `<agent>` and closes the caller's agent visit so the delegate appears as a sibling visit.

```json
{"kind":"tool_call_start","agent_id":"itinerary_agent","call_id":"tp-tc-001","parent_call_id":"tp-pg-001","run_id":"r1","tool_name":"search_flights","timestamp":1716200005.1,"arguments":{"origin":"NYC","destination":"CDG","date":"2026-07-01"}}
```

### 6.6 `tool_call_end` / `network_call_end`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `output` / `result` | `toolOutput` | `output` wins when both are present. |
| `status` | `status` | |

`tool_name` is spec-required on `tool_call_end` but the handler reads the name from the start event; `latency_ms` is not read.

```json
{"kind":"tool_call_end","agent_id":"itinerary_agent","call_id":"tp-tc-001","parent_call_id":"tp-pg-001","run_id":"r1","status":"success","tool_name":"search_flights","timestamp":1716200006.5,"result":{"flights":[{"id":"AF101","dep":"08:00","arr":"21:30","price_usd":680}]}}
```

### 6.7 `mas_call_start` / `mas_call_end`

Brackets the whole multi-agent system invocation, usually the root of the call tree. The start handler reads `mas_name` (default `agent_id`) -> `masName` and `mas_type` -> `masType`. `mas_id` and `session_id` are spec-recommended but not read. The `MASCall` (or, without one, the earliest `AgentCall`) supplies the `Session` node's `inputQuery`, times and final response.

```json
{"kind":"mas_call_start","agent_id":"mas","call_id":"tp-mas-001","run_id":"r1","timestamp":1716200000.0}
{"kind":"mas_call_end","agent_id":"mas","call_id":"tp-mas-001","run_id":"r1","status":"success","timestamp":1716200010.0}
```

### 6.8 `rag_query_*`, `memory_*`

These produce `RAGQuery` / `MemoryCall` nodes with only the common call-node fields. Payload fields such as `query_text`, `collection`, `retrieved_doc_count` or `results` are carried on the event but not copied onto the node by the native handlers.

```json
{"kind":"rag_query_start","agent_id":"itinerary_agent","call_id":"tp-rag-001","parent_call_id":"tp-pg-001","run_id":"r1","query_text":"Paris boutique hotels near Marais","collection":"hotels-europe","timestamp":1716200005.2}
{"kind":"rag_query_end","agent_id":"itinerary_agent","call_id":"tp-rag-001","parent_call_id":"tp-pg-001","run_id":"r1","status":"success","retrieved_doc_count":4,"timestamp":1716200006.8}
```

### 6.9 `processing_call_*`, `workflow_transition_*`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `processing_name` | `processingName` | Start. Defaults to the kind base. |
| `processing_type` | `processingType` | Start. |
| `input` / `payload` | `inputContent` | Start. |
| `messages` (end event) | `processingOutput` | Rendered as `role: content` lines; falls back to `output`. Context-assembly steps carry the assembled prompt in `messages`. |

### 6.10 `skill_execution_*`

Start reads `skill_name` (or `processing_name`) -> `skillName`, `input` / `payload` -> `skillInput`, `skill_version` -> `skillVersion`. End reads `output` / `result` -> `skillOutput` and `status`.

### 6.11 Point and annotation events

`routing`, `routing_result`, `context_assembled`, `state_update_*`, `agent_communication_*`, `checkpoint_*`, and the governance kinds are copied to an annotation node (`CallAnnotation` or `GovernanceEvent`) with `kind`, `agentId`, `timestamp`, `runId` plus any of these fields that are present:

| Event field | KG attribute |
|-------------|-------------|
| `source_agent_id`, `target_agent_id` | `sourceAgentId`, `targetAgentId` |
| `task`, `correlation_id`, `status` | `task`, `correlationId`, `status` |
| `segments`, `total_tokens` | `segments`, `totalTokens` |
| `operation`, `target`, `from`, `to` | same names |
| `message_type` | `messageType` |
| `policy_id`, `decision`, `reason` | `policyId`, `decision`, `reason` |
| `denied_call_id` | `deniedCallId` |
| `budget_scope`, `amount` | `budgetScope`, `amount` |
| `intervention_type` | `interventionType` |

Other fields (for example `hook`, `plugin`, `contract_id`, `details`, `content`) are not copied. For `routing`, when the source agent has an open `execution_start`, that call becomes the `parent_call_id` of the target agent's next `execution_start` that lacks one. An annotation whose `agent_id` is empty falls back to `source_agent_id` for the `annotates` match.

```json
{"kind":"routing","agent_id":"planner","run_id":"r1","source_agent_id":"planner","target_agent_id":"itinerary_agent","task":"Build a 3-day Paris itinerary.","correlation_id":"corr-001","timestamp":1716200003.9}
{"kind":"context_assembled","agent_id":"planner","run_id":"r1","total_tokens":512,"segments":3,"timestamp":1716200001.9}
{"kind":"governance_checked","agent_id":"planner","run_id":"r1","policy_id":"routing","decision":"allow","reason":"planner to itinerary_agent: found in declared edges","timestamp":1716200003.8}
```

### 6.12 `context_part_contributed`

Requires `--include-provenance`. Produces a `ContextContribution` node:

| Event field | KG attribute | Default |
|-------------|-------------|---------|
| `part_id` | `partId` | derived id |
| `parents` (or `provenance.parents`) | `parents`; `derivedFrom` edges | `[]` |
| `source`, `section_id` | `source`, `sectionId` | `""` |
| `source_type` | `sourceType` | `unknown` |
| `access_mechanism` | `accessMechanism` (and `mechanism`) | `inject` |
| `cause`, `cause_type` | `cause`, `causeType` | `context_manager`, `deterministic` |
| `token_estimate` | `tokenEstimate` | `0` |
| `retained` | `retained` | `true` |
| `eviction_reason` | `evictionReason` | `budget_exceeded` when `retained` is false |
| `sensitivity` | `sensitivity` | |
| `content` / `content_preview` | `content`, `contentPreview` (first 200 chars) | |
| `llm_call_id` | `contributesTo` edge to that `LLMCall` | without it, linked by agent and timestamp enclosure |

```json
{"kind":"context_part_contributed","agent_id":"itinerary_agent","run_id":"r1","part_id":"part-hotel-rag-001","source":"rag_result","section_id":"retrieved_context","access_mechanism":"vector_search","token_estimate":142,"retained":true,"content_preview":"Hotel du Marais: 195 EUR/night","llm_call_id":"tp-llm-002","parents":[],"timestamp":1716200007.1}
```

### 6.13 `parallel_group_*`, `branch_*`, `infrastructure_info`

`parallel_group_*` and `branch_*` produce `ParallelGroup` / `Branch` nodes with only the common call-node fields; `group_id`, `branch_count`, `branches` and `branch_name` stay on the event and are not copied to the node. `infrastructure_info` currently produces no node (see [5.4](#54-infrastructure-optional-layer)).

## 7. Pairing rules and validation

Validation is split in two, and the two parts are reached by different entry points.

| Entry point | What it runs |
|-------------|--------------|
| `mas-lab kg verify EVENTS --strictness required\|recommended\|complete` and `run_verify_events` (`steps/verify_events.py`) | `EventValidator` against `events.spec.yaml`: L1 envelope, L2 `call_id`, L3 per-kind fields. |
| `verify_events_file` / `verify_events` (`mas.library.kg.observability.native.verify`, Python only) | Everything above, plus `check_interval_pairing`, a single-run check, and the JSON Schema check (`events.schema.json`, needs the `jsonschema` package). |

### What `EventValidator` checks

| Rule | Severity | Description |
|------|----------|-------------|
| `global_required` | error | A required envelope field (`kind`, `timestamp`, `run_id`, `agent_id`) is missing or empty. |
| `interval_required` | error | A `*_start` / `*_end` event has no `call_id`. |
| `shape_required` | error | A per-kind required field is missing (for example `model` on `llm_call_start`; also `call_id` on kinds with the `memory_`, `rag_`, `processing_`, `skill_`, `network_`, `workflow_`, `agent_communication_`, `parallel_group_`, `branch_`, `checkpoint_`, `governance_` or `obs_wrap_gov_` prefix). |
| `global_recommended`, `interval_recommended`, `shape_recommended` | warning | Recommended fields, only with `--strictness recommended` or `complete` (the two behave identically today). |

A field counts as missing when it is absent, `null`, or an empty string. Kinds mapped to `None` and kinds absent from `KIND_TO_CLASS` are skipped without a violation, so a misspelled kind is not reported by `verify`; it is reported by the normalizer ([Section 1](#unknown-and-suppressed-kinds)).

### What `check_interval_pairing` checks

Group events by `call_id` over `*_start` / `*_end` kinds and warn when:

| Warning | Meaning |
|---------|---------|
| `call_id X: mixed interval bases [...]` | The same `call_id` is used with different base kinds, for example `llm_call` and `tool_call`. |
| `call_id X: start without matching end` | Open call; typically a crashed or truncated trace. The node is still built without `endTime`. |
| `call_id X: end without matching start` | Orphan end; the node is built without `startTime`. |

Containment (`contains` edges) is inferred separately by timestamp enclosure of nodes that have both `startTime` and `endTime`; it does not use `parent_call_id` alone.

### Running validation

CLI (prints the JSON report, exits 1 when `error_count` is non-zero):

```bash
mas-lab kg verify events.jsonl --strictness recommended
```

Python:

```python
from mas.library.kg.steps import run_verify_events

report = run_verify_events("events.jsonl", strictness="recommended", fail_on_error=False)
print(report["error_count"], report["warning_count"], report["event_count"])
for v in report["violations"]:
    print(v["line"], v["severity"], v["rule"], v["message"])
```

`run_verify_events` returns:

| Key | Type | Description |
|-----|------|-------------|
| `error_count` | `int` | Number of error-level violations. |
| `warning_count` | `int` | Number of warning-level violations. |
| `event_count` | `int` | Number of non-empty lines in the file. |
| `violations` | `list` | One dict per violation: `level` (`L1`/`L2`/`L3`), `severity`, `rule`, `message`, and when set `kind`, `line` (1-based event index, not a physical line number when blank lines exist), `field`. |

With `fail_on_error=True` it raises `ValueError` when `error_count > 0`.

Pairing and JSON Schema checks from Python:

```python
from mas.library.kg.observability.native.verify import verify_events_file

report = verify_events_file("events.jsonl", spec_strictness="recommended")
print(report["ok"], report["errors"], report["warnings"])
print(report["pairing"])   # {"ok": bool, "warnings": [...]}
```

Example pairing warnings:

```
call_id tp-tc-001: start without matching end
call_id tp-llm-999: end without matching start
call_id tp-tc-007: mixed interval bases ['llm_call', 'tool_call']
```

## 8. Session ID convention

The normalizer builds one `Session` node per distinct `run_id`; the event field `session_id` is not read by the graph builder. Session identity comes from the `run_id` (the one passed to `run_normalize` / `--run-id`, which overrides the value in the events):

| `run_id` shape | Session id | `Session.appName` |
|----------------|------------|-------------------|
| `<appName>_<uuid>` (and `split_session_id=True`, the default) | the `<uuid>` part | `<appName>` |
| contains `/` (for example `trip-planner/demo/baseline/item1/r1`; with `split_session_id=True`) | the whole `run_id` | text before the first `/` |
| anything else | `session-<run_id>` | `""` unless `--app-name` is given |

A hierarchical identifier of the form `{app}/{experiment}/{scenario}/{item}/{run}` (following the Experiment / Scenario / Item / Run levels) is therefore a convenient `run_id`: it becomes the Session id and its first segment becomes `appName`. The format is a convention, not validated.

### Overrides

```bash
mas-lab kg normalize events.jsonl \
  --run-id trip-planner/demo/baseline/item1/r1 \
  --output-dir out/
```

| Option | Effect |
|--------|--------|
| `--run-id` / `run_id=` | Sets the run id on every event and drives the Session id as above. When omitted, the CLI uses `--session-id`, else the first event's `run_id`, else its `session_id`, else the file name stem. |
| `--session-id` | On `mas-lab kg normalize`, only a fallback source for the run id (it is not applied as a separate Session id override). |
| `--no-split-session-id` | Do not split `<appName>_<uuid>` run ids. |
| `--app-name` | Force `Session.appName`. |
| `--application-node` | Also emit an `Application` node and `hasSession` edges. |

```python
from mas.library.kg.steps import run_normalize

artifact = run_normalize(
    "events.jsonl",
    run_id="trip-planner/demo/baseline/item1/r1",
    output_dir="out/",
)
```

`run_id` is also written into every node (`runId`) and into the `Run` node.

## 9. Adding new event kinds

The registration point for event kinds is:

```
library-kg/src/mas/library/kg/core/event_mappings.py
```

### To add a new kind

1. Add an entry to `KIND_TO_CLASS`:

   ```python
   "my_new_event_start": "ToolCall",   # an existing ontology class
   "my_new_event_end":   "ToolCall",
   ```

   The value is the local name of a class in the SCOPE ontology (`oxp-ontology`), for example `ToolCall`, `LLMCall`, `AgentCall`, `CallAnnotation`, `GovernanceEvent`, `ContextContribution`. Classes with a dispatch entry in `observability/native/handlers/__init__.py` (`HANDLERS`) get class-specific enrichment; others fall back to the generic call handler.

2. If the kind belongs to an optional layer, add it to `LAYER_KINDS` in the same file so it is dropped when the layer is off.

3. If the kind needs field extraction beyond the common call-node fields, extend the matching handler in `observability/native/handlers/` (for annotation fields, `_ANN_KEY_MAP` in `handlers/annotation.py`).

4. Describe its payload in `schemas/events.spec.yaml` (a `shapes` entry) and regenerate the JSON Schema kind enum with `python library-kg/scripts/generate_events_schema.py`.

### To suppress a kind silently

Map it to `None`:

```python
"legacy_event": None,   # skipped by normalizer and validator; no warning
```

### Kinds absent from the map

An event whose `kind` is not in `KIND_TO_CLASS` is dropped with a warning by default and raises `UnknownSpanBoundaryError` with `strict=True` (`run_normalize(..., strict=True)`). Map or suppress unrecognised kinds explicitly; they signal a data-quality gap.

See [normalization.md](normalization.md) for the two-phase pipeline, [steps.md](steps.md#run_verify_events) for the `run_verify_events` API, and [ontology.md](ontology.md) for the class hierarchy.

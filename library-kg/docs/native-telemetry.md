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

1. **Annotation** (`normalize_events` in `core/graph_builder.py`): drops events whose class belongs to a disabled category, maps each event's `kind` to an ontology class through `KIND_TO_CLASS` (`core/event_mappings.py`), and stamps `mas_class`, `mas_uri`, `run_id` and `call_id` on the event.
2. **Graph extraction** (`extract_graph`, which drives `NativeGraphBuilder` in `core/native_graph_builder.py`): maps each event, by itself, to nodes and edges through a per-class handler in `observability/native/handlers/`. Both records of a call carry the same `call_id` and give the same node id, so they merge; the State and Transition nodes come from the `state_in` / `state_out` fields; the final step only merges by id, drops edges whose endpoint never arrived and applies the SCOPE attribute projection.

### Native normalization contract

Native normalization is mechanical. The runtime should emit explicit values for:

- `run_id`
- `agent_id`
- `call_id` on every `*_start` / `*_end` event
- `parent_call_id` when a call is nested under another call
- the payload fields listed per kind below

Nothing is inferred. Each event maps to nodes and edges by itself: the States and Transitions are the ones the `state_in` / `state_out` fields name, containment is what `parent_call_id` says, and a call that never ended keeps no `endTime`. A field the runtime does not write gives no node, no edge and no placeholder. Because ids come from what the event carries and records merge by id, the graph does not depend on the order of the events.

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
- Order does not matter to the graph: each event maps by itself and records merge by id, so a trace read in any order, or one event at a time as it arrives, gives the same graph. Emit non-decreasing `timestamp` values anyway: they become `startTime` / `endTime`.

A start/end pair:

```json
{"kind":"execution_start","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","session_id":"trip-planner/demo/baseline/item1/r1","boundary":"AgentCall","timestamp":1716200001.0,"input":"Plan a 3-day trip to Paris."}
{"kind":"execution_end","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","session_id":"trip-planner/demo/baseline/item1/r1","status":"success","output":"Trip planned: AF101 NYC to CDG.","timestamp":1716200009.5}
```

Real traces: `library-kg/examples/qa-agent/events.jsonl` (emitted by the runtime) and `library-kg/examples/trip-planner/events.jsonl` (captured from a live run of the `trip-planner` sample). Both ship with an empty or absent `run_id`, so `mas-lab kg verify` reports `missing required field 'run_id'` on them; pass `--run-id` to `mas-lab kg normalize` to normalize them.

## 3. Global envelope (L1)

### Required fields

`events.spec.yaml` and `events.schema.json` require these four on every line:

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `string` | Event kind. Must be a key of `KIND_TO_CLASS` to reach the graph (see [Section 5](#5-kind-reference)). |
| `timestamp` | `number` | Unix epoch seconds; use a float for sub-second precision. |
| `run_id` | `string` | Run identifier grouping the events of one trace. The `run_id` passed to `run_normalize` / `--run-id` overrides this value on every event. |
| `agent_id` | `string` | Agent emitting the event. Used for agent attribution (`agentId`, the `Agent` catalog node). |

### Recommended fields

Reported as warnings when `--strictness recommended` (or `complete`) is used:

| Field | Type | Description |
|-------|------|-------------|
| `mas_id` | `string` | Multi-agent system identifier. |
| `session_id` | `string` | Free-form session identifier (see [Section 8](#8-session-id-convention)). |
| `block` | `string` | Observability block, one of `structural`, `execution`, `context`, `trajectory`, `governance` (the schema enum). A coarse grouping of what the event describes. |
| `layer` | `string` | One of `structure`, `execution`, `semantic`, `provenance`, `governance` (the schema enum). Not the same thing as the opt-in `categories` of [Section 5](#5-kind-reference). |
| `summand` | `string` | One of `model`, `tool`, `context`, `governance`, `orchestrator` (the schema enum): which component of the runtime's observability state machine produced the event. |
| `mealy_symbol` | `string` | Label of the runtime state-machine transition the event records, for example `RUN_START`, `AGENT_START`, `LLM_CALL`, `POLICY_CHECK`. Free-form in the schema; the normalizer does not read it. |

`block`, `layer`, `summand` and `mealy_symbol` are set by the runtime and are not read by the native normalizer.

## 4. Interval events: start/end pairing (L2)

Interval events are kinds whose name ends in `_start` or `_end`. They model one timed operation (a tool call, an LLM call, an agent turn) as a pair that the normalizer merges into one KG node:

- `*_start` creates the node and sets `startTime` plus start-phase payload.
- `*_end` updates the same node with `endTime`, `status` (default `success` when absent) and end-phase payload.
- A call with a start and no end gets `success=false`, and so does the Session when its outer call has no end. This only happens for an interrupted trace.

### Pairing contract

| Rule | Detail |
|------|--------|
| Same `call_id` | Both halves carry the identical `call_id`. This is the join key. It is a string, not necessarily a UUID. |
| Same base kind | The part before `_start`/`_end` must match. A `call_id` shared by different bases is reported by `check_interval_pairing` as `mixed interval bases`. |
| `call_id` required | The JSON Schema and `EventValidator` require `call_id` on every `*_start` and `*_end`. The normalizer drops a structural event without one. |
| `parent_call_id` recommended | Names the call's parent. For an LLM, tool, processing or memory call it is the agent visit (`execution_start` `call_id`) that contains it. Omit it (or set it to `null`) for root calls such as `mas_call_start`. A delegated visit names the tool call that started it. |

### Resilience behaviour

Behaviour of the merge by node id (`NativeGraphBuilder.add_node`, `core/native_graph_builder.py`). Start and end records are two partial descriptions of one node; each says what it carries:

| Scenario | Behaviour |
|----------|-----------|
| Start arrives before end | Normal case. |
| End arrives before start | The node is created from the end event (no `startTime`); `startTime` is set when the start arrives. |
| Start with no matching end | The node keeps `startTime` and has no `endTime` and no `success` (open call). Nothing is cut off at the last event. |
| End with no matching start | The node is created from the end alone. |
| Second start | Merges into the same node: the earliest `startTime` is kept; a different non-empty value for a field keeps the one that sorts first. |
| Duplicate end | Merges into the same node: the latest `endTime` is kept; a different non-empty value for a field keeps the one that sorts first. |

### State identity

Each agent visit (`execution_start` to `execution_end`, keyed by `call_id`) is a chain of States. A State id is `{exec_call_id}#{n}`, with `n` counting from 0 in emission order. Ids depend only on emission order, never on uuids or time. The fields are additive and no existing field changes:

| Field | On | Meaning |
|-------|----|---------|
| `state_out` / `state_out_content` | `execution_start` | The agent input (`#0`) and its text. |
| `state_in` / `state_out` | `llm_call_*`, `tool_call_*`, `memory_call_*`, context assembly `processing_call_*` | The State the element reads and the State it produces. Start and end of one call carry the same pair. |
| `state_out_content` | `llm_call_end` | Model text, or the canonical JSON of `tool_calls` when the text is empty. |
| `state_out_content` | `tool_call_end`, `memory_call_end` | The `output`. |
| `state_out_content` | context assembly `processing_call_end` (and its start, when it has messages) | Canonical JSON of `messages`. |
| `state_in` / `state_out` | `execution_end` | The visit's final State. No new id. |
| `state_in` / `state_out` | `mas_call_end` | The root visit's first State and the last root visit's final State. |
| `state_in_content` | every record with `state_in` | The content of the input State, as its producer stated it. `""` if the producer has not ended yet. |

Canonical JSON means `json.dumps(value, sort_keys=True, separators=(",", ":"))`.

Parallel groups do not advance the chain. Members read the group's input State and each gets its own `state_out`. The join is one `aggregate` ProcessingCall pair at the group end (`processing_type` `parallel_group`, the same timestamp on start and end). Its `state_in` is the list of member outs in start order, `state_out` is the new join State, and `state_in_content` / `state_out_content` (also `input` / `output`) are the JSON list of the members' contents. This is the only State producer at the join. `parallel_group_start` / `parallel_group_end` are plain markers and carry no state fields. There is no fork ProcessingCall.

Control markers are plain records. `wait_state_start` / `wait_state_end` and `parallel_group_start` / `parallel_group_end` have no `wait`, `resume` or `parallel fork` ProcessingCall companions. A context assembly `processing_call_end` has zero duration (its timestamp equals its start) and no `synthetic` flag.

Delegation hands data from the caller to the callee through the tool call that starts the callee's visit. The callee's `execution_start` carries `parent_call_id` = that tool call's `call_id`. A session-wide registry records every tool call (caller `agent_id`, caller `exec_call_id`, and the call's `state_in` / `state_in_content` / `state_out`). When a visit is delegated, the caller gets two ProcessingCall pairs:

| Pair | `call_id` | `parent_call_id` | State flow | Emitted |
|------|-----------|------------------|------------|---------|
| `delegate` | `{tool_call_id}:delegate` | caller `exec_call_id` | reads the tool call's `state_in`, writes the callee's `#0` | before the callee's `execution_start` |
| `reply` | `{tool_call_id}:reply` | caller `exec_call_id` | reads the callee's final State, writes the tool call's `state_out` | after the callee's `execution_end` |

Both pairs use `processing_type` `agent_boundary`, and both start and end records of a pair carry the same timestamp. A root visit (no delegating `parent_call_id`) gets neither pair.

**How the fold reads these fields.** `mas.library.kg` turns each record into States and Transitions with no knowledge of any other record (`core/state_transition.py`; [normalization.md](normalization.md#state-and-transition-nodes-corestate_transitionpy)):

- A State node per id (`state:{run_id}:{state id}`). The same id on two records is one State; non-empty content beats empty, and two different non-empty contents keep the one that sorts first and log a warning. The emitter guarantees equal content for a same id.
- One Transition per element. On `execution_start`, `state_out` is the visit's initial State; on `execution_end`, `state_in` = `state_out` is its final State; on `mas_call_end`, `state_in` / `state_out` are the root visit's first and the last root visit's final State; on every other element, `state_in` (one id, or a list for a join) is read and `state_out` written.
- Edges: `hasInitialState` / `hasFinalState` / `hasState` (element -> State), `inputTo` (input State -> Transition), `leadsTo` (Transition -> output State), `correspondsToTransition` / `representsExecution`. The Session and the MASCall share the States `mas_call_end` names and each has its own Transition.
- A record with no state fields adds no State, no Transition and no placeholder content. `wait_state_*` and `parallel_group_*` records are plain markers.

## 5. Kind reference

`KIND_TO_CLASS` in `library-kg/src/mas/library/kg/core/event_mappings.py` maps every known kind to an ontology class (tables below) or to `None` (suppressed, [5.6](#56-suppressed-kinds)). Class names are local names in the SCOPE ontology (`ioc-scope-ontology`; see [ontology.md](ontology.md)).

**Categories.** The default output holds the core classes only: `Session`, `MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `State`, `Transition` and the `MAS` / `Agent` / `LLM` / `Tool` / `Processing` catalog. One option, `categories` (`--categories a,b` on `mas-lab kg normalize`; a list or a comma-separated string in `run_normalize`, `build_kg_document`, `normalize_events` and the `normalize_events` step's `categories:`), adds opt-in classes on top; `all` enables every one, and an unknown name is an error. The filter acts on the class an event maps to (`CATEGORY_CLASSES` in `core/event_mappings.py`), the same way in the batch and the realtime fold: an event of a disabled category is dropped, never changes a core node, and the core graph is the same whether or not such events are in the input. Default attributes are SCOPE model fields only (`--all-attributes` keeps the native-only ones).

| Category | Classes | Kinds | Default |
|----------|---------|-------|---------|
| `capabilities` | `Skill`, `SkillCall`, `Memory`, `MemoryCall`, `RAGQuery` (and `executesSkill`, `executesMemory`, `usesSkill`, `usesMemory`, `hasSkillCall`, `hasMemoryCall`) | `skill_execution_*`, `memory_*`, `rag_query_*` | off |
| `annotations` | `CallAnnotation`, `GovernanceEvent`, `ContextContribution` (and `annotates`) | `routing`, `routing_result`, `context_assembled`, `state_update_*`, `agent_communication_*`, `observability_*_execute_*`, `user_input`, `user_output`, `client_response`, `tool_result_injected`, `boundary_ingress`, `boundary_egress`, `context_steer`, `compaction`, `wait_state_*`, `context_part_contributed`, the governance kinds (`audit`, `policy_*`, `budget_event`, `transformation_event`, `control_intervention`, `hitl_*`, `governance_decision`, `governance_event`) and the outcome a check interval states | off |
| `worker` | `Worker` (and `runsOn`) | `infrastructure_info` | off |
| `thinking` | `ThinkingCall` (and `hasThinkingCall`) | `thinking_start`, `thinking_end` | off |

The names are the ones of the observe-sdk categories (memory, rag, skill, governance, context, annotation, worker, thinking), which are also off by default there. `norm` has no category gate: it maps every span suffix it is given (`*.skill`, `*.memory`, `*.rag`, `*.annotation`, `*.governance`, `*.context`, `*.worker`, `*.thinking`), so on the OTel path the gate is the observe-sdk's switches (and the converter's export layers), and a native graph with every category on equals `norm`'s output for the same trace.

The "Spec required" and "Spec recommended" columns below come from `events.spec.yaml` (what `mas-lab kg verify` checks). Every `*_start` / `*_end` kind additionally requires `call_id` (L2) and recommends `parent_call_id`. The "Normalizer reads" details are in [Section 6](#6-per-kind-payload-reference).

### 5.1 Call-tree kinds (start/end pairs)

| Kind | KG class | Spec required (L3) | Spec recommended (L3) |
|------|----------|--------------------|------------------------|
| `execution_start` | `AgentCall` | `input` | `context` |
| `execution_end` | same as its start | `status` | `output`, `output_parts` |
| `llm_call_start` | `LLMCall` | `model` | `messages`, `temperature`, `max_tokens`, `tools` |
| `llm_call_end` | `LLMCall` | | `latency_ms`, `response`, `tool_calls`, `output_parts`, `finish_reason`, `tokens_used` |
| `tool_call_start` | `ToolCall` | `tool_name` | `arguments` |
| `tool_call_end` | `ToolCall` | `tool_name` | `latency_ms`, `result`, `output_parts`, `status` |
| `network_call_start` / `_end` | `ToolCall` | | |
| `mas_call_start` / `_end` | `MASCall` | | `mas_id`, `session_id` |
| `rag_query_start` / `_end` | `RAGQuery` (a `MemoryCall` that retrieves); `capabilities` | | |
| `memory_call_*`, `memory_store_*`, `memory_retrieve_*` | `MemoryCall`; `capabilities` | | |
| `processing_call_start` / `_end` | `ProcessingCall` | | |
| `workflow_transition_start` / `_end` | `ProcessingCall` (no dedicated class) | | |
| `skill_execution_start` / `_end` | `SkillCall`; `capabilities` | | |
| `thinking_start` / `_end` | `ThinkingCall`; `thinking` | | `model`, `reasoning_tokens` (end), `state_in` (start), `state_out` (end) |
| `parallel_group_start` / `_end` | none (suppressed marker; the join is the `aggregate` ProcessingCall) | | |

A `thinking_start` / `thinking_end` pair is the reasoning interval of one LLM call (`thinking` category): its `call_id` is `<llm_call_id>#think`, its `parent_call_id` the LLM call (which `hasThinkingCall` it), `state_in` the prompt State of the LLM call and `state_out` the reasoning text, like any element record. The fold never estimates an interval from timing; a producer that reports reasoning only as text puts it on the LLM call's output State and emits no pair. `branch_*` is not a known kind (no producer emits it).

### 5.2 Annotation kinds (point events)

With the `annotations` category, annotation events become `CallAnnotation` nodes (`eventKind`, `eventTime`, `payload`). They do not form call-tree nodes: each line (including each half of a `*_start`/`*_end` annotation pair) becomes its own annotation node, linked by an `annotates` edge to the call its record names in `call_id` (else `parent_call_id`) when that call is in the trace. No call is looked up by time.

| Kind | Notes |
|------|-------|
| `routing`, `routing_result` | Delegation between agents ; no parent is inferred from it. |
| `context_assembled` | Context window assembled before an LLM call. |
| `state_update_start` / `_end` | Context state mutation. |
| `agent_communication_start` / `_end` | Agent-to-agent message. |
| `checkpoint_start` / `_end`, `governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_authorize_*`, `obs_wrap_gov_validate_*` | Timing bracket of a check: a `ProcessingCall` named by the kind without its suffix (core class, always mapped). `call_id` is the interval's own key, `parent_call_id` the call it brackets, which contains it (`hasProcessingCall` from an agent visit, `containsCall` from an LLM or tool call). The States come from `state_in` / `state_out` when the records carry them. A record that states a decision (`decision_type`, `decision`, `outcome`, `resolution`) also gives a `GovernanceEvent` (`annotations` category) that annotates the bracket; one that states none gives none. |
| `observability_pre_execute_start` / `_end`, `observability_post_execute_start` / `_end` | Runtime wrapper checkpoints around an operation. |
| `user_input`, `user_output`, `client_response` | User-facing I/O. |
| `tool_result_injected` | A tool result fed back into the context. |
| `boundary_ingress`, `boundary_egress`, `context_steer`, `compaction`, `wait_state_start` / `_end` | Runtime markers. |

### 5.3 Context provenance (`annotations`)

`context_part_contributed` becomes a `ContextContribution` node (one per context part). Spec recommended: `part_id`, `source`, `content`, `access_mechanism`, `cause`, `retained`, `parents`. Off by default; enable with `--categories annotations`.

### 5.4 Infrastructure (`worker`)

`infrastructure_info` maps to class `Worker`. Spec recommended: `worker_id`, `endpoint`. The handler writes a `Worker` node named by `worker_id` (`processId` from `worker_pid`, `durableBackend` from `durable_backend`) and, when the record has a `parent_call_id`, a `runsOn` edge from that execution to the Worker.

### 5.5 Governance (`annotations`)

All governance kinds (listed in the category table above) map to class `GovernanceEvent` and are handled like annotations: a `GovernanceEvent` node with an `annotates` edge. Off by default; enable with `--categories annotations`.

Note: `events.spec.yaml` requires `call_id` for every kind with a `governance_` prefix (including point kinds such as `governance_checked` and `governance_decision`), so `mas-lab kg verify` flags them when `call_id` is missing, although the normalizer does not need it.

### 5.6 Suppressed kinds

Mapped to `None` in `KIND_TO_CLASS`: skipped by the normalizer and by `EventValidator`, with no warning.

| Kind | Reason |
|------|--------|
| `prompt_build_start`, `prompt_build_end` | Redundant with `llm_call_start.messages`. |
| `user_response` | Not a node. Its `content` is the AgentCall's `outputRaw` (the runtime puts the reply here and `execution_end` carries none), and the OTel converter reads the reply the same way. |
| `human_interaction`, `delegated_agent_output` | Superseded; no mapping. |
| `skills_check` | Legacy governance extension. |
| `object_upsert`, `object_link`, `object_model_event` | Domain-KG extension only. |
| `user_input_request` | Not a call node. |
| `contract_call_start`, `contract_call_end` | Runtime wrapper spans; no KG representation. |

## 6. Per-kind payload reference

This section lists what the native handlers in `observability/native/handlers/` read. Fields the spec recommends but the handlers do not read are called out. Examples use the trip-planner scenario (`run_id` `r1`, agents `mas`, `planner`, `itinerary_agent`) and are illustrative.

**Common to every call-tree node** (`NativeGraphBuilder.call_partial`): `call_id` -> `callId`, `agent_id` -> `agentId`, `parent_call_id` of the start record -> `parentCallId`, `run_id` -> `runId`, start `timestamp` -> `startTime`, end `timestamp` -> `endTime`, end `status` -> `status` (default `success`), and `event_id` (when present) -> `sourceRecordIds`.

### 6.1 `execution_start`

Creates an `AgentCall` node.

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `input` / `payload` | `inputContent` | AgentCall only. |
| `agent_type` | `agentType` | AgentCall only. |
| `agent_sequence` | `agentSequence` | AgentCall only; converted to int. |
| `agent_id` | `agentName` | AgentCall only. |
| `parent_call_id` | `parentCallId` | Not read for containment of an AgentCall: agent calls are contained by the MASCall of the session. |

`context` is spec-recommended but not read by the handler.

```json
{"kind":"execution_start","agent_id":"planner","call_id":"tp-exec-001","parent_call_id":"tp-mas-001","run_id":"r1","boundary":"AgentCall","timestamp":1716200001.0,"input":"Plan a 3-day trip to Paris with flight booking and hotel."}
```

### 6.2 `execution_end`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `status` | `status` | Spec-required. Free-form string (the shipped examples use `success` and `ok`). |
| `output` / `payload` | `outputRaw` | AgentCall only. |

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
| `tools` | `offeredTools` | Offered function names sent with the request (kept when `all_attributes` is on). |
| `temperature` | `temperature` | Spec-recommended. |
| `max_tokens` | `maxTokens` | Spec-recommended (kept when `all_attributes` is on). A `max_tokens` on `llm_call_end` (another cap was sent) replaces the start's. |

Token counts and `finishReason` start at `0` / empty and are filled by the end event.

```json
{"kind":"llm_call_start","agent_id":"planner","call_id":"tp-llm-001","parent_call_id":"tp-exec-001","run_id":"r1","model":"vertex_ai/gemini-2.5-flash","timestamp":1716200002.0,"messages":[{"role":"system","content":"You are a travel planning agent."},{"role":"user","content":"Plan a 3-day trip to Paris."}],"temperature":0.7,"max_tokens":1024}
```

### 6.4 `llm_call_end`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `response.usage.prompt_tokens` / `input_tokens` | `promptTokenCount` | Also `tokens_used` at the top level of the event. |
| `response.usage.completion_tokens` / `output_tokens` | `completionTokenCount` | Same. |
| `response.usage.total_tokens` | `totalTokenCount` | Same. |
| `response.usage.cache_read_input_tokens` | `cacheReadTokenCount` | |
| `response.id` | `responseId` | |
| `response.choices[0].finish_reason` or top-level `finish_reason` | `finishReason` | |
| `response.choices[0].message.{content,tool_calls}` or flat `response.{content,text,tool_calls}` or top-level `tool_calls` | `completion`, `toolCalls` | Tool calls are rendered as `→ name(args)` lines (args cut at 300 characters, appended after any output text). The OTel converter renders the same text. |
| `content` / `output` (flat) | `completion` | Used when there is no `response` object. |

New runtime traces put `model` / `messages` / offered `tools` on `llm_call_start`. Token usage and `finish_reason` stay on `_end`. An OpenAI-shaped `response` is still accepted; `model` on that object is not read.

```json
{"kind":"llm_call_end","agent_id":"planner","call_id":"tp-llm-001","parent_call_id":"tp-exec-001","run_id":"r1","status":"success","timestamp":1716200003.5,"latency_ms":1500,"output":"Delegating to itinerary_agent.","finish_reason":"stop","tokens_used":{"prompt_tokens":80,"completion_tokens":18,"total_tokens":98}}
```

### 6.5 `tool_call_start` / `network_call_start`

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `tool_name` | `toolName` | Spec-required on `tool_call_*`. |
| `tool_call_id` | `toolCallId` | Provider-assigned id. |
| `tool_arguments` / `arguments` / `parameters` | `toolArguments` | First present, JSON-encoded. |

**Delegation tools.** A tool named `delegate_to_<agent>` is an ordinary `ToolCall` of the caller; the callee's visit is an ordinary `AgentCall` (contained by the MASCall). The data handoff is on the States ([State identity](#state-identity)). `callsAgent` is not emitted until A2A observability lands.

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

These produce `RAGQuery` / `MemoryCall` nodes (a `RAGQuery` is a `MemoryCall`; both are contained by `hasMemoryCall`).

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| kind (`memory_store`, `memory_retrieve`, `rag_query`) or `operation` | `memoryOperation` | `store`, `retrieve`, `retrieve`; for `memory_call_*` the record's `operation`. |
| `query` / `query_text` / `key` | `memoryQuery` | |
| `result_count` / `results_count` / `retrieved_doc_count` | `resultCount` | |
| `top_k` | `topK` | `RAGQuery` only. |
| `memory_name`, else `collection`, else `memory_type` | the `Memory` node (`name`) and the `executesMemory` edge | `memory_type` also fills `Memory.memoryType`. A record that names none gives no `Memory` and no edge: SHACL then reports the missing `executesMemory`. |

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

A `SkillCall` has the common call fields only. `skill_name` (or `processing_name`) names the `Skill` node it `executesSkill`; the input and output belong to its States.

### 6.11 Point and annotation events

`routing`, `routing_result`, `context_assembled`, `state_update_*`, `agent_communication_*`, and the governance kinds become an annotation node (`CallAnnotation` or `GovernanceEvent`) with `eventKind` (the kind), `eventTime` (the timestamp), `sessionId` and `payload`, a JSON object of the fields below that the record carries:

| Event field | In `payload` |
|-------------|-------------|
| `source_agent_id`, `target_agent_id`, `task`, `correlation_id`, `status` | same names |
| `segments`, `total_tokens`, `operation`, `target`, `from`, `to`, `message_type` | same names |
| `denied_call_id`, `budget_scope`, `amount`, `intervention_type` | same names |

A `GovernanceEvent` also takes `decision` (`decision`), `policyName` (`policy_name`, else `policy_id`) and `decisionReason` (`reason`). Other fields (for example `hook`, `plugin`, `contract_id`, `details`, `content`) are not copied. The node `annotates` the call in `call_id`, else `parent_call_id`; a record naming neither has no edge, and SHACL reports the missing `annotates`.

```json
{"kind":"routing","agent_id":"planner","run_id":"r1","source_agent_id":"planner","target_agent_id":"itinerary_agent","task":"Build a 3-day Paris itinerary.","correlation_id":"corr-001","timestamp":1716200003.9}
{"kind":"context_assembled","agent_id":"planner","run_id":"r1","total_tokens":512,"segments":3,"timestamp":1716200001.9}
{"kind":"governance_checked","agent_id":"planner","run_id":"r1","policy_id":"routing","decision":"allow","reason":"planner to itinerary_agent: found in declared edges","timestamp":1716200003.8}
```

### 6.12 `context_part_contributed`

Requires `--categories annotations`. Produces a `ContextContribution` node (a `CallAnnotation` subclass):

| Event field | KG attribute | Notes |
|-------------|-------------|-------|
| `part_id` | `partId` | derived id when absent |
| `source` | `contextSource` | |
| `access_mechanism` | `contributionMechanism` | |
| `token_estimate` | `tokenEstimate` | |
| `retained` | `retained` | |
| `kind`, `timestamp` | `eventKind`, `eventTime` | |
| `parents`, `section_id`, `source_type`, `cause`, `cause_type`, `sensitivity`, `eviction_reason`, `content` | `payload` (JSON) | only those the record carries |
| `llm_call_id` | `annotates` edge to that `LLMCall` | without it, `annotates` the call in `parent_call_id` when present |

There is no `derivedFrom` edge: SCOPE has none, so `parents` stays in `payload`.

```json
{"kind":"context_part_contributed","agent_id":"itinerary_agent","run_id":"r1","part_id":"part-hotel-rag-001","source":"rag_result","section_id":"retrieved_context","access_mechanism":"vector_search","token_estimate":142,"retained":true,"content_preview":"Hotel du Marais: 195 EUR/night","llm_call_id":"tp-llm-002","parents":[],"timestamp":1716200007.1}
```

### 6.13 `parallel_group_*`, `infrastructure_info`

`parallel_group_*` are plain markers and map to nothing (see [5.1](#51-call-tree-kinds-startend-pairs)). `infrastructure_info` produces a `Worker` (see [5.4](#54-infrastructure-worker)).

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

Containment is not inferred. `hasAgentCall` / `belongsToMASCall` link every AgentCall to the MASCall of the session; `hasLLMCall`, `hasToolCall`, `hasProcessingCall` and `hasMemoryCall` link a call to the agent visit its `parent_call_id` names (no edge when that visit never arrives).

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

The normalizer builds one `Session` node per distinct `run_id`. Session identity comes from the `run_id` (the one passed to `run_normalize` / `--run-id`, which overrides the value in the events). The last row applies when the `run_id` carries no session of its own: the events' own `session_id` (the runtime writes one on every event), if no other run shares it, is the Session id as is, and the first `app_name` the events carry is `Session.appName` when `run_id` gives none. The OTel path reads the same two fields, so both paths name the session alike. `--no-split-session-id` turns the events' `session_id` off too.

| `run_id` shape | Session id | `Session.appName` |
|----------------|------------|-------------------|
| `<appName>_<uuid>` (and `split_session_id=True`, the default) | the `<uuid>` part | `<appName>` |
| contains `/` (for example `trip-planner/demo/baseline/item1/r1`; with `split_session_id=True`) | the whole `run_id` | text before the first `/` |
| anything else, and the events carry a `session_id` | that `session_id` | the events' `app_name`, else `""` unless `--app-name` is given |
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

```python
from mas.library.kg.steps import run_normalize

artifact = run_normalize(
    "events.jsonl",
    run_id="trip-planner/demo/baseline/item1/r1",
    output_dir="out/",
)
```

`run_id` is also written into every node (`runId`; kept with `all_attributes=True`).

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

   The value is the local name of a class in the SCOPE ontology (`ioc-scope-ontology`), for example `ToolCall`, `LLMCall`, `AgentCall`, `CallAnnotation`, `GovernanceEvent`, `ContextContribution`. Classes with a dispatch entry in `observability/native/handlers/__init__.py` (`HANDLERS`) get class-specific enrichment; others fall back to the generic call handler.

2. If its class belongs to an opt-in category, add the class to `CATEGORY_CLASSES` in the same file so it is dropped when the category is off.

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

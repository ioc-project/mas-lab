# Normalization pipeline

Normalization turns a run's native event log (`events.jsonl`) or OpenTelemetry (OTel) spans into a knowledge graph (KG): typed nodes (entities such as an agent call) and directed edges (relations between them), stored as `kg.jsonld`. This page is the reference for that conversion in `mas.library.kg`. Read it when you need to know which event produces which node, or why an id or edge looks the way it does.

```bash
mas-lab kg normalize path/to/events.jsonl -o kg.jsonld
```

Code covered: `mas.library.kg.pipeline`, `core/graph_builder.py`, `core/native_graph_builder.py`, `core/event_mappings.py`, `core/state_transition.py`, `core/ontology_align.py`, `observability/native/handlers/`.

See also: [ontology.md](ontology.md), [steps.md](steps.md), [neo4j.md](neo4j.md).

## Table of contents

1. [Overview](#1-overview)
2. [Normalization modes and controls](#2-normalization-modes-and-controls)
3. [Runtime properties](#3-runtime-properties)
4. [Event to KG mapping table](#4-event-kg-mapping-table)
5. [Synthesised nodes and edges](#5-synthesised-nodes-and-edges)
6. [Node attributes reference](#6-node-attributes-reference)
7. [Edge type reference](#7-edge-type-reference)
8. [Layer defaults and control](#8-layer-defaults-and-control)
9. [Node ID stability](#9-node-id-stability)

## 1. Overview

Normalization reads native observability events of a multi-agent system (MAS), from an `events.jsonl` file or a list of dicts, and writes a `kg.jsonld` document with the top-level keys `@context`, `nodes`, `edges` and `metadata`.

Terms used below:

- **Call node**: a node for one execution step, created from a `*_start` / `*_end` event pair (`AgentCall`, `LLMCall`, `ToolCall`, ...). Call nodes carry a `callId`.
- **Annotation node**: a point-in-time event with no `call_id` (`CallAnnotation`, `GovernanceEvent`, `ContextContribution`).
- **State / Transition**: trajectory nodes. A `State` holds the content of one step's input or output; a `Transition` connects the two and *represents* that call (see [section 5](#5-synthesised-nodes-and-edges)).
- **Layer**: a group of event kinds you can switch on or off (trajectory, provenance, infrastructure, governance; see [section 8](#8-layer-defaults-and-control)).

OTel telemetry is input, not a node layer: normalization never emits `Span` nodes. Call nodes carry `sourceRecordIds`, the `event_id` of each native event that created or updated the node. Validation uses `mas-ontology.ttl` from the `oxp-ontology` package (the ontology of the Observe and eXplain Platform, OXP) plus the MAS Lab extension file (see [ontology.md](ontology.md)).

The native pipeline has three stages:

```
events.jsonl
  -> normalize_events        (annotate: layer filter, class lookup, call_id check)
  -> extract_graph           (back-compat wrapper over NativeGraphBuilder)
       process_event x N     (dispatch to handlers/*.py, build call/annotation nodes)
       finalize()            (cross-event passes, synthesis, ontology alignment)
  -> kg.jsonld document
```

### Stage 1: annotation (`core/graph_builder.py:normalize_events`)

`normalize_events(events, ontology, run_id, **layer_flags, strict=False)` iterates the list once and, per event:

1. Drops events whose `kind` belongs to a disabled layer (`layer_skip_kinds`, built from `LAYER_KINDS`).
2. Looks up the ontology class in `KIND_TO_CLASS` (`core/event_mappings.py`) via `_class_for_event`. Kinds mapped to `None` are suppressed and receive `mas_class = "ExecutionElement"`, which `process_event` skips. Kinds absent from the table raise `UnknownSpanBoundaryError`. `execution_*` events become `TaskCall` when `boundary == "TaskCall"` or `agent_id` ends with `.task`.
3. Annotates the event with `mas_class`, `mas_uri`, `span_level` and `mas_icon` from the ontology index.
4. Checks that structural events carry a `call_id` (a missing one raises `MissingCallIdError`). `ProcessingCall` events without one get a deterministic `agent:parent:type` key instead. Annotation, governance, provenance and infrastructure events legitimately have none.
5. Sets `run_id` and copies `event_id` to `record_id`.

Both errors are observability gaps, not fatal: with the default `strict=False` the event is dropped, a deduplicated `WARNING` is logged and normalization continues. `strict=True` raises instead (useful in CI checks of the mapping table).

### Stage 2: graph construction (`core/native_graph_builder.py`)

`extract_graph(normalized, ...)` in `core/graph_builder.py` is a thin wrapper: it calls `build_graph_from_normalized`, which sorts the events by timestamp (stable), feeds each to `NativeGraphBuilder.process_event`, then calls `finalize()`.

- `process_event` dispatches on `mas_class` through `HANDLERS` (`observability/native/handlers/__init__.py`): `agent.py` (AgentCall, TaskCall), `chat.py` (LLMCall plus ThinkingCall), `tool.py` (ToolCall, SkillCall), `processing.py`, `memory.py`, `rag.py`, `session.py` (MASCall), `annotation.py` (CallAnnotation, GovernanceEvent, ContextContribution). `ParallelGroup`, `Branch` and `Worker` use the generic fallback in `structural.py`. All call handlers share `NativeGraphBuilder.upsert_call`, which creates the node on `*_start` and updates it on `*_end`.
- `finalize()` runs the passes that need the complete event set, in this order: derive tool-use completions and materialise AgentCall output parts (`core/output_rendering.py`); resolve annotations to calls (`_resolve_annotation_edges`); build `Session` (and optional `Application`) and `Run`/`Agent` nodes; infer `contains` edges by time enclosure (`_infer_contains_edges`); backfill missing `parentCallId` (`_backfill_missing_parent_call_ids`); flatten delegated AgentCalls to siblings; synthesise State/Transition nodes (`core/state_transition.py`); add `derivedFrom` edges (`_derived_from_edges`); add catalog nodes (`_extract_catalog_layer`); add session trajectory anchors; normalise ids; and finally align edge names and add topology edges to the OXP ontology vocabulary (`core/ontology_align.py:align_graph`, then `core/oxp_models.py:map_graph`).

`build_kg_document` (`pipeline.py`) wraps both stages and adds `metadata` (event/node/edge counts, `ontology_path`, `layers`, `plot_events`).

## 2. Normalization modes and controls

`library-kg` has two entry modes:

1. **Native events** (`events.jsonl`)
   - Entry points: `build_kg_document(...)`, `build_kg_from_events_path(...)`, `run_normalize(...)`, `mas-lab kg normalize EVENTS_PATH`.
   - Code path: `normalize_events` -> `extract_graph` as in [section 1](#1-overview).

2. **OTel spans**
   - Entry points: `build_kg_from_otel_spans(...)`, `run_normalize_otel(...)`, `mas-lab kg otel-to-kg`, `mas-lab kg normalize --otel FILE`.
   - Code path: `observability/otel_via_norm.py:normalize_otel`. It detects ClickHouse-shaped rows (`SpanName` / `SpanAttributes`) versus OTel SDK span JSON (`name` / `context` / `attributes`), reshapes SDK spans mechanically, and calls `norm.normalize()` from the OXP OTel normalizer. Dispatch and span handlers live in `norm`, not here. The graph vocabulary is `norm`'s; no events are reconstructed.
   - `norm` is the optional `[norm]` extra (installed from git until published). Without it, the OTel entry points log a warning and return an empty graph.

`pipeline.normalize(raw_input)` detects the shape (`kind` field means native; `SpanName` or `name`+`context`/`attributes` means OTel) and returns `(nodes, edges)`.

### 2.1 Controls

| Control | Where | Meaning |
|---|---|---|
| `strict` | `normalize_events`, `build_kg_document`, `run_normalize` | Raise on observability gaps instead of logging and skipping the event. |
| `include_infrastructure` / `include_trajectory` / `include_provenance` / `include_governance` | same, and `--include-*` / `--no-include-*` CLI flags | Select optional layers; defaults `False` / `True` / `False` / `False` ([section 8](#8-layer-defaults-and-control)). |
| `split_session_id` | `build_kg_document`, `--split-session-id` | Split a `<appName>_<uuid>` run id into `Session.appName` and `Session.sessionId`; default on. |
| `app_name` | `build_kg_document`, `--app-name` | Override `Session.appName`. |
| `application_node` | `build_kg_document`, `--application-node` | Emit one `Application` node per app plus `hasSession` edges. |
| `session_id_override` | `extract_graph`, `--session-id` | Rename the session of a single-run trace (ignored with a warning when the trace has several run ids). |
| `include_normalization_provenance` | `build_kg_document`, `build_kg_from_otel_spans` | Add `normalization_path`, `provenance_mode`, `heuristics_applied` to `metadata` (default off). |
| `rewrite_tool_delegation` | `NativeGraphBuilder` only (default `True`) | Drop `delegate_to_*` ToolCalls and emit `callsAgent` edges instead ([section 5](#5-synthesised-nodes-and-edges)). Not exposed on `build_kg_document`. |
| `allow_heuristics`, `fill_synthesized_processing_defaults` | native entry points | Accepted and ignored; the heuristics they used to gate were removed. |

### 2.2 No heuristic recovery

Neither path reconstructs missing telemetry. The native path builds nodes only from fields on the events themselves; OTel input goes through `norm` unchanged. The only derived content is:

- `LLMCall.completion` for tool-use-only responses, rendered from the following sibling `ToolCall` nodes (`derive_tool_use_completions`).
- `AgentCall.outputParts` / `outputContent`, rendered from the agent's own `execution_end` output and its child calls (`materialize_agent_outputs`).
- `parentCallId` fallbacks, in precedence order: (1) the runtime's explicit `parent_call_id`; (2) routing-derived delegation parent, applied event by event in `handlers/agent.py` using the last `routing` event addressed to the agent; (3) time enclosure (`_backfill_missing_parent_call_ids`), only for nodes still unset.

### 2.3 Provenance fields

- `sourceRecordIds` is the ordered list of `record_id`s that created (first) and updated (later) a call node. On the native path `record_id = event_id`.
- Document-level provenance in `metadata` is opt-in (`include_normalization_provenance`) to avoid payload growth.

## 3. Runtime properties

| Property | Verdict | Detail |
|---|---|---|
| Per-event processing | Yes | `NativeGraphBuilder.process_event` handles one event at a time. |
| Real-time, complete graph | Partial | Cross-event passes run only in `finalize()`. |
| Stateless across traces | Yes | State lives in the builder instance. |
| Duplicate events | Mostly | Duplicate starts become annotations; duplicate ends overwrite. |
| Out-of-order events | Yes (batch) | Batch path sorts by timestamp first. |

### Per-event versus batch

Classification and handler dispatch are O(1) per event (a dict lookup in `KIND_TO_CLASS`, then one handler call). `build_graph_realtime(builder, event)` feeds one event to a long-lived `NativeGraphBuilder` and returns only the nodes and edges that event added. The complete graph needs `finalize()`, because several passes need every node:

| Pass | Why it needs the full trace | Cost |
|---|---|---|
| `_resolve_annotation_edges` | finds the tightest enclosing call per annotation | O(annotations x calls) |
| `_infer_contains_edges` | sweep over all timed calls sorted by `(startTime asc, endTime desc)` | O(n log n) |
| `synthesize_states_and_transitions` | chains sibling calls in start order | O(n log n) |
| `derive_tool_use_completions`, `materialize_agent_outputs` | read sibling/child calls | O(n) |
| `_extract_catalog_layer` | groups calls by tool/model/skill/processing name | O(n) |

`build_kg_document` is a batch function.

### Statelessness

Normalization keeps no state across invocations. The only module-level state is `_ontology_cache` in `core/graph_builder.py`, a read-only cache of parsed ontology indexes keyed by resolved TTL path. All per-trace accumulators (`call_nodes`, `edges`, `raw_annotations`, `cpr_nodes`, `run_ids`, `agent_ids`, routing inference dicts) belong to the `NativeGraphBuilder` instance.

### Duplicates

- Duplicate `*_start` for a call that is still open (same call, `startTime` set, no `endTime`) is not applied; it is recorded as a `CallAnnotation` carrying `callId`, `parentCallId` and the original `kind`.
- Duplicate `*_end` events overwrite `endTime`, `status` and output fields (last writer wins).
- Duplicate point events (annotations, governance, context contributions) are not deduplicated: each event produces one node, and two events with the same identity key produce two nodes with the same `id`. Deduplicate the input if that matters.

### Out-of-order input

`build_graph_from_normalized` sorts events by `timestamp` (stable on ties; events without a numeric timestamp sort last) before `process_event`. After the sort, the arrival-order logic (routing-derived parents, `_active_exec`) sees causal order. `contains` inference and sibling chaining sort again on their own keys. A `routing` event that still arrives after the target agent's `execution_start` cannot supply a parent; the node then falls back to time enclosure, or stays a root.

## 4. Event → KG mapping table

The authoritative mapping is `core/event_mappings.py:KIND_TO_CLASS`. A kind mapped to `None` is silently dropped; a kind that is absent is an observability gap (warning, dropped; error under `strict`). "Start/end" kinds update one node; "point" kinds create one node per event. Layer names refer to [section 8](#8-layer-defaults-and-control).

### Call nodes (start/end pairs)

| Event kind (`_start` / `_end`) | KG class | Fields read | Notes |
|---|---|---|---|
| `tool_call`, `network_call` | `ToolCall` | `tool_name`, `tool_call_id`, `tool_arguments` / `arguments` / `parameters`, `output` / `result` | `delegate_to_*` tools are rewritten, see [section 5](#5-synthesised-nodes-and-edges). |
| `llm_call` | `LLMCall` | `model`, `llm_name`, `input` / `prompt` / `messages`, `response` (`content`, `usage`, `id`, `choices`, `tool_calls`, `thinking`), `temperature` | `response.thinking` adds a `ThinkingCall`. |
| `execution` | `AgentCall` or `TaskCall` | `agent_id`, `agent_type`, `input`, `output`, `agent_sequence`, `boundary` | `TaskCall` when `boundary == "TaskCall"` or `agent_id` ends in `.task`. |
| `mas_call` | `MASCall` | `mas_name`, `mas_type` | |
| `rag_query` | `RAGQuery` | common fields only | |
| `memory_call`, `memory_store`, `memory_retrieve` | `MemoryCall` | common fields only | |
| `processing_call` | `ProcessingCall` | `processing_name`, `processing_type`, `input`, `messages`, `output` | The end event's `messages` list is rendered as `role: content` lines into `processingOutput`; otherwise `output` is used. |
| `workflow_transition` | `ProcessingCall` | as `processing_call` | No dedicated ontology class. |
| `skill_execution` | `SkillCall` | `skill_name`, `input`, `output` / `result`, `skill_version` | |
| `parallel_group`, `branch` | `ParallelGroup`, `Branch` | common fields only | Trajectory layer. |

"Common fields" are set by `upsert_call` for every call node: `callId`, `executionId`, `agentId`, `parentCallId`, `kindBase`, `masUri`, `spanLevel`, `runId`, `startTime`, `endTime`, `status`, `sourceRecordIds`.

### Annotation nodes (point events, no `call_id`)

Each becomes a `CallAnnotation` (or `GovernanceEvent`) and is later linked to its tightest enclosing call of the same agent by `annotates`. `routing` and `routing_result` use `source_agent_id` as the agent when `agent_id` is null.

| Event kinds | KG class | Layer |
|---|---|---|
| `routing`, `routing_result` | `CallAnnotation` | trajectory |
| `context_assembled`, `state_update_start/end`, `agent_communication_start/end`, `checkpoint_start/end`, `observability_pre_execute_start/end`, `observability_post_execute_start/end`, `user_input`, `user_output`, `client_response`, `tool_result_injected`, `boundary_ingress`, `context_steer`, `compaction`, `wait_state_start/end` | `CallAnnotation` | always on |
| `audit`, `policy_denial`, `policy_allow`, `budget_event`, `transformation_event`, `control_intervention`, `hitl_gate`, `hitl_request`, `governance_denied`, `governance_checked`, `governance_decision`, `governance_event`, `governance_policy`, `obs_wrap_gov_authorize_start/end`, `obs_wrap_gov_validate_start/end`, `governance_authorize_start/end`, `governance_validate_start/end` | `GovernanceEvent` | governance |
| `context_part_contributed` | `ContextContribution` | provenance |
| `infrastructure_info` | `Worker` | infrastructure |

Annotation payload fields copied from the event (`_ANN_KEY_MAP` in `handlers/annotation.py`): `source_agent_id`, `target_agent_id`, `task`, `correlation_id`, `status`, `segments`, `total_tokens`, `operation`, `target`, `from`, `to`, `message_type`, `policy_id`, `decision`, `reason`, `denied_call_id`, `budget_scope`, `amount`, `intervention_type` (stored camelCase, see [section 6](#6-node-attributes-reference)).

### Suppressed kinds (mapped to `None`)

`prompt_build_start/end`, `user_response`, `human_interaction`, `delegated_agent_output`, `skills_check`, `object_upsert`, `object_link`, `object_model_event`, `user_input_request`, `contract_call_start/end`. These produce no nodes; older traces that still emit them are tolerated.

A `llm_call_start` or `tool_call_start` event that carries a `content` field is treated as a `CallAnnotation` (the OTel round-trip representation of a duplicate start).

## 5. Synthesised nodes and edges

`NativeGraphBuilder.finalize()` or a handler produces these nodes and edges; they do not map one-to-one from events. Edge names below are the names in the final document; internal intermediate names are listed in [section 7](#7-edge-type-reference).

### Session, Run, Agent, Application

- **Session**: one per distinct `run_id`. The `id`/`sessionId` is `session-{run_id}`, except: with `split_session_id` (default) a `<appName>_<uuid>` run id gives `sessionId = <uuid>` and `appName = <appName>`, and a hierarchical run id containing `/` is used as the session id with the first path segment as `appName`. `inputQuery` and `finalResponse` come from the outermost call (the first `MASCall`, else the earliest `AgentCall`); `startTime` / `endTime` are copied from it.
- **Run**: one per `run_id`, `id` = `run_id`. It is retained as a node but, after alignment, has no edges (the internal `contains` / `hasCall` edges from Run are dropped).
- **Agent**: one per distinct `agent_id` seen on `AgentCall` / `MASCall` events (ids `""`, `unknown`, `agent`, `mas` excluded) plus delegation targets. `id` = the agent id. Alignment may add more `Agent`, `MAS`, `LLM`, `Tool`, `Processing` catalog nodes (below).
- **Application** (optional, `application_node=True`): `app:{appName}`, linked by `hasSession` to each `Session`.

### Containment edges

Containment comes from three sources, all emitted under the ontology names `hasMASCall`, `hasAgentCall`, `hasLLMCall`, `hasToolCall`, `hasProcessingCall`, `hasMemoryCall`, `hasSkillCall`, `hasRAGQuery` (chosen by the type of the contained node, see `CONTAINS_TO_OXP` in `core/ontology_align.py`):

1. `_infer_contains_edges`: sweep-line over timed call nodes sorted by `(startTime asc, endTime desc)`; a stack holds the active parents, and the top is the tightest enclosing call. Zero-duration nodes (`startTime == endTime == 0`) are skipped.
2. `parentCallId` links (explicit or backfilled) from `AgentCall` / `MASCall` parents.
3. Topology: every `AgentCall` gets `belongsToMASCall` to the `MASCall`, and the `MASCall` gets `hasAgentCall`; the `Session` gets `hasMASCall`.

A contained `ThinkingCall` is emitted as `hasProcessingCall`; contained `TaskCall` nodes get no containment edge (no ontology name).

### State and Transition nodes (`core/state_transition.py`)

For every `AgentCall`, `LLMCall`, `ToolCall` and `ProcessingCall` that has input or output content, `synthesize_states_and_transitions` creates a final `State`, an initial `State`, and a `Transition` that represents the call:

| Call type | Condition | State ids (input / output) | Transition id | `actionType` |
|---|---|---|---|---|
| `AgentCall` | `inputContent` or rendered output non-empty | `state-agent-{callId}-initial` / `-final` | `trans-agent-{callId}` | `agent_delegation` |
| `LLMCall` | `prompt` or `completion` non-empty | `state-llm-{callId}-prompt` / `-completion` | `trans-llm-{callId}` | `llm_call` |
| `ToolCall` | `toolArguments` or `toolOutput` non-empty | `state-tool-{callId}-args` / `-result` | `trans-tool-{callId}` | `tool_call` |
| `ProcessingCall` | `inputContent` or `processingOutput` non-empty | `state-proc-{callId}-input` / `-output` | `trans-proc-{callId}` | the `context_*` processing name, else `context_assembly` |

All States and Transitions carry `layer = "normalized"` and `sessionId`. `State.semanticType` is `initial` for the input state and `final` for the output state (the `prompt`/`args`/`input` and `completion`/`result`/`output` words appear only in the ids). `State.content` is truncated to 8000 characters; `contentHash` is the SHA-256 of the full content.

**Sibling chaining.** Calls that share a `parentCallId` are ordered by `startTime`. Every call after the first reuses the previous sibling's output state as its own input state, so the input `State` listed in the table is created only for the first call in each group. A call's input is therefore its predecessor's output, giving one content chain per parent.

**Edges per call.** `hasInitialState` and `hasFinalState` (call -> State), `hasState` (call -> both States), `inputTo` (input State -> Transition), `leadsTo` (Transition -> output State), `representsExecution` (Transition -> call) and its inverse `correspondsToTransition` (call -> Transition). Alignment also adds the declared inverse of `leadsTo`, so the output State has an `inputTo` edge to the same Transition as well. The internal `fromState` / `toState` / `realizes` edges are dropped or renamed during alignment ([section 7](#7-edge-type-reference)). The Transition node also keeps `fromState` / `toState` / `realizesCallId` as attributes.

**Session anchors.** The `Session` (and the `MASCall`) gets `hasInitialState` / `hasState` to the first top-level transition's input state, `hasFinalState` / `hasState` to the last one's output state, and `correspondsToTransition` to the first `Transition`.

`ToolCall` with a `barrierId` gets `Transition.edgeType = "parallel"` and `appliedOperator = "parallel_merge"` (otherwise `sequential` / `sequential_compose`). `ProcessingCall` transitions add `transitionDeltaType`, and when token counts are present `contextTokensIn`, `contextTokensOut`, `contextTokensDelta`, `compressionRatio`, `contextStrategy`. The delta type comes from `deltaType` on the node or a map from the processing name (`context_truncation` -> `remove`, `context_summarization` -> `compress`, `context_assembly` / `context_injection` / `prompt_engineering` -> `add`, and so on); the default is `add`.

### ThinkingCall

Created by `handlers/chat.py` on `llm_call_end` when `response.thinking` is non-empty: `callId = {llmCallId}-thinking`, `parentCallId` = the LLM call, `startTime` = the LLM call's start, `endTime = start + 0.85 x (end - start)`, `thinkingContent`, `status = "success"`. The edge `hasThinking` (LLMCall -> ThinkingCall) is emitted. The 85 percent split is a fixed convention, not a measurement.

### Delegation: `callsAgent`

With `rewrite_tool_delegation` (default), a `ToolCall` whose `tool_name` starts with `delegate_to_` is **not** materialised. The handler emits `callsAgent` from the caller (`parent_call_id`, else `agent_id`) to the target agent id, ends the caller's current `AgentCall` visit, and the delegated `AgentCall` is flattened to a sibling under the `MASCall` / `TaskCall` (so delegations are siblings, not nested). If the caller later resumes, a new `AgentCall` `{callId}-resume-{n}` is opened. With the switch off, the `ToolCall` is kept and a `callsAgent` edge is added from it.

### Catalog nodes (`_extract_catalog_layer` and alignment)

One catalog node per distinct name: `Tool` (from `ToolCall.toolName`), `LLM` (from `LLMCall.modelName`), `Skill` (from `SkillCall.skillName`), `Processing` (from `ProcessingCall.processingName`), each with `callCount` and `block = "structural"`. Alignment adds, if missing, a `MAS` node (named after `Session.appName`, else `mas`) and `Agent` / `LLM` / `Tool` / `Processing` catalog nodes for every call, with `declared = false`. Call nodes are linked to them by `executesAgent`, `executesLLM`, `executesTool`, `executesProcessing`, `executesMAS`, plus the generic `executes`; `Session` gets `executesSession` and `executes` to the `MAS`; `Agent` gets `belongsToMAS`.

### Annotation resolution

`_resolve_annotation_edges` links each annotation to the call of the same `agentId` whose `[startTime, endTime]` window contains the annotation timestamp and is narrowest; if none, to the most recently ended call of that agent. `CallAnnotation` / `GovernanceEvent` get `annotates` (annotation -> call). `ContextContribution` gets `contributesTo` (to the call, or directly to `llm_call_id` when the event names it). `derivedFrom` links a contribution to each id in its `parents[]` that exists in the trace.

## 6. Node attributes reference

Attribute names are camelCase. Which attributes exist depends on which events carried the source field; absent source fields mean absent attributes (or the defaults noted).

### Common to all nodes (added by alignment, `core/ontology_align.py`)

| Attribute | Notes |
|---|---|
| `node_type`, `@type` | Class name. |
| `id` | See [section 9](#9-node-id-stability). |
| `canonicalId` | `urn:mas:session:{sessionId}:call:{callId}` (calls), `...:annotation:{annotationId}`, `...:state:...`, `...:transition:...`; `urn:mas:session:{id}` for Session, `urn:mas:run:{runId}` for Run, `urn:mas:global:{type}:{slug}` for catalog nodes. |
| `name` | Filled from `name` / `agentName` / `masName` / `llmName` / `toolName` / `processingName` / `skillName` / `appName` / `agentId`, else `"{Type} {id}"`. |

### Call nodes (`MASCall`, `AgentCall`, `TaskCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `SkillCall`, `MemoryCall`, `RAGQuery`, `ThinkingCall`, `ParallelGroup`, `Branch`)

| Attribute | Source |
|---|---|
| `callId`, `executionId` | `event.call_id`; `exec-{Type}-{callId}` |
| `agentId` | `event.agent_id` |
| `parentCallId` | `event.parent_call_id`, routing-derived, or time-enclosure backfill; empty string for roots |
| `kindBase` | event kind without `_start` / `_end` |
| `masUri`, `spanLevel` | ontology lookup (`mas:` IRI, `spanLevel` annotation) |
| `runId`, `sessionId` | `run_id`; session id of the run (not on `ParallelGroup` / `Branch`) |
| `startTime`, `endTime` | `event.timestamp` of the start / end event (epoch seconds) |
| `status` | end event `status`, default `success` |
| `sourceRecordIds` | ordered `event_id`s |
| `duration`, `success`, `spanId`, `parentSpanId` | added by the OXP models and alignment; `duration` is in milliseconds |

### Class-specific attributes

| Node | Attributes |
|---|---|
| `Session` | `sessionId`, `executionId` (`exec-Session-{run_id}`), `runId`, `appName`, `inputQuery`, `finalResponse`, `startTime`, `endTime`, `duration`, `success` |
| `Run` | `runId`, `declared`, `description` |
| `Agent` | `agentId`, `declared`, `description` |
| `MASCall` | `masName` (`mas_name`, else `agent_id`), `masType` |
| `AgentCall` | `agentName`, `agentType`, `agentSequence` (optional), `inputContent`, `outputRaw` (`execution_end.output`), `outputParts` (structured list), `outputContent` (rendered from the parts) |
| `TaskCall` | `taskName`, `taskType` |
| `LLMCall` | `llmName`, `modelName`, `provider` (`vertex_ai/gemini` splits to provider + model; else `unknown`), `prompt`, `completion`, `toolCalls` (structured list), `promptTokenCount`, `completionTokenCount`, `totalTokenCount`, `cacheReadTokenCount`, `finishReason`, `responseId`, `temperature` |
| `ToolCall` | `toolName`, `toolCallId`, `toolArguments` (JSON string), `toolOutput`, optional `barrierId` |
| `ProcessingCall` | `processingName`, `processingType`, `inputContent`, `processingOutput` |
| `SkillCall` | `skillName`, `skillInput`, `skillOutput`, optional `skillVersion` |
| `MemoryCall`, `RAGQuery` | common call attributes only |
| `ParallelGroup`, `Branch` | common call attributes except `sessionId`, plus `layer = "normalized"` |
| `ThinkingCall` | `thinkingContent`, `kindBase = "thinking_call"`, `spanLevel = "thinking"` |
| `CallAnnotation` | `annotationId`, `agentId`, `kind`, `timestamp`, `runId`, plus any `_ANN_KEY_MAP` fields present: `sourceAgentId`, `targetAgentId`, `task`, `correlationId`, `status`, `segments`, `totalTokens`, `operation`, `target`, `from`, `to`, `messageType`. `callId` / `parentCallId` appear only on duplicate-start annotations. Checkpoint events add `annotationKind` and `block = "governance"`. |
| `GovernanceEvent` | as `CallAnnotation`, plus `policyId`, `decision`, `reason`, `deniedCallId`, `budgetScope`, `amount`, `interventionType` when present; `annotationKind` and `block = "governance"` are set for the kinds listed in `handlers/annotation.py` (the nine core governance kinds and any `governance_*` kind). |
| `ContextContribution` | `partId`, `parents`, `source`, `sectionId`, `sourceType` (default `unknown`), `accessMechanism` and `mechanism` (default `inject`), `cause` (default `context_manager`), `causeType` (default `deterministic`), `tokenEstimate` (default 0), `retained` (default true), `evictionReason` (only when not retained, default `budget_exceeded`), optional `sensitivity`, `content`, `contentPreview` (first 200 chars), `timestamp`, `runId`. The `id` is `cpr-{part_id}`. |
| `State` | `stateNodeId`, `contentHash`, `content`, `semanticType` (`initial` / `final`), `sessionId`, `layer`; `deltaType` (and `contextWindowSize`) for tool and processing states |
| `Transition` | `transitionId`, `fromState`, `toState`, `realizesCallId`, `edgeType` (`sequential` / `parallel`), `actionType`, `appliedOperator`, `transitionTimestamp`, `transitionDuration` (seconds, or null), `sessionId`, `layer`; processing transitions add `transitionDeltaType`, `contextTokensIn` / `Out` / `Delta`, `compressionRatio`, `contextStrategy`; tool transitions add `barrierId` |
| `Tool`, `LLM`, `Skill`, `Processing` (catalog) | `name`, `callCount`, `block = "structural"`, `declared`, `description`; `LLM` adds `provider` |
| `MAS` | `name`, `block`, `declared`, `description` |
| `Worker` | declared in the extension TTL; no handler fills it (see [section 8](#8-layer-defaults-and-control)) |
| `Application` | `appName`, `block`, `masUri` |

## 7. Edge type reference

`NativeGraphBuilder` first builds edges with the original Neo4j-style names (`contains`, `hasCall`, `ofToolType`, `realizes`, ...). `align_graph` then rewrites them to the ontology vocabulary and adds topology edges. The final document therefore contains the names in the first table below, not the internal ones.

### Edges in the output document

| Edge type | From -> To | Origin |
|---|---|---|
| `hasMASCall` | `Session` -> `MASCall` | topology |
| `hasAgentCall` | `MASCall` -> `AgentCall` (also `AgentCall` -> `AgentCall` when time-enclosed) | topology, `contains` |
| `hasLLMCall`, `hasToolCall`, `hasProcessingCall`, `hasMemoryCall`, `hasSkillCall`, `hasRAGQuery` | enclosing call -> contained call (named by the contained type; `ThinkingCall` uses `hasProcessingCall`) | `contains`, `parentCallId` |
| `belongsToMASCall` | `AgentCall` -> `MASCall` | topology |
| `belongsToMAS` | `Agent` -> `MAS` | topology |
| `executes` | call or `Session` -> catalog element | topology (generic form of the next six) |
| `executesSession` | `Session` -> `MAS` | topology |
| `executesMAS`, `executesAgent`, `executesLLM`, `executesTool`, `executesProcessing` | call -> `MAS` / `Agent` / `LLM` / `Tool` / `Processing` | topology; also renamed from `ofLLMType`, `ofToolType`, `invokesProcessing`, `invokesSkill` |
| `hasInitialState`, `hasFinalState`, `hasState` | call, `Session`, `MASCall` -> `State` | State synthesis |
| `inputTo` | `State` -> `Transition` (input State, and the output State as inverse of `leadsTo`) | State synthesis |
| `leadsTo` | `Transition` -> `State` | State synthesis |
| `representsExecution` | `Transition` -> call | renamed from `realizes` |
| `correspondsToTransition` | call, `Session`, `MASCall` -> `Transition` | inverse of `representsExecution` |
| `annotates` | `CallAnnotation` / `GovernanceEvent` -> call | annotation resolution |
| `contributesTo` | `ContextContribution` -> `LLMCall` (or enclosing call) | handler or annotation resolution |
| `derivedFrom` | `ContextContribution` -> `ContextContribution` | `_derived_from_edges` |
| `hasThinking` | `LLMCall` -> `ThinkingCall` | `handlers/chat.py` |
| `callsAgent` | call -> `Agent` | `handlers/tool.py` |
| `hasSession` | `Application` -> `Session` | optional (`application_node`) |

### Internal names rewritten during alignment

| Internal edge | Becomes |
|---|---|
| `contains`, `hasCall` | `has<Type>Call` by destination type; dropped when an endpoint is a `Run`, or the destination type has no mapping (for example `TaskCall`) |
| `fromState`, `toState` | dropped (`inputTo` / `leadsTo` carry the same information) |
| `realizes` | `representsExecution` |
| `ofToolType`, `ofLLMType`, `invokesProcessing`, `invokesSkill` | `executesTool`, `executesLLM`, `executesProcessing`, `executesProcessing` |

`hasThinking`, `derivedFrom`, `hasRAGQuery` and `hasSession` are produced but are not declared in `mas-ontology.ttl` or the extension TTL, so the `unknown_edge_types` check of `mas-lab kg validate` reports them (see [ontology.md](ontology.md#12-verification)).

## 8. Layer defaults and control

Four boolean flags control which event kinds reach the KG. They are applied before any class lookup: filtered events produce no nodes or edges. The sets are `LAYER_KINDS` in `core/event_mappings.py`.

| Flag | Default | Event kinds in the layer | Effect when `False` |
|---|---|---|---|
| `include_infrastructure` | `False` | `infrastructure_info` | No `Worker` nodes (see note below). |
| `include_trajectory` | `True` | `parallel_group_start/end`, `branch_start/end`, `routing`, `routing_result` | No `ParallelGroup` / `Branch` nodes and no routing annotations; routing-derived delegation parents are not inferred, so those parents come from time enclosure instead. |
| `include_provenance` | `False` | `context_part_contributed` | No `ContextContribution` nodes or `contributesTo` / `derivedFrom` edges. |
| `include_governance` | `False` | the `GovernanceEvent` kinds listed in [section 4](#4-event-kg-mapping-table) | No `GovernanceEvent` nodes. |

The structural core (all call kinds in [section 4](#4-event-kg-mapping-table)) and the always-on annotation kinds cannot be switched off. CLI: `mas-lab kg normalize --include-governance`, `--no-include-trajectory`, and so on. The flags are recorded in `metadata.layers`.

Note on `Worker`: `infrastructure_info` maps to `Worker`, but no handler is registered for it. It falls through to the generic call handler, which creates a node only if the event has a `call_id`. Treat the infrastructure layer as reserved.

### 8.1 Terminology: layer, `block`, ontology `layer`

Three similarly named things exist.

- **Feature layers** (above): runtime filters over event kinds.
- **`block`** attribute on nodes: coarse partition used by validation. Allowed values are `structural`, `execution`, `trajectory`, `governance`. `check_block_vocabulary` expects `structural` for `StructuralElement` subclasses, `trajectory` for `TrajectoryElement` subclasses and `execution` for everything else. Catalog nodes carry `block = "structural"`; `GovernanceEvent` nodes set `governance`. The Neo4j push fills a missing `block` per node type (see [neo4j.md](neo4j.md)).
- **`layer`** attribute: only `TrajectoryElement` classes (`State`, `Transition`, `ParallelGroup`, `Branch`) carry it, with the single value `normalized` (`check_layer_vocabulary`).

### 8.2 Where classes are defined

| Source | Classes |
|---|---|
| `oxp-ontology` (`mas-ontology.ttl`) | `Session`, `MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `State`, `Transition`, `MAS`, `Agent`, `LLM`, `Tool`, `Processing`, `Capability`, `CapabilityCall` |
| `library-kg/ontology/extensions/mas-kg-native-extensions.ttl` | `RAGQuery`, `MemoryCall`, `SkillCall`, `Skill`, `ThinkingCall`, `CallAnnotation`, `GovernanceEvent`, `ContextContribution`, `ParallelGroup`, `Branch`, `Worker`, `Run`, `Application` |

`TaskCall` is not declared in either file (it is accepted by the native builder and Neo4j push but reported as an unknown node type by `mas-lab kg validate`). Normalization resolves ontology files through `mas.library.kg.ontology`, which loads `mas-ontology.ttl` from `oxp-ontology` and adds the extension TTL; it is not a single-file snapshot. If `oxp-ontology` or `rdflib` cannot be loaded, `build_kg_document` logs a warning and continues without the ontology-derived `masUri` / `spanLevel` enrichment.

## 9. Node ID stability

Deterministic ids make re-normalizing the same trace idempotent and let Neo4j `MERGE` deduplicate. The final id is assigned in `finalize()` by `_node_human_id`.

| Node type | ID pattern | Example |
|---|---|---|
| Call nodes (`MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, ...) | `{type lowercased}:{run slug}:{agent slug}:{callId}` | `agentcall:demo:planner:tp-exec-001` |
| `CallAnnotation`, `GovernanceEvent` | `ann-{uuid5(run_id\|agent_id\|kind\|timestamp)[:16]}`; duplicate-start annotations (with a `callId`) use the call pattern with type `callannotation` | `ann-a83e9a4a96ac55ae` |
| `ContextContribution` | `cpr-{part_id}` (fallback: the annotation hash) | `cpr-9bb51fd9-...` |
| `State` | `state-{agent\|llm\|tool\|proc}-{callId}-{initial\|final\|prompt\|completion\|args\|result\|input\|output}` | `state-agent-tp-exec-001-initial` |
| `Transition` | `trans-{agent\|llm\|tool\|proc}-{callId}` | `trans-llm-tp-llm-001` |
| `Session` | see [section 5](#5-synthesised-nodes-and-edges) | `session-demo` |
| `Run` | `run_id` | `demo` |
| `Agent` | the `agent_id` string (catalog agents added by alignment: `catalog:agent:{slug}`) | `itinerary_agent` |
| `ThinkingCall` | call pattern with `callId = {llmCallId}-thinking` | |
| `Tool`, `Skill`, `Processing` catalog | `catalog:tool:{name}`, `catalog:skill:{name}`, `catalog:processing:{name}` | `catalog:tool:search_flights` |
| `LLM` catalog | `catalog:model:{modelName}` (from calls), `catalog:llm:{slug}` (added by alignment when no model name) | `catalog:model:gemini-2.5-flash` |
| `MAS` | `catalog:mas:{appName or mas}` | `catalog:mas:mas` |
| `Application` | `app:{appName}` | `app:trip-planner` |

Because `State` / `Transition` ids embed the original `callId` (not the rewritten call node id), they are stable for a given `call_id`. The set of `State` nodes can still differ between runs of the same trace shape because of sibling chaining ([section 5](#5-synthesised-nodes-and-edges)).

Every node also carries a `canonicalId` URN; edges carry `id = "{edge_type}:{from_id}->{to_id}"` and a `canonicalId` of the form `urn:mas:session:{session}:edge:{type}:{sha1[:16]}`.

### Content hash versus identity

`State.contentHash` is the SHA-256 of the state content. It is distinct from the node id: the same content in two sessions gives the same `contentHash` and different ids, so embeddings can be reused by content hash without merging state identities across traces.

### AgentCall merge key

Spans for different agents may share one runtime `call_id`. `call_nodes` is therefore keyed by `_call_nodes_merge_key(call_id, local_name, agent_id)`: `"{call_id}::{agent_id}"` for `AgentCall`, plain `call_id` for everything else.

For the ontology of these types, see [ontology.md](ontology.md). For the pipeline steps that wrap normalization (`run_normalize`, `run_normalize_otel`), see [steps.md](steps.md). For Neo4j push and query patterns, see [neo4j.md](neo4j.md).

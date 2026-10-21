<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Trajectory and metric file schemas

This page describes the files a MAS run writes: the event stream (`events.jsonl`), the benchmark run entries, and the OpenTelemetry (OTel) span export.
It is for people who read traces, write evaluation plugins, or convert traces between formats.

Print the kind and agent of each event of a run:

```bash
jq -c '{kind, agent_id}' traces/events.jsonl
```

The file families are:

1. **Event stream**: `traces/events.jsonl`, written by the runtime for every run (`mas-ctl chat`, `run-mas`, benchmarks). See also [docs/cli/observability.md](../../docs/cli/observability.md).
2. **Benchmark run entries**: for `mas-lab benchmark run`, each run is stored in the content-addressed trace cache with the metadata files below.
3. **Metric files**: written by the benchmark pipeline into the pipeline output directory. The metric types are `MetricValue` and `MetricSpec` in `lab/src/mas/lab/evaluation/eval_contract.py`.

The schemas are identical across use cases.

---

## Run directory layout

A single `mas-ctl` run writes `traces/events.jsonl` relative to the working directory. Override the path with `--events-file`; disable the file with `--no-events`. A benchmark run entry looks like:

```text
<trace-cache>/<run-hash>/
├── run.json          # Immutable input fingerprint (what produced this hash)
├── inputs.json       # Full run inputs
├── result.json       # status, elapsed_ms, error, executed_at
└── traces/
    └── events.jsonl  # Ordered stream of events (one JSON object per line)
```

`mas-lab config` shows the trace-cache location. Benchmark run directories reference their entry (`.run_ref`, or an inline `traces/`).

### `run.json`

One JSON object, written once per cache entry and never overwritten:

```jsonc
{
  "run_hash": "…",               // content hash of the run inputs
  "created_at": "2026-02-23T23:24:48.694866Z",
  "item_id": "1",                // dataset item
  "run_idx": 0,                  // repetition index
  "model": "gpt-4o-mini",
  "api_base": "",
  "prompt": "what trains go from Celestia to Verdantia?"
}
```

Outcome metadata is in `result.json`: `status` (`"ok"` or `"error"`), `elapsed_ms`, `error`, `executed_at`.

---

## `events.jsonl`

The file is newline-delimited JSON: each line is one event, in chronological order. `timestamp` is a Unix epoch float in seconds.

Every event carries these fields:

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `string` | Event type, one of the `Kind.*` constants in `mas.lab.evaluation.events` |
| `agent_id` | `string` | Emitting agent identifier (matches the agent id in the MAS manifest) |
| `timestamp` | `float` | Unix epoch seconds |

The observability layer adds correlation and classification fields to most events:

| Field | Description |
|-------|-------------|
| `call_id`, `parent_call_id` | Interval identity and its parent interval; `*_start` / `*_end` pairs share a `call_id` |
| `correlation_id` | Ties the envelope events of one egress operation together |
| `turn_id` | User turn (`u1`, `u2`, ...) |
| `session_id`, `task_id` | Session and task identifiers |
| `app_name`, `mas_id` | Application and MAS names |
| `block`, `layer`, `summand`, `mealy_symbol` | Position of the event in the Mealy product (for example `summand: model`, `mealy_symbol: LLM_CALL`) |
| `run_id` | Run identifier; empty for single `mas-ctl` runs |

The examples below omit these fields and show the payload fields specific to each kind. Further keys may be present.
Streams also contain governance and envelope events (`governance_decision`, `boundary_ingress`, `governance_authorize_start` / `_end`, `hitl_gate`, ...), `state_update_start` / `_end`, `mas_call_start` / `_end`, `processing_call_start` / `_end`, `context_part_contributed`, `context_steer`, and `client_response`. The authoritative list is `Kind` in `lab/src/mas/lab/evaluation/events.py`.

### Event kinds

| Kind | Emitted when | Payload fields |
|------|--------------|----------------|
| `execution_start` | An agent begins a task (entry point or delegation) | `input`: the prompt or delegated task text |
| `execution_end` | An agent finishes | `status`: `"ok"`, or the failure class |
| `llm_call_start` | Just before an LLM request is dispatched | `messages`: the array sent to the model, when available (also on the preceding `context_assembled` event) |
| `llm_call_end` | After the LLM response arrives | `output`: model text; `next_step`: what the design pattern schedules next (`"TOOL_CALL"`, `"FINALIZE"`, ...). When set: `tokens_used` (provider usage), `finish_reason`, `model`, `max_tokens`, `truncation_retries`, `tools`, `cache_status`, `cache_layer`, `cache_events` |
| `tool_call_start` | The runtime dispatches a tool call, including delegation tools such as `delegate_to_<agent>` | `tool_name`; `arguments`: what the model supplied |
| `tool_call_end` | The tool returns | `tool_name`; `output`: tool or sub-agent result text |
| `user_response` | The entry-point agent emits its final answer | `content`: the rendered answer; `message_type`: `"info"`, `"warning"`, or `"error"` |
| `context_assembled` | The context assembler finishes, before each LLM call | `segments`, `total_tokens`, `message_count`, `messages` (see below) |
| `audit` | Any plugin calls `recorder.audit(payload)` | `payload`: arbitrary plugin-specific object |

Examples:

```jsonc
{ "kind": "execution_start", "agent_id": "moderator", "timestamp": 1771889088.7677,
  "input": "what trains go from Celestia to Verdantia?" }

{ "kind": "llm_call_end", "agent_id": "moderator", "timestamp": 1771889092.6869,
  "output": "Based on your request ...", "next_step": "TOOL_CALL" }

{ "kind": "tool_call_start", "agent_id": "moderator", "timestamp": 1771889092.6896,
  "tool_name": "delegate_to_schedule_agent",
  "arguments": {"task": "Plan a trip from A to B"} }

{ "kind": "user_response", "agent_id": "moderator", "timestamp": 1771889113.6235,
  "content": "Here is the schedule for trains from Celestia to Verdantia ...",
  "message_type": "info" }
```

### `context_assembled`

The context assembler emits this event just before each LLM call. It carries the segment map and the `messages` array sent to the model. See [context-segmentation.md](context-segmentation.md).

```jsonc
{
  "kind": "context_assembled",
  "agent_id": "moderator",
  "timestamp": 1771889088.7681,
  "segments": 2,          // number of context parts
  "total_tokens": 253,    // estimated
  "message_count": 2,
  "messages": [
    { "role": "system", "content": "You are the Trip Planner Moderator ..." },
    { "role": "user",   "content": "what trains go from Celestia to Verdantia?" }
  ]
}
```

### `audit`

Plugins use `audit` for structured logging of non-standard events, for example budget gate decisions and checkpoint saves:

```jsonc
{
  "kind": "audit",
  "agent_id": "moderator",
  "timestamp": 1771889113.62,
  "payload": { "event": "budget_gate_triggered", "total_cost_usd": 1450.0, "budget_limit": 1200.0 }
}
```

---

## Nested interval view

Events form a nested interval tree, linked by `call_id` and `parent_call_id`:

```text
MAS call:
  Agent:    execution_start(coordinator)  ───────────────────────────────── execution_end(coordinator)
              context_assembled_1 + llm_call_start_1  ── llm_call_end_1
              tool_call_start(delegate_to_worker) ────────────────────────── tool_call_end
                Agent: execution_start(worker) ──── execution_end(worker)
                  context_assembled_2 + llm_call_start_2  ── llm_call_end_2
                  tool_call_start(get_data) ── tool_call_end(get_data)
              context_assembled_3 + llm_call_start_3  ── llm_call_end_3
              user_response
```

Each `tool_call_end` at level N provides the facts available to the next `context_assembled` event at level N-1.

---

## OTel export

Pass `--events-format otel` (or `format: otel` in the manifest `observability` block) to write spans as OTLP JSON (`resourceSpans` envelope). Evaluation plugins in the OSS tree read the raw JSONL events directly. `mas-lab otel` and `mas-lab telemetry` convert between native events and OTLP spans.

```jsonc
{
  "resourceSpans": [{
    "scopeSpans": [{
      "spans": [{
        "name": "ToolCall",
        "startTimeUnixNano": "1771889092000000000",
        "endTimeUnixNano":   "1771889098000000000",
        "attributes": [
          { "key": "mas.agent.id",       "value": { "stringValue": "moderator" } },
          { "key": "mas.tool.name",      "value": { "stringValue": "delegate_to_schedule_agent" } },
          { "key": "mas.tool.arguments", "value": { "stringValue": "{}" } },
          { "key": "mas.tool.result",    "value": { "stringValue": "..." } }
        ]
      }]
    }]
  }]
}
```

Recognised `name` values: `ToolCall`, `LLMCall`, `AgentCall`, `TaskCall`, `MASCall`, `RAGQuery`, `MemoryCall`, `ProcessingCall`.

An OTel span holds both start and end in one record. Conversion back to native events expands each span into an `(e_start, e_end)` event pair.

---

## See also

- [context-segmentation.md](context-segmentation.md): how context is assembled and attributed
- `lab/src/mas/lab/evaluation/events.py`: `Kind`, `obs_event()`
- `lab/src/mas/lab/evaluation/eval_contract.py`: `MetricValue`, `MetricSpec`, `EvalContract`

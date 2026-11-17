# Native events → OTel spans (conversion)

Conversion turns `events.jsonl`, the structured trace of native MAS execution events, into OpenTelemetry (OTel) spans. It is the core transform of `library-telemetry` and the inverse of `library-kg`, which reads OTel spans and produces a knowledge graph. Read this page to run a replay or to add support for a new event kind.

```python
from mas.library.telemetry.conversion import replay_events_file

n = replay_events_file("events.jsonl", "otel_sdk_spans.jsonl", app_name="my-app")
```

Each `*_start` / `*_end` pair becomes one interval span. A singleton event becomes a point (zero-width) span.

```
events.jsonl
   │  replay_events_file()  /  MasOtelConverter.process_event()
   │  • dispatch each event kind to a registered handler
   │  • reconstruct the span tree from call_id / parent_call_id
   │  • preserve timestamps (float seconds → ns)
   ▼
otel_sdk_spans.jsonl   (one ReadableSpan JSON per line)
```

## Two modes

**Offline replay** (batch, from a file) — the common case:

```python
from mas.library.telemetry.conversion import replay_events_file

n = replay_events_file(
    "runs/item0/traces/events.jsonl",
    "runs/item0/traces/otel_sdk_spans.jsonl",
    service_name="my-app",
    app_name="my-app",
)
```

**Live** (during agent execution): a plugin builds a minimal event dict per hook and calls `converter.process_event(event)`. The same handlers run in both modes, so replayed traces are span-for-span identical to live ones (the parity tests enforce this).

```python
from mas.library.telemetry.conversion import MasOtelConverter, ExportLayers

# `tracer` is an OpenTelemetry SDK tracer whose provider uses an exporter such
# as JSONLineFileSpanExporter (see conversion/replay.py for the wiring).
converter = MasOtelConverter(tracer, app_name="my-app",
                             export_layers=ExportLayers(governance=True))
for event in events:
    converter.process_event(event)
converter.finish()
```

## Design: thin core and category handlers

As in `library-kg`, the per-`kind` logic is not one flat dispatch table. Category modules that mirror the ontology blocks hold the handlers, and a decorator registry assembles them.

```
conversion/
├── converter.py        stateful span-tree core + primitive API (open/close/point)
├── exporter.py         JSONLineFileSpanExporter
├── replay.py           replay_events_file — offline batch entry point
├── layers.py           ExportLayers — which layers to emit
├── envelope.py         kind → (block, summand, mealy) — the categorization table
├── tool_name.py        resolve_tool_name
└── mappings/
    ├── base.py         register() decorator + SpanEmitter protocol + build_handler_table()
    ├── structural.py   mas_call (TaskCall), execution (AgentCall), infrastructure (Worker)
    ├── execution.py    llm / tool / processing / skill / network / workflow / communication
    ├── memory.py       memory_store / memory_read / memory_retrieve / rag_query
    ├── context.py      context_assembled / context_part / state_update / compaction
    ├── governance.py   governance / policy / hitl / budget / control
    └── trajectory.py   routing / user I/O / parallel_group / obs_wrap_gov
```

A handler is a plain function `handler(conv, event) -> None`. It does not touch converter internals; it emits spans through the `SpanEmitter` primitive API:

| Primitive | Purpose |
|-----------|---------|
| `conv.open_span(call_id, name, attrs, parent_call_id, start_ns)` | open an interval span |
| `conv.close_span(call_id, extra, status, end_ns)` | close it |
| `conv.point_span(name, attrs, parent_call_id, ts_ns, call_id)` | zero-width point span |
| `conv.ts_ns(ev)` / `conv.agent_id(ev)` / `conv.require_call_id(ev)` / `conv.enc(v)` | helpers |
| `conv.is_open(id)` / `conv.is_closed(id)` | span-state queries |
| `conv.emit_duplicate_start_annotation(ev, kind)` | dedupe repeated `*_start` |

### Extending the mapping

Adding a new event `kind` takes two steps, and `converter.py` does not change:

1. Add a row to `envelope.KIND_ENVELOPE` (`kind → (block, summand, mealy_symbol)`)
   so the event is categorised and layer-gated correctly.
2. Add a handler in the category module matching its block, decorated with
   `@register`:

```python
# conversion/mappings/execution.py
from mas.library.telemetry.conversion.mappings.base import register, SpanEmitter

@register("embedding_call_start")
def h_embedding_call_start(conv: SpanEmitter, ev):
    conv.open_span(conv.require_call_id(ev), "EmbeddingCall", {
        "mas.boundary": "EmbeddingCall",
        "mas.agent.id": conv.agent_id(ev),
        "mas.embedding.model": ev.get("model") or "unknown",
    }, ev.get("parent_call_id"), start_ns=conv.ts_ns(ev))

@register("embedding_call_end")
def h_embedding_call_end(conv: SpanEmitter, ev):
    conv.close_span(ev.get("call_id"), status=ev.get("status", "success"), end_ns=conv.ts_ns(ev))
```

Third-party code can register handlers by calling `register` before it constructs a converter. One handler can serve several kinds: `@register("policy_allow", "policy_denial")`.

Verify what is registered:

```bash
mas-lab telemetry kinds            # JSON list of the registered event kinds
```

```python
from mas.library.telemetry.conversion import registered_kinds
print(len(registered_kinds()))   # 76 built-in kinds at the time of writing
```

## LLM, processing and status attributes

- `llm_call_start` carries the request: `mas.llm.model`, `mas.llm.messages`, `mas.llm.tools` (offered tool names), `mas.llm.max_tokens`, `mas.llm.temperature`. `llm_call_end` carries the return: `mas.llm.response`, `mas.llm.tool_calls` (structured, `{toolName, arguments}`), finish reason, token counts. A `max_tokens` on the end is the one actually sent, written only when it differs from the start's.
- A `processing_call` is exported as a `*.processing` span by default (the `observe_sdk` profile), with `mas.processing.input`, `mas.processing.messages` and `mas.processing.output`.
- `llm_call_start` also carries `reasoning_effort` (`gen_ai.request.reasoning_effort`), and `llm_call_end` the provider's reasoning token count (`completion_tokens_details.reasoning_tokens`, as `gen_ai.usage.reasoning_tokens`). Neither name is fixed by the GenAI semantic conventions yet; these are the names SCOPE norm reads. A token count is not a reasoning interval: no thinking span is made from it.
- `mas.status` is the end event's status. A call that never reported its end (an interrupted trace) stays open and is not exported, as an OTel SDK never exports a span that did not end. No end time or status is made up for it; a realtime consumer saw it start through its `*.started` event.
- `session.end` is written only on `mas_call_end`. A cut-off run's session stays open; there is no fallback end. It carries `execution.success` from the `mas_call_end` status: `ok`, `success`, `true` and `completed` (`semconv.SUCCESS_STATUS`) are a success, anything else a failure. The native fold applies the same rule.

## Observe-sdk span names and attributes

Under the `observe_sdk` profile, span names end in the suffix SCOPE norm dispatches on, and the attributes are the ones its execution-extension contract reads:

| Native record | Span | Attributes |
|---------------|------|------------|
| any `CallAnnotation` kind | `{agent}.annotation`, one point span per record | `mas.annotation.kind` (the native kind), `mas.annotation.content` (the record's payload, the same fields as the native fold: `semconv.ANNOTATION_PAYLOAD_FIELDS`), `ioa_observe.annotates.span_id` (the target) |
| `infrastructure_info` | `{agent}.worker`, child of the execution it was reported under | `mas.worker.id`, `mas.worker.pid`, `mas.worker.durable_backend` |
| `thinking_start` / `thinking_end` | `{agent}.thinking`, child of the `*.chat` span of its LLM call | `ioa_observe.state.*` (input: the LLM call's prompt State; output: the reasoning State), `gen_ai.provider.name`, `gen_ai.request.model` |
| `skill_execution_*`, `memory_*`, `rag_query_*` | `{skill}.skill`, `{agent}.memory`, `{agent}.rag` | `ioa_observe.agent.span_id` (the agent span), `mas.memory.operation` in the ontology vocabulary (`store`, `retrieve`, `update`, `delete`; a RAG query is `retrieve`), `mas.memory.collection` (the store the record names), `ioa_observe.entity.output` |
| `context_part_contributed` | `{agent}.context` | `mas.annotation.content` (the payload, `semconv.CONTEXT_PAYLOAD_FIELDS`), `ioa_observe.annotates.span_id`: the exported span id of the LLM call the part was contributed to |
| governance decisions | `{agent}.governance` | `mas.governance.decision_type` (the decision the record states: `decision_type`, `decision`, `outcome` or a HITL `resolution`), `mas.governance.policy_name` |
| `governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_*`, `checkpoint_*` | `{agent}.processing` (the check's interval) | `ioa_observe.processing.name` = the check; when the end record states an outcome, a `{agent}.governance` point under it. The bracket is a ProcessingCall of the execution layer, exported by default; the governance decision stays in the opt-in governance layer |

A check bracket has `parent_call_id` = the agent visit it runs in and `checked_call_id` = the call it checks. An authorize bracket runs before the checked call and a validate bracket after it, so neither is inside it. `state_in` and `state_out` of a bracket are the same State: for authorize, the State the checked call consumes; for validate, the output State of the checked call. A `skill_execution_*` span carries the state contract. `user_response` is not exported. A memory span has no defaulted memory type or durable backend, and a context span no defaulted source or mechanism.

Span names follow one rule: `*.agent` is the agent id, `*.chat` the model name, `*.tool` the tool name, `*.processing` the processing name, `*.skill` the skill name, `*.memory` and `*.rag` the memory name, `*.thinking` the model name, and an annotation span its kind.

A memory write closes with what it reports back, else with the data it stored, so its output State is never empty. A context contribution recorded before its LLM call's span started waits for that span (keeping its own timestamp) and is emitted when it starts.

## Realtime runtime events

With `realtime` (the `observability-otel-realtime` Control overlay, or `--realtime`), the end-of-run `*.graph` span is replaced by the ioa-observe runtime events, emitted as each boundary crosses. They are OTel **LogRecords**, the shape the ioa-observe SDK itself emits (`ioa_observe.sdk.tracing.runtime_events`): body and event name `topology.node.started` / `.completed`, `tool.started` / `.completed`, `llm.started` / `.completed`; attributes `event.id` (the SDK's SHA-256 of the other attributes), `event.name`, `event.time`, `session.id`, `application_id`, `agent.name`, `tool.name` / `tool.type` / `tool.input` / `tool.output` / `tool.status`, `llm.name` / `llm.input` / `llm.output` / `llm.call.id`, `agent.input` / `agent.output`, `span.id`, `trace.id` and `ioa_observe.agent.span_id`. `mas_call_start` and `mas_call_end` emit `topology.session.started` and `topology.session.completed`. Every `*.started` record carries the attributes its span was started with (the state contract, `gen_ai.request.*`, `mas.*`). Each record is stamped with the span of the call it reports, so a `*.started` and its `*.completed` name one span id and SCOPE norm's realtime path (`otel_io_realtime`) folds them into one call, unchanged.

The records go to `<spans file stem>_runtime_events.jsonl` next to the spans (`session.runtime_events_path`) and, with an OTLP endpoint, to `/v1/logs`.

## Explicit dataflow (`ioa_observe.state.*`)

The native events name the dataflow: each agent visit is a chain of States (`{exec_call_id}#{n}`), and every element records the State it reads (`state_in`, `state_in_content`) and the State it writes (`state_out`, `state_out_content`). Under the `observe_sdk` profile the converter copies these onto four attributes:

| Attribute | On | Copied from |
|-----------|----|-------------|
| `ioa_observe.state.input.id` | `*.agent`, `*.chat`, `*.tool`, `*.processing`, `session.end` | START `state_in` (a join: the JSON list of member ids). Agent: `execution_start` `state_out`. `session.end`: `mas_call_end` `state_in`. |
| `ioa_observe.state.input.content` | same | START `state_in_content` (a join: the JSON-array string as recorded). Agent: `execution_start` `state_out_content`. `session.end`: `mas_call_end` `state_in_content`. |
| `ioa_observe.state.output.id` | same | START `state_out`. Agent: `execution_end` `state_in`. `session.end`: `mas_call_end` `state_out`. |
| `ioa_observe.state.output.content` | same | END `state_out_content`. Agent: `execution_end` `state_in_content`. `session.end`: `mas_call_end` `state_out_content`. |

The input and output id are set when the span opens; the output content when it closes. A missing field gives no attribute. The raw profile carries none of these.

A parallel group's `aggregate` ProcessingCall reads the member outputs as a join: its input id is a JSON list of ids, its input content a JSON list of the same length.

Delegation adds two ProcessingCall pairs on the caller, `delegate` and `reply` (`ioa_observe.processing.name`). `delegate` reads the delegating tool call's input and writes the callee's first State. `reply` reads the callee's final State and writes the tool call's output. So the callee's `*.agent` input id equals the `delegate` output id, and the `reply` output id equals the delegating `*.tool` output id.

### What the converter does not do

- It does not rewrite `delegate_to_*` tool calls. There is no `rewrite_tool_delegation` option. A delegation is a `*.tool` span of the caller, and the callee's visit is a `*.agent` span. The caller's visit is not split, and no `ioa_observe.agent.previous` or `ioa_observe.handoff.source.*` attributes are written.
- It writes no placeholder or wrapper text. An empty input or output gets no attribute, rather than `input:<agent>:…` or a generated `llm-output:…` id.
- The agent's `mas.output` is its final State (`execution_end`), not the text of `user_response`.

## Export layers

`ExportLayers` gates which events are emitted, keyed off each event's ontology
block (via `envelope.export_layer_for_kind`):

| Layer | Blocks | Default |
|-------|--------|---------|
| `structure` | structural | on |
| `execution` | execution | on |
| `semantic` | context | on |
| `provenance` | trajectory | on |
| `governance` | governance | off |

SCOPE requires the structure, execution and trajectory (`provenance`) layers; `semantic` stays on too. Governance is opt-in:

```python
replay_events_file(..., export_layers={"governance": True})
```

## Determinism

`replay_events_file` seeds a stable session uuid from `(file, service_name)`, so
repeated replays of the same events produce identical span and trace ids, which makes the [span parity](compare-spans.md) checks meaningful.

## References

- Categorization (block / summand / Mealy symbol): the `KIND_ENVELOPE` table in
  `conversion/envelope.py`.
- Inverse transform: `library-kg` `docs/kg-spec.md`.

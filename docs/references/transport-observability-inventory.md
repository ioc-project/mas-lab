<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Transport observability inventory

Recorded against the versions pinned in this tree: **a2a-sdk 1.1.5**,
**mcp 2.2.0**, **slim-bindings 2.2.0**, **opentelemetry-sdk 1.45.2**,
**ioa-observe-sdk 1.1.2**. Span fixtures live in
`library-telemetry/tests/transport/fixtures/<protocol>/<sdk-version>/`.

This page is the evidence for the design in
[transport-observability.md](transport-observability.md). Defaults are in
[Decisions](#decisions).

## How this was recorded

A throwaway recorder (not in the tree) set
`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true` and installed an in-memory
`TracerProvider` **before** importing any protocol SDK. It then ran each
scenario, serialised finished spans (stable `$trace-N` / `$span-N` ids,
timestamps dropped, attribute values capped), and captured the
`opentelemetry.context` logger for
`Token was created in a different Context`.

A2A used the in-process FastAPI app from `library_ioa.plugins.a2a.server.app.build_app`
and `library_ioa.plugins.a2a.client.A2AClient` over JSON-RPC. MCP used
`mcp.Client` against an in-process `MCPServer` with `mode="legacy"` (JSON-RPC
framing, so both client and server SDK spans fire). SLIM used the repo's
`slim_node_fake` bindings and `SlimAgentComm` / `StackHandle`.

## A2A (a2a-sdk 1.1.5)

Instrumentation scope: `a2a-python-sdk` version `1.0.0` (some spans record an
empty version string). Flag `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` is read
**once** at import of `a2a.utils.telemetry` (default true if OpenTelemetry is
installed). Decorators: `@trace_class` / `@trace_function` wrapping
`tracer.start_as_current_span`. **No `attribute_extractor` is wired**, so
recorded SDK spans have **empty attributes**.

### Scenarios

| Scenario | Fixture | SDK spans | Names that carry the call | Token-context error |
| --- | --- | --- | --- | --- |
| Blocking `send_message` | `a2a/1.1.5/blocking_message.json` | 29 | Client `JsonRpcTransport.send_message` + `_send_request`; server `JsonRpcDispatcher.handle_requests` → `_handle_send_message` → `DefaultRequestHandlerV2.on_message_send` | none |
| Streaming `send_message` | `a2a/1.1.5/streaming_message.json` | 28 | Client `send_message_streaming` + `_send_stream_request`; server `on_message_send_stream` + `_process_streaming_request` | none |
| Agent-card fetch (`aget_agent_card`) | `a2a/1.1.5/agent_card.json` | **0** | `A2ACardResolver.get_agent_card` is not decorated | none |
| Handler failure (`RuntimeError` in executor) | `a2a/1.1.5/failure.json` | 66 | Same call spans as blocking, plus many `EventQueueSource.close` / `_dispatch_loop`; `dequeue_event` records `exception` events; `on_message_send` status stays OK (failure is a task state) | none |
| Thread-pool `asyncio.run` bridge | `a2a/1.1.5/thread_pool_bridge.json` | 30 | Same as blocking. Outer loop `asyncio.to_thread` → worker `asyncio.run(A2AClient.asend_message)`. Call succeeded. | **none** |

Most of the 29 blocking spans are queue internals:
`EventQueueSource.enqueue_event`, `_deliver_to_sink`, `dequeue_event`,
`task_done`, `tap`, `remove_sink` (each `SERVER` / `INTERNAL` via
`@trace_class(kind=SpanKind.SERVER)` on the class). Client `close` also
emits a span.

### Typed hooks (verified in installed 1.1.5)

| Seam | Class / method | What it sees |
| --- | --- | --- |
| Client interceptor | `a2a.client.interceptors.ClientCallInterceptor.before(BeforeArgs)` / `.after(AfterArgs)` | `input` is the request (`SendMessageRequest` for send); `method` is `'send_message'` or `'send_message_streaming'`; `agent_card`; `context`. `before` can mutate `args.input` (metadata). Wired by `a2a.client.base_client.BaseClient._execute_with_interceptors` / `_execute_stream_with_interceptors`. Passed in at `create_client(..., interceptors=)` or `Client.add_interceptor`. |
| Extended card | same interceptor, `method='get_extended_agent_card'` | Authenticated card only. The well-known GET is **not** this path. |
| Card resolver | `a2a.client.card_resolver.A2ACardResolver.get_agent_card` | HTTP GET `/.well-known/agent-card.json`. **Not traced. No interceptor.** MAS Lab already wraps this in `library_ioa.plugins.a2a.client.A2AClient.aget_agent_card`. |
| Server handler | `a2a.server.request_handlers.RequestHandler.on_message_send` / `on_message_send_stream` | `params` (message, metadata, `context_id`, `task_id`, `message_id`) and `ServerCallContext`. MAS Lab already subclasses this as `_A2ARequestHandler` in `library_ioa.plugins.a2a.server.app`. Installed `DefaultRequestHandler` is `DefaultRequestHandlerV2`. |
| Executor | `a2a.server.agent_execution.AgentExecutor.execute` / `cancel` | `RequestContext`, `EventQueue`. MAS Lab: `MasLabAgentExecutor` (handler runs in `asyncio.to_thread`). |
| HTTP middleware | FastAPI `@app.middleware("http")` already on `build_app` | Path, body sanitise, agent-card cache headers. Can observe served card GET. |
| MAS Lab client | `library_ioa.plugins.a2a.client.A2AClient.asend_message` / `aget_agent_card` | Prompt, `context_id`, `metadata`. Does **not** currently pass interceptors into `create_client`. |
| MAS Lab link | `library_ioa.plugins.a2a.agent_comm.A2AAgentComm.asend` / `aprobe` | Peer send and fresh card fetch. |

There is **no** A2A SDK callback that yields message content on the transport
span: meaning has to come from the interceptor or from `_A2ARequestHandler`.

### observe-sdk wrappers (`ioa_observe.sdk.instrumentations.a2a`)

Monkeypatches. Each wrapper: (1) injects `metadata["observe"]` (`session_id`,
`last_agent_name`, `agent_sequence`, `fork_id`, `traceparent`, baggage);
(2) `upsert_topology_edge` + `emit_topology_event` (`a2a.message.sent` /
`received`); (3) server side `opentelemetry.context.attach` / `detach` around
the handler. Own spans (`a2a.client.send_message`, …) are extra and empty of
protocol ids.

| Wrapped method | Adds beyond typed hook / SDK span | Minimum typed seam | Verdict |
| --- | --- | --- | --- |
| `BaseA2AClient.send_message` | Observe metadata on the wire; topology `a2a.message.sent`; extra span | `ClientCallInterceptor.before` + native `transport.message.sent` | **replace** |
| `BaseA2AClient.send_message_streaming` | same for streaming | interceptor `method='send_message_streaming'` | **replace** |
| legacy `A2AClient.send_message` | same | unused: MAS Lab uses `create_client` / `BaseClient` | **drop** |
| legacy `A2AClient.broadcast_message` | same | unused | **drop** |
| `DefaultRequestHandler.on_message_send` | Restore observe metadata; `attach`/`detach`; topology `a2a.message.received` | `_A2ARequestHandler.on_message_send` (already a subclass). Do **not** attach/detach. | **replace** |
| `DefaultRequestHandler.on_message_send_stream` | same for stream | `_A2ARequestHandler.on_message_send_stream` | **replace** |
| `SRPCTransport.send_message` / `send_message_streaming` (`slima2a`) | same as client send | **not in this tree** (`slima2a` is not a dependency here). A2A-over-SLIM is another branch. | **drop** here; revisit if that stack lands |

### Thread-bridge result

With the A2A SDK flag **on**, the client's `asyncio.run` worker-thread bridge,
and the executor's `asyncio.to_thread` handler, **`opentelemetry.context`
logged nothing**. Call succeeded.

The historical `Token was created in a different Context` error is
`attach`/`detach` of a token on another thread or task. The SDK's own
`start_as_current_span` enters and exits on the **same** async task, so it
does not trip that logger. observe-sdk's server wrapper **does**
`otel_ctx.attach` / `detach` around `on_message_send` — that is the shape
that can bring the error back. Intake and hooks must not do that (rule R4).

Enabling the A2A flag is therefore **safe for the SDK's own spans** in 1.1.5
on this path, provided nothing else attaches context tokens across tasks.

### Recommended input source (proposal)

**hooks only** for `transport.message.*` and `transport.discovery` (agent
card). SDK spans add timings of dispatcher and queue internals and **no**
ids; treat them as `transport.internal` when `sdk_spans` and `internals` are
on. Default `sdk_spans: false` still makes sense: 29 empty-attribute spans
per call, flag-at-import cost, and hooks already see the message.

## MCP (mcp 2.2.0)

Instrumentation scope: `mcp-python-sdk` (empty version). **No env flag.**
`mcp.shared._otel` imports OpenTelemetry unconditionally.
`mcp.server.lowlevel.server.Server.middleware` ships
`OpenTelemetryMiddleware` by default. Client outbound spans are created in
`mcp.shared.jsonrpc_dispatcher.JSONRPCDispatcher` (`MCP send {method} …`)
with `inject_trace_context` into `_meta` (W3C traceparent). Comment in that
file: move this to outbound middleware once that seam exists; **there is no
client middleware type today**.

### Scenarios

| Scenario | Fixture | SDK spans | Names + attributes |
| --- | --- | --- | --- |
| `initialize` | `mcp/2.2.0/initialize.json` | 2 | SERVER `initialize` (`mcp.method.name`, `mcp.protocol.version`, `jsonrpc.request.id`); CLIENT `MCP send initialize` (`mcp.method.name`, `jsonrpc.request.id`) |
| `tools/list` | `mcp/2.2.0/tools_list.json` | 3 | SERVER `tools/list` + leftover `notifications/initialized`; CLIENT `MCP send tools/list` |
| `tools/call` success | `mcp/2.2.0/tools_call_success.json` | 2 | SERVER `tools/call echo` (`gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, ids); CLIENT `MCP send tools/call echo` |
| `tools/call` failure | `mcp/2.2.0/tools_call_failure.json` | 2 | SERVER `tools/call boom` status ERROR, `error.type=tool_error`; CLIENT `MCP send tools/call boom` |

Unlike A2A, these spans **name the method and the tool** and carry the
JSON-RPC request id. They are usable for `transport.message.*` /
`transport.discovery` **in addition to** hooks, not only as internals.

Server spans use `start_as_current_span` with `extract_trace_context(ctx.meta)`
as parent. Client injects into `_meta` on every outbound request.

### Typed hooks (verified in installed 2.2.0)

| Seam | Class / method | What it sees |
| --- | --- | --- |
| Server middleware | `mcp.server.context.ServerMiddleware.__call__(ctx, call_next)` | Every inbound request/notification (`ctx.method`, `ctx.params`, `ctx.request_id`, `ctx.meta`) **including** `initialize` and `tools/list` / `tools/call`. `OpenTelemetryMiddleware` is this interface. Append to `Server.middleware`. |
| Client message handler | `mcp.client.session.ClientSession(message_handler=)` / `mcp.client.client.Client(..., message_handler=)` | Server **notifications** and transport exceptions. **Not** outbound `tools/call`. |
| Client session methods | `ClientSession.list_tools` / `call_tool` / `initialize` | Typed results. MAS Lab: `library_ioa.plugins.mcp.client.MCPClient.list_tools` / `call_tool` / `connect`. |
| Trace helpers | `mcp.shared._otel.inject_trace_context` / `extract_trace_context` | W3C in `_meta`. Already used by the dispatcher. |
| In-process transport | `mcp.client._memory.InMemoryTransport` / `mcp.Client(server)` | Tests only. |

No `ClientMiddleware`. Outbound observation in-process is: our
`MCPClient` methods, or the SDK's own `MCP send …` spans.

### observe-sdk wrappers (`ioa_observe.sdk.instrumentations.mcp`)

| Wrapped method | Adds beyond typed hook / SDK span | Minimum typed seam | Verdict |
| --- | --- | --- | --- |
| `mcp.shared.session.BaseSession.send_request` | Observe metadata in `params.meta`; topology `mcp.message.sent`; extra `{method}.mcp` span with payload dump | `MCPClient.list_tools` / `call_tool` (native events). SDK already emits `MCP send …`. Payload dump is not needed (redaction). | **replace** |
| `stdio_client` / `stdio_server` / `sse_client` / `SseServerTransport.connect_sse` / `streamablehttp_client` / `StreamableHTTPServerTransport.connect` | Wrap streams; inject/extract context on the wire; receive topology | Server: `ServerMiddleware`. Client: SDK `_meta` inject already. Do not wrap streams. | **replace** |
| `ServerSession.__init__` | `ContextAttachingStreamReader` / `ContextSavingStreamWriter` using `context.attach` / `detach` | **Violates R4.** Server middleware sees the request without this. | **drop** |

`mcp.message.*` is informational in norm: native `transport.message.*` for
tools/call may exist, but compatibility aliases for `mcp.message.*` must not
become KG-bearing. `tools/list` aliases to `mcp.tools.discovered`.

### Recommended input source (proposal)

**hooks + spans.** Hooks (`MCPClient`, `ServerMiddleware`) emit
`transport.message.*` / `transport.discovery` with peers and session.
SDK spans carry method, tool name, request id, error type — and they fire
whenever a TracerProvider exists, with no disable flag, so intake should
classify them rather than let them leak to an exporter.

## SLIM (slim-bindings 2.2.0)

No OpenTelemetry in the installed wheel (grep of `slim_bindings` finds only
`traceback`). observe-sdk invents its own spans by monkeypatching.

### Scenarios

| Scenario | Fixture | SDK spans | What the typed path did |
| --- | --- | --- | --- |
| Connect / subscribe | `slim/2.2.0/connect.json` | **0** | `StackHandle.start` + two `SlimAgentComm` links |
| Send / receive | `slim/2.2.0/send_receive.json` | **0** | `asend("b", "hello")` → `ack:hello` |
| Channel setup + publish | `slim/2.2.0/channel_session.json` | **0** | `create_channel` + `publish`; subscriber saw `ping` |

### Typed hooks (verified in this tree against bindings 2.2.0)

| Seam | Class / method | What it sees |
| --- | --- | --- |
| Outbound request | `library_ioa.plugins.slim.agent_comm.SlimAgentComm.asend` / `_asend_impl` | Target, task, headers, `correlation_id`, `caller_call_id`, `context_id`. Calls `Session.publish_async` then `get_message_async`. |
| Inbound | `library_ioa.plugins.slim.inbound.SlimInbound._on_inbound` / `_dispatch_inbound` | Decoded envelope, sender, headers, kind (`request` / message). |
| Session / channel | `SlimAgentComm._p2p_session`, `create_channel` / `_acreate_channel`, `publish` | `App.create_session_async`, `set_route_async`, `invite_async`. |
| Listen | `SlimInbound` listen loop → `App.listen_for_session_async` | New inbound session. |
| Service | `library_ioa.plugins.slim.service.SlimService.start` / `close` | Shared node connection (`ConnectionActor`). |
| Bindings (not a callback) | `Session.publish_async` / `publish_to_async` / `get_message_async` | Used from the plugin; do not monkeypatch. |

There is no SLIM interceptor type. The plugin methods **are** the seam.

### observe-sdk wrappers (`ioa_observe.sdk.instrumentations.slim`)

Rewrites the payload JSON to inject `headers` (`session_id`, `traceparent`,
agent linking, `source_agent` / `target_agent`) and strips them on receive.
That **changes the wire message**.

| Wrapped method | Adds beyond typed hook | Minimum typed seam | Verdict |
| --- | --- | --- | --- |
| `Service.connect_async` / `run_server_async` | span `slim.service.connect` / `run_server` | `SlimService.start` → `transport.session` | **replace** |
| `App.create_session_async` / `create_session_and_wait_async` / `subscribe_async` / `set_route_async` / `listen_for_session[_async]` | session-id attributes | `_p2p_session` / `_acreate_channel` / listen loop | **replace** |
| `Session.publish_async` / `publish_to_async` / `publish_and_wait_async` / `publish_to_and_wait_async` | header wrap + topology `slim.message.sent` | `SlimAgentComm.asend` / `publish` with `propagate_metadata` | **replace** |
| `Session.get_message_async` | header unwrap + `attach` (no matching detach!) + topology `slim.message.received` | `SlimInbound._dispatch_inbound` | **replace**; do not copy `attach` |

### Recommended input source (proposal)

**hooks only.** No classifier unless a later slim-bindings release ships
OTel. Registry must allow a protocol with hooks and no classifier.

## Cross-protocol notes

- **Agent ids** appear in observe-sdk topology as `source` / `target` /
  `last_agent_name`. Native events should carry them as **keys** (session /
  agent fields), never as a vocabulary of names.
- **Metadata on the wire** (`observe` on A2A, `_meta` on MCP, JSON `headers`
  on SLIM) is the only way observe-sdk got cross-process session and sender.
  That is `propagate_metadata`, **off by default**: it puts the session id and
  the sender's name in every message to every peer, third parties included,
  and a strict peer may reject an unknown key. Turn it on per protocol for
  peers you control. Only A2A (`message.metadata`) implements it today; MCP
  `_meta` and SLIM headers do not. SLIM header wrapping **mutates the payload**; turning it
  off must leave `SlimAgentComm` envelopes working (they already have their
  own `headers` dict).
- **Monkeypatching is not required** for any of the three: every observe-sdk
  wrapper has a typed seam in the plugin or the SDK. Out of scope in the
  design: wrapping SDK methods.

## Decisions

Locked for this branch.

| Question | Recommendation | Decision |
| --- | --- | --- |
| Can the A2A SDK flag be enabled without the context error? | Yes, on 1.1.5, if we never `attach`/`detach` tokens. Workaround: none needed for the SDK itself; ban attach/detach (R4). | **yes**, with R4 |
| A2A `sdk_spans` default | **false**. Spans have no attributes; 29 internals per call. Hooks carry the message. | **false** |
| A2A input source | **hooks only** for message/discovery; optional SDK spans → `transport.internal` | **hooks only** (SDK spans optional internals) |
| MCP input source | **hooks + spans** | **hooks + spans** |
| SLIM input source | **hooks only** (no classifier) | **hooks only** (empty-scope classifier) |
| observe-sdk wrappers | **replace** all that this tree uses; **drop** legacy A2A, slima2a, MCP `ServerSession.__init__` attach/detach | **replace / drop** |
| `propagate_metadata` default | **false** everywhere. It changes what peers receive and names the session and sender to every peer. | **false**; opt in per protocol (A2A only today) |
| Default operational mode | `contract`, `hooks` on, `internals` off, `sdk_spans` off: one native event per crossing; SDK copies dropped. Discovery (agent card, tools) rides `hooks` because SCOPE's norm maps it. | **locked** |
| KG default for SCOPE facts | Fold `transport.message.*` and `transport.discovery` with no category gate, as SCOPE's norm does. | **on** |

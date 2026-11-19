<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Transport observability

How MAS Lab records what crosses a protocol boundary: A2A, MCP, SLIM, and
any protocol plugin added later. Status: **implemented**. Native
`transport.*` events are the source of truth; OTel is an optional
projection.

## Why

MAS Lab observes the boundary with typed contracts: a send, a tool call, a
model call. Each protocol library also ships its own telemetry. The A2A SDK
emits OpenTelemetry (OTel) spans straight to the global OTel provider, and
`ioa-observe-sdk` wraps A2A, MCP and SLIM methods to emit topology events.

Observability backends use this protocol-level data in realtime: edges between
agents, agent cards, tool discovery. They do not wait for an agent span to end.
MAS Lab must keep providing it, without double counting, without depending on
OTel being present, and without breaking when a protocol SDK changes.

## Principles

1. **Native first.** Every transport fact is a native event. OTel is an
   optional projection of native events, never a second source of truth.
2. **One outbound path.** A protocol SDK's spans never reach an exporter
   directly. They enter MAS Lab, become native events, and leave from the
   native-to-OTel projector.
3. **Typed hooks for meaning, spans for internals.** Message content, peers
   and task state come from the protocol plugin's typed hooks. SDK spans only
   add what no hook exposes (internal steps, timings).
4. **Generic core, protocol-specific plugins.** The core knows no protocol
   vocabulary. All span names, attributes and mappings live in the protocol
   plugin.
5. **Never lose, never duplicate.** Unknown input degrades to a generic event.
   Known duplicates attach to an existing event.
6. **Fail loud, degrade soft.** SDK drift produces a warning, not silence and
   not a crash.

## Architecture

```text
protocol SDK spans (OTel API) ─► intake ─► classifier (protocol plugin) ─┐
protocol plugin typed hooks ─────────────────────────────────────────────┴─► native event
                                                                           │
                       ┌───────────────────────────────────────────────────┤
                       ▼                                                   ▼
                  native sink                          projector ─► OTel spans / observe-sdk events
                (events.jsonl, stream)                 (optional; per-transport mode)
                       │
                       ▼
                 native-to-KG handlers
```

| Part | Where it lives | Responsibility |
| --- | --- | --- |
| Intake | core (generic) | Attach a processor to the OTel API without exporting. Route each span by instrumentation scope to the registered classifier. |
| Classifier | protocol plugin (for example `library-ioa` `a2a`, `mcp`, `slim`) | Map the protocol's span names and attributes to a decision: `drop`, `emit`, `passthrough`. |
| Typed hooks | protocol plugin | Emit native events from typed objects (message, task, agent card, tool list). |
| Native event | runtime schema | Self-contained record with a correlation key. |
| Projector | core (generic) plus per-protocol alias table in the plugin | Native events to OTel spans, with the selected mode. |
| KG handlers | `library-kg` | Native events to ontology nodes and edges. |

The core provides primitives only: provider attachment, scope routing,
correlation, ordering buffer, redaction, version check, warn-once. It never
mentions `a2a`, `mcp` or `slim`.

## Native event shape

Transport events are a family: `transport.message.sent`,
`.received`, `.completed`, `transport.discovery` (agent card, tool list),
`transport.session` (link or channel setup), `transport.internal` (anything
from the protocol that is not one of the above), `transport.error`.

Every event carries:

- `protocol` (`a2a`, `mcp`, `slim`, ...) and `side` (`client` or `server`);
- a **correlation key** built from ids the protocol already gives
  (`context_id`, `task_id`, `message_id`, hop or session id);
- the MAS ids it already has (session, agent, call) when known;
- a source tag: `hook` or `sdk-span`.

### Properties the shape must keep

The KG path is stateless, realtime, order-independent and constant cost per
event. The native event shape has to preserve that, as SCOPE's normalizer does
for OTel.

- **Self-contained.** An event can be mapped to nodes and edges from its own
  fields. Node and edge ids derive from the event's own ids, so an edge may
  name a node that has not arrived yet.
- **Idempotent.** The same event delivered twice produces the same graph.
- **Order-independent.** Sent before received, completed before started: the
  resulting graph is identical.
- **Constant cost.** Mapping one event costs the same however long the run is.
  No lookup of earlier events at mapping time. Intake keeps no per-span state.
- **Events are never edited.** Late information is a new event with the same
  correlation key. A consumer merges by key. An exported event is never
  changed.
- **Realtime.** A `sent` event is emitted when the send starts. It does not wait
  for the span to end.

## Protocol plugin contract

A protocol plugin supplies:

1. **Typed hooks** that emit native events.
2. **A classifier** for its SDK's OTel scope:
   `classify(span_start | span_end) -> drop | emit(event) | passthrough`.
   It reads span names and attributes only.
3. **Compatibility aliases**: the table that maps native fields and events to
   the names existing observability consumers read (see KG mapping).
4. **Supported SDK range**: the versions its mapping was tested against.

A span is announced once, when it ends and its attributes are complete. Typed
hooks give the realtime view: a `sent` event is emitted when the send starts,
not when a span ends. A span whose work a hook already reports is dropped, so
a call is never counted twice.

## Projection to OTel

The otel sink turns native events into the observe-sdk compatibility events
SCOPE's norm reads (`a2a.message.sent`, `a2a.agent_card`, ...). Each alias is
built from one native event alone and written as an OTel log record. Listing
`otel` is enough; kernel `otel.realtime` is a separate switch for
`topology.node.*` / `tool.*` / `llm.*` signals.

| `otel.transport.projection` | OTel output |
| --- | --- |
| `none` | no alias events; native only |
| `contract` (default when `otel` is listed) | one alias event per native event the plugin maps |

Native output does not depend on this setting. Default native holds the
facts SCOPE's norm maps (message, agent card, tools discovered). Protocol
session and SDK internals are opt-in (`internals: true`).

Not built yet: exporting SDK spans as OTel spans, nested under the contract
span (`nested`) or beside it (`flat`). Both are rejected by the config until
the span export exists, so no setting promises more than the code does. When
they are added, the rules are: parent ids are rewritten in one pass and a span
is never suppressed without re-parenting its children; on the client the
contract span is the parent, on the server the SDK span starts first so the
contract span is linked or a child; sampling is decided once, at the
projector; and there is one exporter with one set of resource attributes.

## KG mapping

SCOPE's normalizer (`norm`, package `ioc-scope-norm`) already reads
protocol-level events in the `ioa-observe-sdk` shape and handles them without
looking at other events: `a2a.message.sent` / `.received` / `.completed`,
`slim.message.sent` / `.received`, `a2a.agent_card`, `mcp.tools.discovered`,
`topology.edge.updated`. It maps `mcp.message.*` to nothing, on purpose.
Each record is mapped alone, so arrival order does not matter.

Two paths reach the KG:

1. **OTel path.** The otel sink emits those event names as compatibility
   aliases, with mapping values as JSON text (norm reads `capability.content`
   as text), so existing KG ingestion keeps working unchanged.
2. **Native path.** `library-kg` native handlers fold the `transport.*` family
   with the same properties as norm's handlers: own-fields-only, derived ids,
   idempotent. Field-by-field differences from norm, and the facts with no
   ontology class, are in
   [transport-observability-kg-mapping.md](transport-observability-kg-mapping.md).

## Robustness to protocol changes

- **Pinned range.** Each classifier states the SDK distribution and versions it
  was tested on. Outside the range, intake logs one warning per process when it
  is installed.
- **Expected-name check.** A process is a client or a server on one transport,
  so some mapped names never appear in a healthy run. If the SDK produced spans
  and none of the classifier's mapped names was among them, log one warning
  per process at shutdown. Silence must not look healthy.
- **Degrade, don't drop.** An unmapped span becomes `transport.internal` with
  its name and allowlisted attributes. A renamed span loses detail, not
  events.
- **Classifier errors are isolated.** An exception in a classifier is logged
  with the span name and counted. The span falls through to `passthrough`. It
  never reaches the caller of the protocol.
- **Startup report.** Intake reports whether it is active, on which provider,
  and which classifiers are loaded.
- **Provider ownership.** MAS Lab never replaces an existing provider. It
  attaches its processor to it. With no provider it creates one without an
  exporter. If neither is possible it fails loudly.
- **Sampling.** Intake sees only spans the process's own sampler records. If
  the host app samples SDK spans out, those spans never reach intake. Typed
  hooks are not sampled, so message and discovery facts are unaffected.
- **Drift test.** A test per protocol replays recorded SDK spans from the
  supported versions and fails if mapping coverage falls.

## Cost and safety

An earlier fix had to switch the A2A SDK's own telemetry off: spans that
finish on another thread or task made OpenTelemetry log one full traceback per
call, and eager imports added start-up work. This design must not bring that
back.

- **Off means zero.** With transport observability off, nothing imports
  OpenTelemetry or a protocol SDK on its behalf, installs a processor, or
  creates a provider.
- **Opt-in and lazy.** Intake and projector load only when configured. Plugin
  listing never imports them.
- **The A2A SDK reads its telemetry flag once, at import.** SDK spans as an
  input can therefore only be chosen at process start, before the SDK is
  imported. If the SDK was imported first, intake says so once and runs on
  typed hooks only.
- **No context tokens across tasks.** Intake and hooks create spans with an
  explicit parent and end them. They never attach or detach OTel context
  around awaits or thread hops.
- **One log line per kind of problem,** then a counter. Never a traceback per
  call.
- **Bounded.** Copied attribute sizes and tool lists have limits, and intake
  keeps no per-span state.
- **Constant overhead per call,** checked by a benchmark that reports
  per-call cost and peak memory.

## Content and privacy
A peer's `session_id` and sender name arrive in message metadata (when
`propagate_metadata` is on) and cannot be checked. The receiving side keeps the
session as `peer_session_id`, a claim, and never as its own `session_id`. Text
from a peer is size-capped and type-checked before it reaches an event.


Intake feeds native through the same redaction plug point as every other
event. Agent cards, headers and message parts are copied by allowlisted field,
never wholesale.

## Invariants (one test each, each checked by breaking the code on purpose)

1. One call produces one `message.sent` event, hook or SDK span alone or both.
2. A converted SDK span never creates a `message.*` event.
3. The alias projection reads one event at a time: a stream projects as its
   parts do.
4. A process with no transport config emits nothing.
5. Shuffling the arrival order of a recorded stream gives the same graph.
6. Delivering any event twice gives the same graph.
7. Mapping cost per event does not grow with run length.
8. With the SDK renamed, events degrade to `transport.internal` and one warning
   is logged.
9. The core imports no protocol plugin and contains no protocol names.
10. Intake never raises into the protocol's caller, whatever a span or a
    classifier does.

## Configuration

Modes come in as explicit configuration to intake and projector. Only the CLI
layer reads the environment.

```yaml
observability:
  - native:
      transport:
        defaults:
          enabled: true
          hooks: true
          internals: false
          sdk_spans: false
          propagate_metadata: false
        slim: { enabled: false }
  - otel:
      transport:
        projection: contract
        aliases: auto
```

Listing `native` turns capture on. Listing `otel` turns projection and
aliases on so SCOPE sees the same A2A, MCP, and SLIM records as other
apps. `transport` is not a plugin. No native and no otel means off: no
hooks, no intake, no events.

CLI: `mas-ctl chat agent.yaml --transport-observability a2a=off` (repeatable;
`a2a.sdk_spans=true` is the same key).

Capture (`native.transport`):

| Attribute | Default | What it controls |
| --- | --- | --- |
| `enabled` | `true` | Hooks, intake, and events for the protocol. Off leaves the contract boundary events (send, tool call, model call) in place. |
| `hooks` | `true` | Message, discovery and error events from the protocol plugin's typed hooks (the facts SCOPE's norm maps). |
| `internals` | `false` | Session setup and SDK-span internals. Off so one crossing is one event. |
| `sdk_spans` | `false` | Use the SDK's own spans as an input. Chosen at process start. |
| `propagate_metadata` | `false` | Session id and sender name in the protocol's message metadata, so the receiving side can name the sender. It changes what every peer receives, so it is off unless you turn it on for peers you control. Only A2A implements it. |

Projection (`otel.transport`):

| Attribute | Default | What it controls |
| --- | --- | --- |
| `projection` | `contract` | `none` or `contract`. Native-only runs omit `otel` instead of setting `none`. |
| `aliases` | `auto` | observe-sdk names SCOPE's norm reads. `auto` is on for `contract`, off for `none`. Write `"off"` in quotes in YAML. |

Precedence: overlay or CLI flag, then the manifest entry for the protocol,
then `defaults`, then the built-in default.

## Out of scope

- Wrapping or patching protocol SDK methods to capture events. Typed hooks and
  the OTel API are the only seams.
- Parsing message text for telemetry.
- A second event bus.

## Decisions

Recorded in [transport-observability-inventory.md](transport-observability-inventory.md)
and [transport-observability-kg-mapping.md](transport-observability-kg-mapping.md).

- Event family is `transport.*` as above. `transport.session` has no ontology
  class; native KG drops it.
- Compatibility aliases: `auto` is on for `contract`
  (one SCOPE name per native event). `mcp.message.*` is not aliased
  (SCOPE marks it informational).
- A2A client interceptors and the server request handler replace observe-sdk
  monkeypatches. SDK spans default off (`sdk_spans: false`).

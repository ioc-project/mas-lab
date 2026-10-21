<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# ADR 0002: Observability event model, an announced stream and not a query interface

**Status:** Accepted · Implemented (see [Implementation](#implementation))
**Date:** 2026-07-10

An ADR (architecture decision record) states one decision, its context, and its consequences. *Observability* here means recording what a run did so that you can inspect it later.

## Context

Every call the runtime makes for an agent crosses a boundary in the same shape: a governance check before the call, the call itself, and a governance check after. *Governance* is the policy layer that approves or blocks a call. The call can be a tool invocation, a model call, or a memory read or write.

Each crossing is worth recording, with more than the fact that it happened. A downstream consumer will eventually need to know:

- what made governance decide as it did,
- what a human answered when asked,
- what the runtime did when a call failed and was retried or abandoned.

Two designs can make that information available:

- A **query store** answers questions on request. A later reader asks "what did governance decide for this call" and gets an answer.
- An **announced stream** writes a self-describing record the moment a crossing occurs. Interested readers subscribe to the stream or read it later.

## Decision

The runtime records every boundary crossing as a self-contained record. It writes the record once, when the crossing happens. Nobody asks for it again.

Every record carries one linking value, a *correlation id*. All records produced by the same call share it. To get the whole story of one call (governance going in, the call, governance coming out), a reader collects every record with that id, in write order.

Nothing needs to be requested in advance. Nothing needs a live system to answer.

## Why a stream and not a query interface

**A run outlives its process.** People inspect a benchmark result in a code review weeks later. They reconstruct an incident months later. They compare a lab result with a previous one that has no running process. A written record survives all of these. A live store survives only if someone turns it into a written record. At that point it was a stream all along.

**Several readers want to know what happened, and they do not coordinate.** A file for later inspection, an external monitor, and a plot built from run history may all read one call's outcome. They run at different times and do not know each other. A stream lets each read independently, at its own pace. A query interface would force all of them to exist and coordinate while the call is still running.

**Self-contained does not mean disconnected.** Each record is small and describes one crossing, not the whole call. The correlation id ties it to everything else the call produced.

The runtime already works this way elsewhere. The context handed to a model call is its own small record, linked to the call it feeds. Governance now follows the same rule. A decision at the boundary is its own record (what was decided and why), linked by correlation id to the call it gates. It is not bundled into the call record, and it is not withheld until asked for.

## What each record carries

A record carries the minimum it needs to stand alone: the kind of crossing, when it happened, and which call it belongs to.

The runtime does not try to anticipate every future question. It follows one rule: record what would be lost the instant the moment passes, and leave the rest to the reader.

- A governance outcome carries the decision and a short human-readable reason. Without the reason, the explanation is lost when the call moves on.
- Anything a reader can compute from the stream stays out of the record. Examples: a chain of retries, or a call that a human approved.

An earlier version of boundary recording dropped several kinds of crossings, because no downstream reader existed yet:

- a human's actual answer to an approval request,
- a call that failed past its retry budget,
- the semantic outcome of a governance check, as distinct from its timing.

Those crossings happened but were never written down. They were unrecoverable. The runtime now writes them:

- `hitl.resolve` carries the resolution and the answer. HITL means human in the loop.
- `governance.decision` carries the decision and reason, plus any failure class and retry count.
- `boundary.error` records a call abandoned at the boundary.

## Consequences: reading the stream as a graph

A stream of small, correlation-linked records already has the shape of a graph. Each record is a node. Correlation ids, with the parent and child structure of nested calls, are the edges.

A consumer that wants a graph view needs no change on the recording side. It needs a translation step that turns the flat stream into nodes and relationships, placed in front of whatever reads the stream today.

## Implementation

As of v0.2.0 the model is implemented as follows.

- **Record type.** `ObservabilityEvent` (`mas.runtime.schema.observability`) is a frozen record with `seq`, `kind`, `phase`, `machine_id`, `correlation_id`, `policy_name`, `actor_id`, `attribution_code`, and a free-form `payload`. `ObsEventKind` enumerates the crossings: `boundary.egress`, `boundary.ingress`, `governance.decision`, `hitl.request`, `hitl.resolve`, `engine.io`, `engine.io.return`, `client.response`, `boundary.error`, `context.steer`, `context.mutation`, `context.assembled`, `cache.lookup`, `envelope.activity`, and `control`.
- **Recorder.** `ObservabilityOperator` (`mas.runtime.boundary.obs.operator`) writes one record per crossing as the kernel driver processes it. The model-call context is its own `context.assembled` record, linked by correlation id to the call it feeds.
- **Projection.** `boundary_event_to_transition()` (`mas.runtime.boundary.obs.transition`) projects each record to a `TransitionEvent`. It adds `agent_id`, `session_id`, `task_id`, `call_id`, `parent_call_id`, and `upstream_correlation_id`. These are the parent and child edges described above.
- **Live stream.** `EventStream` (`mas.runtime.boundary.obs.event_stream`) is an in-process publish/subscribe bus. The native observability plugin (`library-standard`, `plugins/observability/native_plugin.py`) publishes projected records onto it. Nothing subscribes unless a host or pipeline step does.
- **Written stream.** JSONL emitters write the same records to a file or stdout for later reading: `JsonlFileEmitter`, `StdoutJsonlEmitter`, `FanOutEmitter`, and `NullEmitter` in `mas.library.standard.lib.observability.emit`.
- **Cross-process tail.** `ExchangeHub` (`mas.runtime.boundary.obs.exchange_hub`) fans out driver exchange records to control-protocol subscribers. The host injects it. It is not a process-wide singleton.

## See also

- [Contracts](contracts.md): where this stream fits among the runtime's other boundaries.
- [ADR 0001](adr-0001-lab-terminology.md): repository terminology, a separate decision in the same format.
- [Observability](../cli/observability.md)
- [Tutorial 8: telemetry](../tutorials/08-telemetry/README.md)

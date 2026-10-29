<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Communication headers

`AgentCommContract.send` and `asend` take an optional `headers` map (string to
string). A local link carries that map in its envelope and restores it around
the receiver's handler. Inside one process the restored map is the ambient
headers for that turn. Names are case-insensitive and stored in lowercase.

These values are **caller-supplied and unauthenticated**. Do not use them for
identity or authorization. A later design will authenticate the sender on the
link. A header is the wrong carrier unless a trusted layer sets it.

`SendContext` is the per-send envelope that can carry this map plus an
intent. The caller may set those; only the link sets `sender`. See
[Communication stack](comm-stack.md).

The in-process `local` link carries the map. **A2A does not:**
`A2AAgentComm.send` accepts `headers` so the contract is implemented, and then
drops them. Forwarding hop headers over HTTP is a later decision.

## What rides the next hop

Only these keys copy onto a later `send` if the caller does not pass `headers`
again:

- `traceparent`
- `tracestate`
- `baggage`

Everything else stays on this hop, including `authorization` and any `mas.*`
key. A peer cannot plant a header that silently rides every later send. To
forward a hop-local key, pass it again in `headers`.

Names must be HTTP tokens (no spaces, slashes, or newlines). Values must not
contain control characters. Caps: 32 headers, 128-byte names, 4096-byte
values, 16384 bytes total. `tracestate` is at most 32 members (W3C);
`baggage` is at most 64. A longer list is dropped so the send still goes
through.

## `trace_context`

`TraceContextLayer` wraps a lower link. On each send it writes a W3C
`traceparent`: the same trace id as a valid inbound parent, and a new span id.
An invalid parent (version `ff`, all-zero trace or span id) is treated as no
parent and starts a root. This span id is a **correlation id for the send**,
not an OpenTelemetry span: with tracing on, receivers' spans will not parent
to it. Wiring it to the active span is later work.

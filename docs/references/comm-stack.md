<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Communication stack

A stack is an `agent_comm` plugin that wraps a link with decorator layers.
The link sits at the bottom. Each layer above it binds the one below. Calls
enter at the top.

`BoundLayer` is the shared bind/forward helper. `TraceContextLayer` is a
`BoundLayer`. Starting the stack starts every layer, bottom first, and
stopping it stops them top first. Each layer's `start` / `stop` is called
once. A failed start can be retried. `BoundLayer` does not forward `start`
or `stop`; a decorator used alone does not start the link.

## Capabilities

A link plugin class declares flags once (`CAPABILITIES`). A plugin that
declares nothing is **unknown**. Unknown cannot claim `isolation`, `e2e`,
`reliable`, or `persistent_groups`. The stack OR-merges flags: a decorator
cannot turn a flag off. See [Communication headers](comm-headers.md) for
the map that rides a send.

## Send context

`SendContext` is the per-send envelope. It is immutable. The caller may set
headers and intent. Only the link sets `sender`, from its `agent_id`. A
caller-supplied sender is ignored. When a layer passes `headers`, that map
replaces `context.headers` (a redaction layer can drop a key). The maps
merge only when `headers` is omitted. A layer that adds keys copies the
map first so `headers` and `context.headers` stay in sync.

## Shared checks

`mas.library.standard.testing.agent_comm_checks.check_agent_comm` takes
real endpoints. Pass `on_request` to install a handler. The expected reply
is `ack:` plus the ping string, unless you pass another prefix.

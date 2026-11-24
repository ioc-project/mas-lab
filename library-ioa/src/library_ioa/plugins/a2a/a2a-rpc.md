<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# a2a-rpc (stack layer) — A2A over a SLIM node

| Field | Value |
|-------|--------|
| **ID** | `a2a-rpc` |
| **Kind** | `layer` (a protocol layer: above a `slim` row, never at the bottom) |
| **URN** | `mas.layer.a2a-rpc` |
| **Implementation** | `A2ALayer` in `library_ioa.plugins.a2a.layer` |
| **Manifest keys** | an infra row `{protocol: a2a-rpc}` that a Flavour binds above a `slim` row: `bind.agent_comm: [desk-node, desk-a2a]` |
| **Extra** | `slim` (`slim-bindings`, `slima2a`) |

The layer is the A2A SDK on the `slimrpc` protocol binding, riding the node connection the `slim` row
below it holds. Each send is one A2A 1.0 `SendMessage` made by the SDK client, and each served
agent answers through the SDK request handler, the same one the `a2a` HTTP exposure uses. Other
A2A-over-slimrpc peers can be reached and can reach this one: the wire is A2A, not a MAS Lab format.

- The task is one text part. `context_id` is the message `contextId`.
  `correlation_id` and `caller_call_id` ride the message metadata as
  `mas.correlation_id` and `mas.caller_call_id`. Hop-safe headers
  (`traceparent`, `tracestate`, `baggage`) ride the same metadata and
  become the ambient headers of the served turn.
- Only the `task` intent is honoured. `inform`, `ask` and the others raise
  `AgentCommError` before anything leaves.
- The reply is the response message's text, or the artifact / status-message text
  of a completed stream. A task that is `failed`, `rejected` or `canceled` raises
  `AgentCommError`.

Inbound, the received message is unwrapped before the agent's turn runs: the turn gets the text,
the call ids and the context id (a new one when the request has none). The answer goes back as an
agent message in that context. A turn that raises is a `failed` task with the text
`Agent execution failed`; the error stays in the serving process's log.

When the stack serves a turn, the layer claims the link's inbound sessions: the RPC server owns
them, so the `slim` link runs no reader of its own on that app. If that server stops, the slim link
reconnects and this layer starts a new one. A reconnect also replaces a server that is still
running on the old connection. A connect-only agent (no turn)
never calls `serve`: this layer starts no RPC server and the link keeps its reader. It runs over
`slim` only. To reach an A2A server at an HTTP url, use the `a2a` link ([a2a.md](a2a.md)): the same
SDK client on the HTTP binding, which is a transport and so only the bottom of a chain. Name,
description, version, `skills`, or a `card` / `manifest` map belong on an `a2a-rpc` row. A
`protocol: a2a+slim` row puts those keys on `a2a-rpc` and the node fields on `slim`.

```yaml
# infra rows
desk-node: {protocol: slim, url: "http://127.0.0.1:46357", identity: acme/lab/desk, secret: {secret: slim}}
desk-a2a: {protocol: a2a-rpc}
# Flavour
bind:
  agent_comm: [desk-node, desk-a2a]
```

`slima2a` pins the `a2a-sdk` release it was built against, so the `slim` extra holds the SDK at that
release.

**Sends are not retried.** A `SendMessage` that reached the peer may already have run its task, so a failed
send raises and is never sent a second time. A dead node is noticed by the slim link (its reader, or the RPC
server stopping) and the next send waits for the new connection.

**Caller and admission.** A served turn's sender is the caller the node verified for the session
(`SlimUser.subject`), or empty when the session carries none; message metadata is never used for it. Unlike
the `a2a` HTTP exposure, the slimrpc server has no `admission` table: any app the node lets reach this agent's
name can call it. Restrict who may call at the node, or with the identity gate on the verified sender.

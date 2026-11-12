<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# a2a-framing (stack layer) — A2A framing above a transport

| Field | Value |
|-------|--------|
| **ID** | `a2a-framing` |
| **Kind** | `layer` (a protocol layer: above a transport, never at the bottom) |
| **URN** | `mas.stack_layer.a2a-framing` |
| **Implementation** | `A2ALayer` in `library_ioa.plugins.a2a.layer` |
| **Manifest keys** | an infra row `{protocol: a2a-framing}` that a Flavour binds above a transport row: `bind.agent_comm: [desk-node, desk-a2a]` |

Each send becomes one A2A 1.0 `SendMessage` JSON-RPC request, the one
the A2A SDK client sends and the `a2a` exposure accepts. This is MAS Lab's
own JSON-RPC encoding of that request, not the A2A SDK client. The transport
below carries the text; the JSON-RPC response comes back as the reply.

- The task is one text part. `context_id` is the message `contextId`.
  `correlation_id` and `caller_call_id` ride the message metadata as
  `mas.correlation_id` and `mas.caller_call_id`. The header
  `a2a-version: 1.0` is added.
- Only the `task` intent is honoured. `inform`, `ask` and the others raise
  `AgentCommError` before anything leaves.
- The reply is the response message's text. A JSON-RPC error, or a task
  that is `failed`, `rejected` or `canceled`, raises `AgentCommError`.

Inbound (`serve`), a received `SendMessage` request is unwrapped before
the agent's turn runs: the turn gets the text, the call ids and the
context id (a new one when the request has none). The answer goes back as
an agent message in that context. A turn that raises is a `failed` task
with the text `Agent execution failed`; the error stays in the serving
process's log. A request that is not JSON, not JSON-RPC 2.0, or not
`SendMessage` gets the JSON-RPC parse, invalid-request or
method-not-found error.

Over `slim`, the request travels inside MAS Lab's SLIM request frame: MAS Lab's
JSON-RPC in a SLIM frame, not the A2A SDK riding SLIM, so both ends must be
MAS Lab sessions with the `a2a-framing` row above the node row.
Over `http`, it is posted to an A2A server's url; such a chain only
connects. `local-group` cannot pass requests up to a layer, so
`[local-group, a2a-framing]` is refused.

Two ids name the two A2A roles. `a2a-framing` is this layer and always sits
above a transport; a chain that starts with it is refused. `a2a` is the
`agent_comm` link, the A2A SDK client at a url ([a2a.md](a2a.md)), which is a
transport and so only the bottom of a chain. `desk-a2a: {protocol: a2a-framing}`
frames A2A over the row below it, and a chain of one `a2a` server row reaches
the agent at that row's url.

```yaml
# infra rows
desk-node: {protocol: slim, url: "http://127.0.0.1:46357", identity: acme/lab/desk, secret_env: SLIM_SECRET}
desk-a2a: {protocol: a2a-framing}
# Flavour
bind:
  agent_comm: [desk-node, desk-a2a]
```

[Communication stack](../../../../../docs/references/comm-stack.md#chains-from-a-flavour),
[Tutorial 7, A2A over the node](../../../../../docs/tutorials/07-slim-link/README.md#a2a-over-the-node).

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# http — an HTTP POST transport for a stack

| Field | Value |
|-------|--------|
| **ID** | `http` |
| **Kind** | `layer` (a transport: the bottom layer of a chain) |
| **URN** | `mas.stack_layer.http` |
| **Implementation** | `HttpLayer` in `library_ioa.plugins.http_layer.transport` |
| **Manifest keys** | an infra row a Flavour binds at the bottom of `bind.agent_comm`: `{protocol: http, url: ..., headers: {...}, timeout: 30, dev: false}` |

Each send's payload is POSTed to `url` with `content_type` (default
`application/json`). The request headers are the layer's `headers`, then
the envelope's headers (the stack's `traceparent`, a protocol layer's
own); an envelope header wins on a clash. The response body is the reply;
a body over `max_message_size` bytes (default 1 MiB, the same default as
`slim`) is refused. A connection error, a timeout, or a status of 400 or more
raises `AgentCommError`; its text shows the url without its query or
fragment. The url is the address, so the send's target is not used. Params:
`url` (required), `headers`, `timeout` (seconds, default 30), `dev`,
`content_type`, `max_message_size`. ctl refuses a row whose url carries
credentials or a query key that names a secret (`?api_key=`). Header names are lowercased, so a header is never sent
twice under two spellings; a name that is not a token, or a value with a control
character, is refused when the layer is built. `host`, `content-length`,
`transfer-encoding`, `connection`, `keep-alive`, `upgrade`, `te` and `trailer`
are the HTTP client's own: a row or a send that sets one is refused. A header that would hold a secret
cannot be a Stack param (ctl refuses keys such as `authorization` or
`x-api-key`).

Plain `http` is only allowed on `127.0.0.1`, `localhost` and `::1`; any
other host needs `https`. A url with credentials, a backslash, a space or
a control character is refused. `dev` skips TLS verification, and only on
a loopback `https` url.

It only connects: it never receives. `usage: listen` for an agent on a
chain whose bottom row is `http` is refused when ctl applies the Flavour; serve an agent over HTTP with the `a2a` `agent_expose` plugin
(`mas-ctl serve`). Bind an [`a2a-framing`](../a2a/a2a-framing.md) row above it to reach such a server:

```yaml
# infra rows
outage-web: {protocol: http, url: "http://127.0.0.1:9003"}
outage-a2a: {protocol: a2a-framing}
# Flavour
bind:
  agent_comm: [outage-web, outage-a2a]
```

The url is the address, so every send of the chain goes to that one
server.

[Communication stack](../../../../../docs/references/comm-stack.md#chains-from-a-flavour).

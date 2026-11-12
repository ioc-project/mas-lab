<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# slim — SLIM L7 link

| Field | Value |
|-------|-------|
| **ID** | `slim` |
| **Kind** | `layer` (a transport: the bottom layer of a chain) |
| **URN** | `mas.stack_layer.slim` |
| **Implementation** | `SlimAgentComm` in `library_ioa.plugins.slim.agent_comm`; shared service `SlimService` in `library_ioa.plugins.slim.service` |
| **Extra** | `slim` of `library-ioa/` (`slim-bindings>=2.2,<3`) |
| **Status** | Group tests use in-process fake bindings that route replies by app name, as a node does. A real node is pytest marker `slim`; unicast and anycast sends return the peer's reply (slim-bindings 2.2 plus a node on 127.0.0.1:46357). |

`slim` is a transport layer. Declare the node as an infra row with
`url`, a three-part `identity` and `secret_env`, and bind it in a
Flavour (`spec.bind.agent_comm`):

```yaml
# infra
kind: Application
metadata: {name: desk}
spec:
  endpoints:
    desk-node: {protocol: slim, url: "http://127.0.0.1:46357", identity: acme/lab/desk, secret_env: SLIM_SECRET}
---
# Flavour
kind: Flavour
metadata: {name: desk-slim}
spec:
  bind:
    agent_comm: [desk-node]
    usage: [connect, listen]
```

Every agent of the session rides that chain unless `bind.agents.<id>`
says otherwise. Each agent's name is `<identity>/<agent_id>`
(`agent_name`). ctl links every agent the Flavour serves here (`listen`)
so a peer that never sends can still receive. The chain gives every agent
its own `SlimAgentComm`; they share one `SlimService`
(`shared_service`), which holds the node connection actor. The chain
starts it before the first agent and closes it after the last; a link
never owns it, and a link built without that service is refused. The
layer params are `url`, `identity`, `secret`, `dev`, `timeout_s`,
`reply_timeout_s`, `auth_mode` (only `shared_secret`) and
`max_message_size`; any other key is refused, and so is a secret under 32
bytes, a timeout that is not a positive number, or a size that is not a
positive integer, when the service is built.

With a protocol layer above it (for example `a2a-framing`), the stack gives the
link its inbound chain (`serve`): each request's payload goes through the
layers before the turn runs, and the reply goes back through them. With
no such layer the link calls the turn itself, as before.

On the node, a received frame names its sender by the three-part app
name, without the instance id. So each agent registers the app
`<org>/<ns>/<svc>.<agent_id>` (`acme/lab/mas.buyer`), and replies
route back to that agent. Each agent also subscribes to the bare
service `<org>/<ns>/<svc>`, which is what an anycast send targets.
Services and agent ids cannot contain `.` on SLIM: `mas` + `x.y` and
`mas.x` + `y` would be the same app name, and the bare service `mas.x`
would be the app name of agent `x` in `mas`. ctl refuses such a service or
agent id at bind time (`check_agent_id`), before anything connects. Several chains in one
process share one event loop, because slim-bindings keeps a single
global loop. A peer outside MAS Lab addresses an agent
by that app name.

ctl reads the env-var named by `secret_env` and passes the value into
the plugin. The plugin never reads the environment. `dev` is off
unless set; it skip-verifies TLS on loopback HTTPS. HTTP is only
allowed on `127.0.0.1`, `localhost`, and `::1`; a url with a backslash,
a space, a control character, or credentials in it is refused, and the
scheme is `http` or `https`. Shared-secret auth
needs a value at least 32 bytes. Anyone who holds the shared secret
can register as any agent name on that node. `headers` on `send` or
`publish` travel in the envelope, as do the call id, parent-call id,
context id, and inform/ask intent. The stack adds a child
`traceparent` to every send. The authenticated session name is
the sender; a `sender` field in the payload is ignored.

A handler runs a whole turn, so each inbound request is served in a
worker thread, one session at a time in order. The handler can call
`send` to another agent, and a long turn does not stop the other agents
of the process. `timeout_s` (default 2) is the I/O timeout;
`reply_timeout_s` (default 120) is how long a send waits for the
peer's reply. A handler that calls `stop()` on its own link gets its
reply sent first.

Limits and failure. A send, a reply and a name are checked before they
reach the node: a task over `max_message_size`, a reply over it (plus a
64 KiB envelope allowance), and a target that is not a name (letters,
digits, `.`, `_`, `-`, parts split by `/`) are refused. Sends to one
target go one at a time, because they share a session and a second
request's reply could be read by the first. A send that gets no reply
drops its session, so a late reply cannot answer the next request. Every
call to the node has a deadline (`reply_timeout_s` plus four I/O
timeouts for a send); a call that times out or whose caller is cancelled
is cancelled on the loop too. A link with no turn (an agent this process only
sends from) refuses every request it receives (`the agent is not served on this link`). A link serves at most 32 inbound requests
at once and stops reading while it is full, so a fast peer waits on the
node. A start that fails half way closes its connection. After `MAX_READ_ERRORS` (5)
consecutive read failures (a read that only found nothing before its deadline is
not one; any other error is, a timeout included) the link drops the connection and
reconnects; a send waits for the new connection or `reply_timeout_s`. Stopping a
link twice, or before it started, does nothing.

`library-ioa` already depends on `library-standard`. The SLIM
link uses the same comm helpers as `local-group` (`headers`, `names`,
`payload`, `send_context`, `capabilities`).

SLIM is two things people mix up. The **channel** is the link
(`SlimAgentComm`: send by name, anycast, groups). The **node** is what
the row's `url` and `secret_env` point at. An in-process fake is the
channel without a node. For A2A over SLIM, bind an `a2a-framing` row above
the node row (`agent_comm: [desk-node, desk-a2a]`, where `desk-a2a` is a
`protocol: a2a-framing` row); each send then travels as an A2A `SendMessage`
request inside the SLIM frame. `mas-ctl serve`
cannot host an agent on a SLIM chain. `--connect` stays `a2a://`
and `mcp://`.

`mas-slim listen` and `mas-slim send` join a node by hand, as one agent
on a one-layer `slim` chain. They read the secret from standard input or
`--secret-file`, never from the environment.

To start a local node in Docker, see
[tutorial 7, step 3](../../../../../docs/tutorials/07-slim-link/README.md#step-3-swap-the-medium-for-a-slim-node).

Rows and Flavour: [Rows a Flavour binds](../../../../../docs/manifests/infra.md#rows-a-flavour-binds).

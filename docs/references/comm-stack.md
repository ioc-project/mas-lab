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

## Local group

`local-group` is a link, so it sits at the bottom of a stack. Each agent
has its own link and a four-part name `org/ns/service/instance`. This PR
is the Python API: you construct one `LocalGroupFabric` and pass it into
every link. The session does not inject that fabric yet, and it cannot
come from YAML. A binding with only `params.name` gets a private fabric,
so two such links cannot see each other. There is no process-wide named
registry: a string fabric name is an error.

`run_turn` (or `on_request`) is the inbound handler. `send` forwards
`correlation_id`, `caller_call_id`, and `context_id`. `asend` uses
`run_turn.asend` when that exists. A send to a link with no handler
raises; it does not invent an `ack:`. `stop` unregisters the link, removes
channels it moderated, drains its inbox, and rejects later sends. A new
link may reuse the name after that and does not inherit the old channels.

Unicast is request/reply. Three-part names are seedable anycast that stick
after the first pick. Channels have a moderator (the creator). Creating a
channel that already exists raises unless you are that moderator. Channel
names are validated like agent names; they cannot shadow an agent or sit
in another namespace. Members are stored as the full four-part name.
`publish(to=...)` requires that target to be a member. A subscriber
callback consumes the message. With no subscriber, the message waits in
the inbox until `receive` or `drain`. Fan-out checks every waiting inbox
first, then delivers, so a full inbox fails the whole publish. History is
appended before subscribers run, so a reply is recorded after the original
message. One subscriber raising does not stop the others.

Inboxes and history are bounded (`inbox_limit`, `history_limit`,
`overflow`, `max_message_size`). The plugin claims `unicast`, `anycast`,
`group`, `ordered`, and `isolation`. It does not claim `reliable`, `e2e`,
or `persistent_groups`. `local` stays the session turn bus. Delegation in
a MAS still uses short agent ids on `local`; four-part names are only for
this opt-in link.

## Send tool

This change puts `delegate_to_<id>` and a Python `send` call on one code
path. The model still sees only `delegate_to_<id>` tools, with the same
`task` and `context_id` arguments. There is no `send` tool in the model's
list. A user tool named `send` stays that tool.

The target of `delegate_to_b` comes from the tool name; an extra
`target` or `intent` argument is rejected. With no peer list, send
reaches nobody. Unknown intent is an error. Intent rides
`SendContext.intent`. `send` / `asend` pass `headers=` and `context=`
only when that plugin's `send` takes those arguments.

When the session context has `agent_comm` or `agent_comm_routes`, those
replace the ones given at construction. Routes replace whole; they do
not merge.

Traces and the knowledge graph treat a handoff as a recorded
`concept=agent_comm` tool, or a legacy `delegate_to_<id>` name. A user
tool named `send`, or a tool that only has a `target` argument, is not a
handoff.

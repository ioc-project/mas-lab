<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# A2A

<p class="eyebrow">Open standards</p>

[Agent2Agent](https://a2a-protocol.org/) (A2A) is the open protocol for calling
a remote agent. Two agents often run in different processes or on different
hosts. They need a shared wire protocol.

A2A is separate from LLM access, from [MCP](mcp.md) (remote tools), and from
delegation policy. The agent spec stays the same; infra chooses local bus or
A2A.

## How MAS-Lab implements it

The A2A plugins ship in `mas-library-ioa` and use the official `a2a-sdk`.

- `mas.agent_comm.a2a` is the outbound client.
- `mas.agent_expose.a2a` is the server.

Every live-agent entry point (`mas-ctl serve`, `chat`, `tui`, `run-mas`) uses
the same `agent_expose` path (`mas.ctl.session.exposure`). Listen URLs come
from an `infra/v1` Application endpoint. There are no `--protocol`, `--host`,
or `--port` flags.

## Dependencies

| Piece | Package |
| --- | --- |
| Client and server plugins | `mas-library-ioa` |
| Official SDK | `a2a-sdk[http-server,sqlite]>=1.1.5` |
| Official CLI (Go binary `a2a`) | not the PyPI package `a2a-cli` |

## Compliance

Evidence against the official TCK, recorded 2026-09-29:

| Check | Result |
| --- | --- |
| Official `a2a` CLI card discovery and message send | Passed against Tutorial 1 agent |
| Official A2A TCK, all transports | 79.8% overall (`88/114` MUST, `7/11` SHOULD, `4/4` MAY) |
| Report | [`a2a-tck-compatibility-2026-09-29.json`](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/compliance/a2a/a2a-tck-compatibility-2026-09-29.json) |

Implemented: AgentCard at `/.well-known/agent-card.json`, JSON-RPC and REST,
text messages and task completion, cancellation events, context/task/message
identity mapping, dynamic card fields from the manifest. The deterministic
compliance agent uses `scripted_response`; an LLM-backed endpoint is not a TCK
fixture. Details:
[protocol-compliance.md](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/docs/a2a/protocol-compliance.md).

## Quickstart

1. Declare the listener in infra. Key it by the agent `metadata.name`.

```yaml
apiVersion: infra/v1
kind: Application
metadata:
  name: a2a-qa
spec:
  endpoints:
    qa-agent:
      protocol: a2a
      usage: listen
      url: http://127.0.0.1:9005
```

2. Start a dedicated server process:

```bash
mas-ctl serve docs/tutorials/01-building-an-agent/agent.yaml \
  --infra-ref path/to/a2a-qa.yaml
```

   Alternatively, the same bind happens in the background when that agent owns the in-process conversation:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  --infra-ref path/to/a2a-qa.yaml -i
```

3. Inspect the agent card and send a message with the official A2A CLI:

```bash
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "What is the capital of France?"
```

## Endpoint fields

| Field | Meaning |
| --- | --- |
| `protocol` | `a2a` selects the A2A `agent_comm` / `agent_expose` plugins. |
| `usage` | `connect` (client route only), `listen` (listen only), or `[connect, listen]`. Omitted means `connect`. `use`, `deploy`, `use-and-deploy` are deprecated aliases. The legacy `expose: true` reads as `[connect, listen]` when `usage` is absent. |
| `url` | Advertised absolute `http(s)://host:port` URL. It cannot carry a path prefix. |
| `headers`, `timeout` | Client-side options passed to the outbound A2A client (`usage: connect`). |
| `a2a.listen.host` / `a2a.listen.port` | Local bind address when it differs from `url`. Required when `url` is `https://`. |
| `a2a.grpc_port` / `a2a.grpc_url` | Also serve and advertise gRPC (dedicated `mas-ctl serve` only). |
| `a2a.capabilities` | AgentCard capabilities: `streaming`, `pushNotifications`, `extendedAgentCard`, `extensions`. All default to off. |

The endpoint serves `/.well-known/agent-card.json`, JSON-RPC at `/`, and HTTP+JSON under `/a2a/rest`. gRPC is added when `a2a.grpc_port` is set.

## `--connect`

`--connect NAME=URI` is the shortcut for a `usage: connect` client route. It works on `serve`, `chat`, `tui`, `run-mas`, and `compose`. `--bind` is the hidden old name. Forms, merge order, and examples are on [CLI overrides → Connect](../cli/overrides.md#connect). `--connect` never opens a listener.

## MAS configuration

`workflow.nodes[].delegates_to` declares who may be contacted. Infra declares how each named agent is reached:

```yaml
spec:
  agency:
    agents:
      - id: moderator
        ref: agents/moderator.yaml
      - id: weather-oracle
  workflow:
    entry: moderator
    nodes:
      - id: moderator
        delegates_to: [weather-oracle]
      - id: weather-oracle
```

- An agency entry without `ref` is an external dependency. It needs an infra endpoint with `usage: connect` or `[connect, listen]`.
- An entry with `ref` and no such endpoint is local. It uses the local agent bus.
- The matching infra endpoint is keyed by the same agent id.
- A `delegates_to` target that matches neither fails during wiring.

## Public exposure

A public agent endpoint is an infra Application endpoint keyed by agent name:

```yaml
spec:
  endpoints:
    weather-oracle:
      protocol: a2a
      usage: listen
      url: http://127.0.0.1:9005
```

- `mas-ctl serve` hosts one agent per process and blocks.
- `chat`, `tui`, and `run-mas` host the conversation in-process. They also bind, in the background, every hosted agent whose A2A endpoint has `usage: listen` or `[connect, listen]`.
- `usage: connect` stays a client route. Run a separate `mas-ctl serve` for that port.
- Each listen URL has one AgentCard. Path-based multiplexing on a single port is not supported.

## Task and session identity

- A2A `contextId` maps to MAS `session_id` and working-memory continuity. A request without one starts a new session.
- A2A `messageId` (or `taskId` when absent) becomes the MAS turn id.
- A2A `message/send` queues a turn at the tail of the session's turn queue (`SessionTurnMailbox`). Concurrent messages on one context run in order.
- A2A `tasks/cancel` marks the A2A task canceled. It does not call `ControlContract.cancel_inflight`. A turn already running in the runtime finishes in the background.
- There is no A2A steer RPC. Preempt, replace, and after exist only in the control protocol. `mas-ctl serve` advertises a control endpoint (disable with `--no-control`), so `mas-ctl control <contextId> …` can attach.
- MAS correlation and parent-call identifiers travel in A2A message metadata
  (`mas.correlation_id`, `mas.caller_call_id`).

## Related

- [Tutorial 5 — A2A agents](../tutorials/05-a2a-agents/README.md)
- [A2A internals](../a2a/developer.md)
- [User guide: running agents through A2A](../user-guide.md#running-agents-through-a2a)
- [Kernel operations](../references/kernel-primitives.md): sessions, steer, and the queue.
- [Infra manifest](../manifests/infra.md)

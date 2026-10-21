# A2A agent communication

This page explains how agents talk to each other over A2A. Read it to serve an agent on a URL, call a remote agent, or wire a multi-agent system (MAS) across processes.

## The problem

Two agents often run in different processes or on different hosts. They need a shared wire protocol. MAS Lab uses A2A (Agent2Agent), an open protocol for calling a remote agent.

A2A is separate from LLM access, MCP (Model Context Protocol, for remote tools), and delegation policy.

## How it is wired

The A2A plugins ship in `library-ioa` and use the official `a2a-sdk`. Install that library to use anything on this page.

- `mas.agent_comm.a2a` is the outbound client.
- `mas.agent_expose.a2a` is the server.

Every live-agent entry point (`mas-ctl serve`, `chat`, `tui`, `run-mas`) uses the same `agent_expose` path (`mas.ctl.session.exposure`). Listen URLs come from an `infra/v1` Application endpoint. There are no `--protocol`, `--host`, or `--port` flags.

An *infra bundle* is a set of `infra/v1` files that say where things are. An *endpoint* is one entry in it that names a URL and a protocol.

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
      usage: deploy
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

3. Inspect the agent card and send a message with the official A2A CLI. It is a Go binary named `a2a`, not the unrelated PyPI package `a2a-cli`:

```bash
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "What is the capital of France?"
```

## Endpoint fields

| Field | Meaning |
| --- | --- |
| `protocol` | `a2a` selects the A2A `agent_comm` / `agent_expose` plugins. |
| `usage` | `use` (client route only), `deploy` (listen only), or `use-and-deploy`. Omitted means `use`. The legacy `expose: true` reads as `use-and-deploy` when `usage` is absent. |
| `url` | Advertised absolute `http(s)://host:port` URL. It cannot carry a path prefix. |
| `headers`, `timeout` | Client-side options passed to the outbound A2A client (`usage: use`). |
| `a2a.listen.host` / `a2a.listen.port` | Local bind address when it differs from `url`. Required when `url` is `https://`. |
| `a2a.grpc_port` / `a2a.grpc_url` | Also serve and advertise gRPC (dedicated `mas-ctl serve` only). |
| `a2a.capabilities` | AgentCard capabilities: `streaming`, `pushNotifications`, `extendedAgentCard`, `extensions`. All default to off. |

The endpoint serves `/.well-known/agent-card.json`, JSON-RPC at `/`, and HTTP+JSON under `/a2a/rest`. gRPC is added when `a2a.grpc_port` is set.

## `--bind`

`--bind NAME=URI` is a shortcut for a `usage: use` client route. It works on `serve`, `chat`, `tui`, `run-mas`, and `compose`.

- It expands to `--override infra:…` and merges with `--infra-ref` YAML. There is no second bind resolver.
- Explicit `--override` values still win.
- The flavour stays `local`. A *flavour* is a deployment preset.

Forms:

- `NAME=a2a://host:port[/path]` (or `a2as://` for HTTPS) adds `spec.endpoints[NAME]` with `protocol: a2a`, `usage: use`.
- `NAME=mcp://host:port/mcp#tool` (or `mcps://`) adds a `spec.tool_servers` entry with `usage: use` that exposes `tool` (default: `NAME`).

```bash
mas-ctl run-mas mas.yaml \
  --bind weather-oracle=a2a://127.0.0.1:9005 \
  --bind analyze_transaction_risk=mcp://127.0.0.1:8080/mcp#analyze_transaction_risk
```

`--bind` never opens a listener. Something else, such as a separate `mas-ctl serve`, must own that port.

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

- An agency entry without `ref` is an external dependency. It needs an infra endpoint with `usage: use` or `use-and-deploy`.
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
      usage: deploy
      url: http://127.0.0.1:9005
```

- `mas-ctl serve` hosts one agent per process and blocks.
- `chat`, `tui`, and `run-mas` host the conversation in-process. They also bind, in the background, every hosted agent whose A2A endpoint has `usage: deploy` or `use-and-deploy`.
- `usage: use` stays a client route. Run a separate `mas-ctl serve` for that port.
- Each listen URL has one AgentCard. Path-based multiplexing on a single port is not supported.

## Task and session identity

- A2A `contextId` maps to MAS `session_id` and working-memory continuity. A request without one starts a new session.
- A2A `messageId` (or `taskId` when absent) becomes the MAS turn id.
- A2A `message/send` queues a turn at the tail of the session's turn queue (`SessionTurnMailbox`). Concurrent messages on one context run in order.
- A2A `tasks/cancel` marks the A2A task canceled. It does not call `ControlContract.cancel_inflight`. A turn already running in the runtime finishes in the background.
- There is no A2A steer RPC. Preempt, replace, and after exist only in the control protocol. `mas-ctl serve` advertises a control endpoint (disable with `--no-control`), so `mas-ctl control <contextId> …` can attach.
- MAS correlation and parent-call identifiers travel in A2A message metadata
  (`mas.correlation_id`, `mas.caller_call_id`).

## See also

- [A2A developer reference](developer.md)
- [Compliance report](https://github.com/outshift-open/mas-lab/blob/main/library-ioa/docs/a2a/protocol-compliance.md)
- [Tutorial 5: A2A agents](../tutorials/05-a2a-agents/README.md)
- [Kernel operations](../references/kernel-primitives.md): sessions, steer, and the queue.
- [Infra manifest](../manifests/infra.md)

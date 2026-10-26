<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# A2A internals

This page is for developers who extend or debug the A2A transport. It covers the contract boundaries, routing, the AgentCard, the task lifecycle, and exposure. For setup, see [Open standards → A2A](../standards/a2a.md).

Terms: a *contract* is a fixed interface the runtime checks. A *plugin* implements a contract and is selected by name. A *session* is one live run. *HITL* means human in the loop.

## Contract boundaries

Two contracts split the work.

- `DelegationContract` (`mas.runtime.boundary.delegation`) owns the LLM-visible `delegate_to_<agent>` tool surface.
- `AgentCommContract` (`mas.runtime.boundary.agentcomm`) owns only the low-level send, after delegation is decided.

The runtime registry selects `AgentCommContract` implementations (category `agent_comm`):

- `LocalAgentComm` (`library-standard`): in-process bus.
- `A2AAgentComm` (`library_ioa.plugins.a2a.agent_comm`): official `a2a-sdk` client. The constructor parameters `url`, `headers`, and `timeout` come from the infra Application endpoint.

The A2A server is a separate ingress adapter. It uses registry category `agent_expose` (`library_ioa.plugins.a2a.exposure.A2AExposure`). It is not an `AgentCommContract` implementation.

It maps each incoming A2A `message/send` onto the session turn mailbox (`mas.ctl.session.mailbox.SessionTurnMailbox`). The message is queued at the tail, and the call waits for its own turn. `AgentCommContract.send` is the outbound peer send after delegation.

## Steer, cancel, and input-required

A2A has no steer RPC. Extra messages on a non-terminal working task are queued. They do not preempt an in-flight decode.

Control-protocol `steer` can do three things:

- preempt: keep the prefix, replace the rest.
- replace: discard the prefix, start a new turn.
- wait (`after`): queue to the front until the current generation finishes.

A2A `tasks/cancel` cancels the server-side wait and emits a `CANCELED` status. It does not call `ControlContract.cancel_inflight`, so the runtime turn in progress is not interrupted.

`input-required` is HITL. It is not a mid-token splice.

## Registry and routing

`infra/v1` `Application.spec.endpoints` holds the public protocol configuration. It is keyed by the exact agency agent id. The runtime registry category stays `agent_comm`. The endpoint's `protocol` selects the implementation.

The MAS-wide in-process comm variant resolves from `spec.agent_comm` (`type` or `protocol`, with optional `params`) through `registry.create`. If it is omitted, the library default (`defaults.agent_comm`) applies. The delegation plugin does not choose it, and `ctl` does not hardcode it.

The route builder resolves every `delegates_to` target against materialized local agents and against infra endpoints with `usage: use` or `use-and-deploy`. Missing targets fail during wiring.

## AgentCard

An *AgentCard* is the JSON document that describes an agent to A2A clients. `agent_card_from_manifest()` (`library_ioa.plugins.a2a.agentcard`) maps manifest metadata and skills to the official A2A `AgentCard`:

- `name` and `version` (default `0.1.0`) come from `metadata`.
- `description` comes from `spec.description`, then `metadata.description`, then the name.
- One `AgentSkill` per `spec.skills` entry. A single general skill when there are none.
- `provider` comes from `spec.provider`. `organization` defaults to `MAS Lab`.
- Capabilities come from the endpoint's `a2a.capabilities`. Default input and output modes are `text/plain`.
- Interfaces: JSON-RPC at the advertised URL, HTTP+JSON at `<url>/a2a/rest`, and gRPC when `a2a.grpc_port` (or `a2a.grpc_url`) is set.

## Task lifecycle

MAS already has `session_id` for a complete run and a turn id for each input. A2A reuses them, so no second session model exists.

- A2A `contextId` maps to `session_id`. A new one is generated when absent.
- `messageId` (falling back to `taskId`) becomes the turn id.
- Parent and correlation identifiers travel in A2A message metadata: `mas.caller_call_id` and `mas.correlation_id`.

The executor (`library_ioa.plugins.a2a.server.executor.MasLabAgentExecutor`) supports:

- immediate text responses (a `Message`, or a status update on an existing task);
- text, data, file, and URL artifacts, and streamed text chunks as artifact updates;
- the `working`, `completed`, and `input_required` task states (a turn that stops on a HITL request reports `input_required`);
- cancellation events and push notifications (when `a2a.capabilities.pushNotifications` is on).

Tasks and push configurations live in memory (the SDK's `InMemoryTaskStore`). There is no durable task storage and no multi-replica subscription. The A2A compliance page lists what the TCK (technology compatibility kit) does and does not score.

## Exposure topology

The infra endpoint entry does two jobs. It declares outbound reachability. It also marks public availability with `usage: deploy` or `use-and-deploy`. The legacy `expose: true` reads as `use-and-deploy` when `usage` is absent.

These settings are independent of agency topology on purpose:

- A private local worker may be exposed.
- An external dependency may be consumed without being exposed.
- An agent may have both a local `ref` and a named infra endpoint, when an overlay deliberately routes it remotely.

`mas-ctl serve`, `chat`, `tui`, and `run-mas` share one exposure helper (`mas.ctl.session.exposure`). Listen URLs come from Application endpoints.

- **Dedicated `serve`:** a blocking listener for that agent. It needs `usage: deploy` or `use-and-deploy`. It advertises a control endpoint in the control directory unless you pass `--no-control`. It is the only path that starts the gRPC listener.
- **`chat` and `tui`:** the in-process agent owns the conversation. A deployed endpoint binds in the background for interrogation. Stdin or curses stays the conversation.
- **`run-mas`:** the entry agent is always in-process. Every hosted agent whose A2A endpoint has `usage: deploy` or `use-and-deploy` binds in this process. `usage: use` peers are client routes. Another process, such as a separate `mas-ctl serve`, owns those ports.

`--bind NAME=a2a://…` is a shortcut for an Application `usage: use` row. It expands to `--override infra:spec.endpoints[...]=…` on the same merged infra document as `--infra-ref`. It is not a second exposure path. It never opens a listener.

The advertised `url` cannot include a path prefix. A single port cannot host multiple independent AgentCards. The runtime does not infer either.

## Compliance workflow

The optional tooling lives under `library-ioa/compliance/a2a`. See the top-level `compliance/README.md`.

- The official A2A TCK is a Git checkout, because its top-level runner is not in the published wheel.
- The official `a2a` CLI is a Go binary.
- MCP uses the official npm conformance framework (`library-ioa/compliance/mcp`).
- Reports are generated outside runtime tests.

## See also

- [Open standards → A2A](../standards/a2a.md)
- [Kernel operations](../references/kernel-primitives.md): the session queue and steer verbs.
- [Contracts](../references/contracts.md)
- [Tutorial 5: A2A agents](../tutorials/05-a2a-agents/README.md)

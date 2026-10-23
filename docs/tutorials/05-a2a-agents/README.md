<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 5 — A2A agents

You move one delegation edge from in-process communication to the Agent2Agent (A2A) protocol.
Then you run a mixed topology: one specialist is remote and two stay local.

A2A lets agents in separate processes find and call each other. Infra handles agent-card discovery, protocol negotiation,
and the task lifecycle. An agent card is the JSON description of an agent that A2A clients fetch.
The MAS workflow and agent prompts do not change.

## What you need

- **Packages:** `mas-ctl` and `mas-library-ioa` (A2A plugins, in the standard install).
- **Prerequisites:** [Tutorial 0](../00-environment-setup/README.md) (install, LLM access) and
  [Tutorial 2](../02-creating-a-mas/README.md) (this tutorial reuses its MAS and `schedule_agent`).
- The official A2A client: a Go binary named `a2a` (not the PyPI package `a2a-cli`).
- Run all commands from the repository root. Serving needs an API key only when a request reaches the LLM.

## Step 1 — Read the A2A infra manifest

A remote agent is an `Application` endpoint keyed by the exact agent id
(fields: [A2A](../../standards/a2a.md), [infra.md](../../manifests/infra.md#a2a-agent-endpoints)):

```yaml
apiVersion: infra/v1
kind: Application
metadata:
  name: a2a-agents-local
spec:
  endpoints:
    schedule_agent:
      protocol: a2a
      usage: use-and-deploy
      url: http://127.0.0.1:9006
```

| Field | Meaning |
| --- | --- |
| `protocol` | `a2a` selects the A2A transport for that route. |
| `url` | Required. The remote agent endpoint. |
| `usage` | `use` consumes the peer; `deploy` exposes a local A2A server; `use-and-deploy` does both. |
| `headers` | Optional auth. Use `env:VAR`, never static secrets. |

The legacy `expose: true` reads as `usage: use-and-deploy`. Prefer `usage`.

Each layer owns one thing:

| Layer | Owns |
| --- | --- |
| MAS manifest | Agent ids, workflow, `delegates_to` topology |
| Agent manifest | Prompt, model, tools, skills |
| `Application` infra | Endpoint id, protocol, URL, headers, exposure |

Delegation always goes through `AgentCommContract.send(target_agent_id, task, ...)`.
The local plugin runs an in-process agent. The A2A plugin sends the same task through an A2A client.

![Local and A2A communication share AgentCommContract](tutorial-05.1-logic-infra-contract.svg)

**Figure 1:** Infra replaces the local handler with A2A. The target id and contract stay fixed.

## Step 2 — Serve an existing agent

Serve Tutorial 2's unchanged `schedule_agent`.
[`mixed-agents.infra.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-samples/infra/mixed-agents.infra.yaml)
holds the endpoint shown in Step 1:

```bash
mas-ctl serve \
  "$PWD/docs/tutorials/02-creating-a-mas/agents/schedule-agent/agent.yaml" \
  --infra-ref "$PWD/library-samples/infra/mixed-agents.infra.yaml"
```

`mas-ctl serve` blocks and listens on port 9006. It starts without a key, but a request that reaches the LLM needs `OPENAI_API_KEY`.
In a second terminal, fetch the agent card:

```bash
a2a card get http://127.0.0.1:9006
```

You should see `Name: schedule_agent`, its interfaces (`JSONRPC` on port 9006, `HTTP+JSON`), and the skill `transport-schedule-lookup`. The runtime generates the card from the agent manifest.

## Step 3 — Mix local and A2A agents

Tutorial 2's MAS delegates from `moderator` to three ids:

```yaml
workflow:
  entry: moderator
  nodes:
    - id: moderator
      delegates_to: [schedule_agent, itinerary_agent, concierge_agent]
```

Start the Step 2 server first (`mas-ctl serve` command above, left running). `--bind` acts as `usage: use`, so this process does not take port 9006:

```bash
mas-ctl run-mas docs/tutorials/02-creating-a-mas/mas.yaml \
  --bind schedule_agent=a2a://127.0.0.1:9006/ \
  -q "Plan a trip and compare the available transport schedules."
```

Passing `--infra-ref` to `run-mas` instead is the all-in-one path: hosted `use-and-deploy` specialists bind in this process.
Do not combine it with a separate `mas-ctl serve` on the same port.
When a hosted agent has `deploy` or `use-and-deploy`, `run-mas`, `chat`, and `tui` still drive the conversation in-process.
You can query the live instance with `a2a card get http://127.0.0.1:9001`. Specialists with `usage: use` need their own `mas-ctl serve` (or `--bind`).

| Agent id | Owner | Dispatch |
| --- | --- | --- |
| `schedule_agent` | Explicit `Application` endpoint, `protocol: a2a` | Remote A2A message |
| `itinerary_agent`, `concierge_agent` | Local agents, no endpoint | In-process turn |

An explicit endpoint overrides a local agent with the same id. One MAS manifest runs as one process in development
and distributed elsewhere.

![Agent discovery and dispatch](tutorial-05.2-discovery-dispatch.svg)

**Figure 2:** Dispatch resolves the target id and calls one `AgentCommContract` implementation.
The A2A client reads the peer's agent card to pick a wire binding.

How routing works:

1. MAS Lab records the MAS's agents as local routes and reads `delegates_to` for target ids.
2. An endpoint with `usage: use` or `use-and-deploy` creates an external provider that overrides the local route for that id.
3. A delegation to `schedule_agent` fetches its card and picks an advertised interface. Other ids stay local.

Missing routes fail when the table is built, so protocol selection is deterministic.

## Step 4 — Switch back to local communication

Run the same MAS with no `--bind` and no `--infra-ref` (the Step 2 server is not needed):

```bash
mas-ctl run-mas docs/tutorials/02-creating-a-mas/mas.yaml \
  -q "Plan a trip and compare the available transport schedules."
```

All three specialists now use the local provider.

## Step 5 — Keep a session across requests

A2A reuses the protocol's `contextId` as the session id. Requests without one are independent sessions.
Start the Step 2 server first. Send the first response's `contextId` on a later request to join the same session:

```python
from library_ioa.plugins.a2a.client import A2AClient

client = A2AClient(url="http://127.0.0.1:9006")

first = client.send_message("Plan day one around the morning train.")
print(first)  # look for "context_id" — top-level on a Task response,
              # nested under "result" on a Message response
context_id = first.get("context_id") or (first.get("result") or {}).get("context_id")

second = client.send_message("Now add day two.", context_id=context_id)
client.close()
```

The one-shot CLI in Step 6 has no `--context-id` flag yet, so call `A2AClient` for sessions.
The server uses the inbound `contextId` as `session_id` (`library_ioa/plugins/a2a/exposure.py`) or mints a new one.
Two callers with the same `contextId` share one turn queue: the second turn waits.
[Tutorial 13](../13-control-and-debug/README.md) covers queueing and attaching to a live session.

## Step 6 — Test the remote agent directly

With the Step 2 server running (both commands reach the LLM, so they need `OPENAI_API_KEY`):

```bash
a2a send -a http://127.0.0.1:9006 \
  "Find transport schedules from Luminara to Verdantia."

python -m library_ioa.plugins.a2a.cli --a2a http://127.0.0.1:9006 \
  --a2a-transport JSONRPC \
  --message "Find transport schedules from Luminara to Verdantia."
```

The first uses the official `a2a` CLI. The second uses the repository's one-shot Python client and prints JSON.

## What you learned

- Only infra changed. A2A owns discovery, transport negotiation, and the task lifecycle.
- The workflow, target ids, agent behavior, and `AgentCommContract` stay stable.
- An explicit endpoint overrides a local agent with the same id, so one MAS runs local or distributed.

Reference: [A2A](../../standards/a2a.md), [A2A internals](../../a2a/developer.md),
[infra.md § A2A agent endpoints](../../manifests/infra.md#a2a-agent-endpoints),
[Kernel operations](../../references/kernel-primitives.md), [Thin waist](../../references/thin-waist.md),
[A2A compliance workflow](https://github.com/ioc-project/mas-lab/tree/main/library-ioa/compliance/a2a).

## Next

[Tutorial 6 — Agent skills](../06-agent-skills/README.md), or [Tutorial 13](../13-control-and-debug/README.md)
to attach to a running session by its `contextId`.

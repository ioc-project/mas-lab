<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# User guide

Day-to-day use after you have a working install. Install first:
[Getting started](getting-started.md).

You declare agents and experiments in YAML (manifests). `mas-ctl` runs agents
and teams. `mas-lab` runs experiments. The web UI and live dashboard talk to
the controller API.

**Related:** [Writing manifests](manifests/writing-manifests.md) ·
[Labs vs libraries](labs-and-libraries.md) ·
[User configuration](user-config.md) ·
[Command line](cli/index.md) ·
[Web UI](ui/index.md) ·
[Live dashboard](ui/live.md) ·
[Controller API](cli/serve.md) ·
[Glossary](glossary.md) ·
[Open standards](standards/index.md)

---

## Ways to work with MAS Lab

Install once ([Getting started](getting-started.md) or
[Tutorial 0](tutorials/00-environment-setup/README.md)). Then pick a surface
from the left menu:

- **Guides** — manifests, labs vs libraries, user configuration
- **Command line** — how the surfaces fit together ([index](cli/index.md))
- **Interfaces** — [Web UI](ui/index.md), [live dashboard](ui/live.md)
  (`task demo-ui`), [TUI](ctl/tui.md)

Flag tables, compile, overrides, observability, and the controller API are
[References → CLI](references/index.md#cli).

Hands-on path: [Discover](discover/index.md) (tutorials, labs, IoC integrations).
Paper reproduction: [Paper](paper/index.md).

---

## Command line and interfaces

Topic map: [command-line index](cli/index.md).
Flag tables: [References → CLI](references/index.md#cli).

| Surface | Use for |
|---------|---------|
| [`mas-runtime`](cli/mas-runtime.md) | Kernel library. The console script only points at `mas-ctl`. |
| [`mas-ctl`](cli/mas-ctl.md) | Chat, TUI, validate, compile, compose, `run-mas`, A2A `serve` |
| [`mas-lab`](cli/mas-lab.md) | `init`, benchmarks, telemetry, plots, `kg`, `eval` |
| `mas-mcp` | MCP tool server (see [MCP](#running-tools-through-mcp)) |
| [Web UI](ui/index.md) | Browser studio |
| [Live dashboard](ui/live.md) | `task demo-ui` / `mas-lab demo-ui` |
| [TUI](ctl/tui.md) | `mas-ctl tui` |
| [Controller API](cli/serve.md) | `mas-lab serve` — REST backend for both browser surfaces |

Benchmarking is always `mas-lab`, never `mas-ctl`. Install paths:
[Getting started](getting-started.md). Package map:
[libraries.md](libraries.md).

A lab is a folder of experiments. A library is reusable code and YAML. See
[labs-and-libraries.md](labs-and-libraries.md). Manifest refs:
[writing-manifests.md](manifests/writing-manifests.md).

---

## Sample apps

Two ways to name a sample app:

- `app: trip-planner` in an experiment's `application:` block. It resolves the
  app's `mas.yaml` through the library catalog (here `mas-library-samples`).
- `samples:apps/trip-planner/mas.yaml` anywhere a manifest path is accepted,
  for example `mas-ctl run-mas samples:apps/trip-planner/mas.yaml`.

In a source checkout, relative paths under `library-samples/` also work. Prefer
`app:` or `samples:` references in committed labs: they do not depend on your
machine.

---

## Typical workflow

1. Write an `agent.yaml` and chat with it using `mas-ctl chat`
   ([Tutorial 1](tutorials/01-building-an-agent/README.md)).
2. Compose agents into a team (`mas.yaml`) and run it with `mas-ctl run-mas`
   ([Tutorial 2](tutorials/02-creating-a-mas/README.md)).
3. Turn the variations you want to compare into overlays and an
   `experiment.yaml`, then run it with `mas-lab benchmark run`
   ([Tutorial 3](tutorials/03-experiments-and-analysis/README.md)).
4. Add libraries (skills, MCP, telemetry, knowledge graph) as needed. See the
   [package map](libraries.md).

---

## Referencing apps from labs

A lab can reference the app it runs in three ways.

### 1. Library app via `app:` or `samples:` scheme (recommended)

```yaml
experiment:
  application:
    app: trip-planner
    configs_dir: ./overlays
```

### 2. Path inside the current workspace

```yaml
experiment:
  application:
    manifest: ../../apps/my-app/mas.yaml
    configs_dir: ./overlays
```

Use this in local development when the app is in the same checkout but not
packaged. Use a path relative to the experiment file. An absolute path works
only on your machine.

### 3. Symlink through a local library-like folder

Link the app directory into a folder listed under `lab.libraries` or
`manifest_libraries`. Use this for local composition only, not for committed labs.

Committed examples should use `app:` or `samples:` references. Library names and
`name:path` references: [labs-and-libraries.md](labs-and-libraries.md).

---

## Running agents with observability

Use **`mas-ctl chat`** for single-agent runs:

```bash
mas-ctl -v chat my-agent.yaml -q "Your question here"
mas-ctl chat my-agent.yaml -q "Your question here" --trace
```

Example from Tutorial 1:

```bash
cd docs/tutorials/01-building-an-agent
mas-ctl -v chat agent.yaml -o overlays/tools.yaml \
  -q "What is the capital of France?"
```

Add `--events` to write `events.jsonl`, the machine-readable trace of the run
(one JSON event per line). See [cli/observability.md](cli/observability.md).
Exchange-log flags (`--trace`, `--trace full`, `--trace-color`): [mas-ctl.md](cli/mas-ctl.md#exchange-log).

## Running tools through MCP

MCP (Model Context Protocol) lets an agent call tools served by a separate
process. A tool can run in one of two ways:

- **Local provider:** the tool runs in the agent's process.
- **MCP provider:** a separate MCP server process runs the tool.

The runtime uses local tools by default and loads MCP servers from infra (the
infrastructure bundle that says where things are). Moving a tool from local to
MCP does not change the agent spec.

`mas-mcp` comes with `mas-library-ioa`. Serve a tool manifest, then point the
runtime at that server with an infra `ToolServerRegistry`:

```bash
mas-mcp serve \
  --tool-manifest library-samples/tools/web-search.tool.yaml \
  --tool web-search \
  --host 127.0.0.1 \
  --port 9001 \
  --transport streamable-http
```

```bash
mcp version
mas-mcp tools list --url http://127.0.0.1:9001/mcp
```

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/tools.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/skills.yaml \
  --infra-ref ../../../library-samples/infra/mcp-localhost.yaml \
  --infra-ref ../../../library-samples/infra/local-tools.yaml \
  -q "What is the current price of Apple stock?" \
  --trace
```

When the agent uses the tool, the `mas-mcp serve` process logs
`MCP tool call name=web-search`. The runtime discovers MCP tool names at start-up.
Local tools such as Tutorial 1's `calc` stay in-process.

Use MCP when infra should own the tool or when the tool needs its own process.
Use a local provider when you debug, because everything runs in one process. The
agent sees the same interface in both modes.

See [Open standards → MCP](standards/mcp.md),
[ToolContract](references/tool-contract.md), [kind: Tool](manifests/tool.md), and
infra [`ToolServerRegistry`](references/tool-server-registry.md)
(`library-samples/infra/mcp-localhost.yaml`).

## Running agents through A2A

A2A (Agent2Agent) is a protocol for agent-to-agent and user-to-agent calls. It
is independent of MCP tools and of the LLM's delegation decision.

Expose one agent from its manifest. The listen URL is the Application endpoint
keyed by `metadata.name` (`usage: listen`):

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "Hello"
```

`mas-ctl chat`, `tui`, and `run-mas` with the same `--infra-ref` bind that URL
in-process. `serve` is the dedicated blocking process.

In a MAS, `delegates_to` names the allowed target. The infra manifest chooses
local bus or A2A by matching the endpoint name to the agent id. Public exposure
is also an infra endpoint setting. See [Open standards → A2A](standards/a2a.md)
and [A2A internals](a2a/developer.md).

---

## Configuration

Machine-wide paths: [user-config.md](user-config.md).
Workspace YAML fields: [config.yaml reference](references/config.yaml.md).
CLI flags: [command-line index](cli/index.md) · [mas-ctl.md](cli/mas-ctl.md).

Runtime contributor docs are under [`runtime/docs/`](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/index.md)
(Mealy envelope, contracts, design patterns).

The repository ships the [tutorials](tutorials/index.md), three paper labs under
`labs/`, and the v2 Mealy runtime kernel.

Next: [Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md).

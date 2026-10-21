<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# User guide

This page shows how to install MAS Lab and run agents, multi-agent teams, and
experiments. Read it after the [home page](index.md).

You declare agents and experiments in YAML files (manifests). `mas-ctl` runs
agents and teams. `mas-lab` runs experiments and analyses their results with
benchmark pipelines.

**Related:** [Architecture](architecture.md) · [Labs](labs.md) · [Labs vs libraries](labs-and-libraries.md) ·
[How to write manifests](manifests/writing-manifests.md) ·
[CLI](cli/index.md) · [mas-ctl options](cli/mas-ctl.md) ·
[config.yaml](references/config.yaml.md) ·
[References](references/index.md) · [Web UI](ui/index.md) ·
[Live dashboard](ui/live.md) ·
[Run logs](cli/observability.md) · [Paper labs](paper/index.md) · [Glossary](glossary.md)

---

## Ways to work with MAS Lab

Every entry point needs a working environment first. Complete
**[Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md)**
once (Docker or developer install, LLM credentials). It covers the CLI and the
web UI.

### Hands-on tutorials

Learn step by step: one agent, then a team, then an experiment.

| | Tutorial | What you will do |
| --- | ---------- | ------------------ |
| 0 | [Environment setup](tutorials/00-environment-setup/README.md) | Install and verify your environment |
| 1 | [Build an agent](tutorials/01-building-an-agent/README.md) | Author a manifest and chat with your agent |
| 2 | [Orchestrate your MAS](tutorials/02-creating-a-mas/README.md) | Define a team and run it end to end |
| 3 | [Run an experiment](tutorials/03-experiments-and-analysis/README.md) | Run a benchmark and inspect results |

Tutorials 4–14 cover MCP, A2A, skills, telemetry, knowledge graphs, evaluation,
sessions, subagents, control, and governance. See the [tutorials index](tutorials/index.md).

### Paper labs

Reproduce the experiments of the MAS Lab article. Three labs under `labs/`
match Section 5 of the paper.

[Paper labs & reproducibility](paper/index.md)

### Web UI

Design agents and experiments, browse manifests, and inspect benchmark runs in a
browser. It starts with the Docker stack from Tutorial 0.

[Web UI guide](ui/index.md)

### Live dashboard

Shows topology, the event log, metrics, and chat for a running session or a
finished run. It is separate from the authoring web UI.

[Live dashboard](ui/live.md)

---

## Command-line tools

| CLI | Package | Use for |
| ----- | --------- | --------- |
| `mas-ctl` | `ctl/` | Chat, TUI, compile, compose, validate, `run-mas`, `serve` — [options](cli/mas-ctl.md) |
| `mas-lab` | `lab/` | Benchmarks, pipelines, telemetry, plots, `init` — [CLI overview](cli/index.md#mas-lab) |
| `mas-mcp` | `library-ioa/` | Serve a tool manifest over MCP and call MCP servers (see [MCP](#running-tools-through-mcp)) |

`mas-runtime` (`runtime/`) is the kernel library you embed in Python. Its
`mas-runtime` console script only prints a pointer to `mas-ctl`.

Benchmarking is always `mas-lab`, never `mas-ctl`. Full map: [cli/index.md](cli/index.md).

---

## Included libraries

- `mas-runtime` — kernel library (contracts, plugins, design patterns)
- `mas-ctl` — control plane CLI
- `mas-lab` — benchmarks and pipelines (pulls `mas-lab-core`, `mas-lab-bench`,
  `mas-lab-controller`, `mas-library-eval`)
- `mas-library-standard` — flavours, overlays, infra bundles
- `mas-library-samples` — sample apps and datasets
- `mas-library-lab` — lab extension plugins and standard plots
- `mas-library-skills`, `mas-library-ioa`, `mas-library-kg`,
  `mas-library-telemetry` — skills, MCP bridge, knowledge-graph and
  OpenTelemetry (trace standard) pipelines

A lab is a folder of experiments. A library is reusable code and YAML. See
[labs-and-libraries.md](labs-and-libraries.md) for when to create each. For the
ways to reference a manifest (inline, file, `LIBRARY:` id, `name@version`), see
[writing-manifests.md](manifests/writing-manifests.md).

Full package map: [libraries.md](libraries.md).

---

## Install

### Docker (recommended for new users)

```bash
git clone https://github.com/outshift-open/mas-lab.git && cd mas-lab
cd docker && cp .env.example .env
# Edit .env and set OPENAI_API_KEY if you plan to call a live model
docker compose build backend
```

Then follow [Tutorial 0](tutorials/00-environment-setup/README.md) for workspace
setup and your first `mas-ctl` commands.

Mount your project at `/workspace` and persistent data at `/data` via
`MAS_WORKSPACE_MOUNT` and `MAS_DATA_MOUNT` in `docker/.env`.
See [docker/README.md](https://github.com/outshift-open/mas-lab/blob/main/docker/README.md).

**Start the web UI** (same compose stack):

```bash
docker compose up --build
# UI http://localhost:8080 · API http://localhost:8090
```

See [Web UI](ui/index.md) for what you can do in the browser.

### From source (developers)

Install the packages normally and refer to sample apps by library reference
(`samples:apps/trip-planner/mas.yaml`). Use editable installs only when you
change MAS Lab itself.

Install from a development checkout:

```bash
uv sync --all-packages

# Optional but recommended: avoid prefixing every command with `uv run`
export PATH="$PWD/.venv/bin:$PATH"

# Verify command resolution
mas-runtime --help >/dev/null
mas-ctl --help >/dev/null
mas-lab --help >/dev/null
```

Editable installs per package when developing:

```bash
uv pip install -e runtime -e ctl -e library-standard -e library-lab \
  -e lab/components/core -e lab/components/bench -e lab/components/controller -e lab
```

Or use `task install` / `task install-dev` from the repository root (see
[DEVELOPMENT.md](https://github.com/outshift-open/mas-lab/blob/main/DEVELOPMENT.md)).

Install from a package index (not available until the first PyPI upload; use
Docker or a checkout until then):

```bash
uv pip install mas-lab mas-library-standard mas-library-samples
```

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

See the MCP library docs in [library-ioa/README.md](https://github.com/outshift-open/mas-lab/blob/main/library-ioa/README.md),
[ToolContract](references/tool-contract.md), [kind: Tool](manifests/tool.md), and
infra [`ToolServerRegistry`](references/tool-server-registry.md)
(`library-samples/infra/mcp-localhost.yaml`).

## Running agents through A2A

A2A (Agent2Agent) is a protocol for agent-to-agent and user-to-agent calls. It
is independent of MCP tools and of the LLM's delegation decision.

Expose one agent from its manifest. The listen URL is the Application endpoint
keyed by `metadata.name` (`usage: deploy`):

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "Hello"
```

`mas-ctl chat`, `tui`, and `run-mas` with the same `--infra-ref` bind that URL
in-process. `serve` is the dedicated blocking process.

In a MAS, `delegates_to` names the allowed target. The infra manifest chooses
local bus or A2A by matching the endpoint name to the agent id. Public exposure
is also an infra endpoint setting. See [A2A quickstart and feature reference](a2a/README.md) and the developer
[A2A architecture reference](a2a/developer.md).

---

## Configuration

Machine-wide paths: [user-config.md](user-config.md).
Workspace YAML fields: [config.yaml reference](references/config.yaml.md).
CLI flags: [mas-ctl.md](cli/mas-ctl.md).

Runtime contributor docs are under [`runtime/docs/`](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/index.md)
(Mealy envelope, contracts, design patterns).

The repository ships the [tutorials](tutorials/index.md), three paper labs under
`labs/`, and the v2 Mealy runtime kernel.

Next: [Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md).

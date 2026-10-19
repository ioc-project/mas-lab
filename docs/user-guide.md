<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# User guide

How to install and use MAS Lab: single agents, multi-agent teams, batch
experiments, and optional libraries.

MAS Lab is a **specification-driven** toolkit. You declare agents and experiments
in YAML, run them with `mas-ctl` and `mas-lab`, and analyze results through
reusable benchmark pipelines.

**Related:** [Labs vs libraries](labs-and-libraries.md) ·
[How to write manifests](manifests/writing-manifests.md) ·
[CLI](cli/index.md) · [mas-ctl options](cli/mas-ctl.md) ·
[config.yaml](references/config.yaml.md) ·
[References](references/index.md) · [Web UI](ui/index.md) ·
[Live dashboard](ui/live.md) ·
[Run logs](cli/observability.md) · [Paper labs](paper/index.md) · [Glossary](glossary.md)

---

## Ways to work with MAS Lab

Several entry points fit different goals. All of them need a working environment first —
complete **[Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md)** once
(Docker or developer install, LLM credentials). The same setup covers the CLI and the
optional web UI.

### Hands-on tutorials

Learn the toolkit step by step: one agent, then a multi-agent team, then a full
benchmark experiment.

| | Tutorial | What you will do |
| --- | ---------- | ------------------ |
| 0 | [Environment setup](tutorials/00-environment-setup/README.md) | Install and verify your environment |
| 1 | [Build an agent](tutorials/01-building-an-agent/README.md) | Author a manifest and chat with your agent |
| 2 | [Orchestrate your MAS](tutorials/02-creating-a-mas/README.md) | Define a team and run it end to end |
| 3 | [Run an experiment](tutorials/03-experiments-and-analysis/README.md) | Run a benchmark and inspect results |

[Tutorials index](tutorials/index.md)

### Paper labs

Reproduce the experiments from the MAS-Lab article — three labs under `labs/` that
match Section 5 of the paper.

[Paper labs & reproducibility](paper/index.md)

### Web UI

Design agents and experiments, browse manifests, and inspect benchmark runs in the
browser. Started as part of Tutorial 0 (same Docker stack).

[Web UI guide](ui/index.md)

### Live dashboard

Live topology, native event log, metrics, and chat for a running session or a completed run.
Separate from the authoring studio.

[Live dashboard](ui/live.md)

---

## Three CLIs

| CLI | Package | Use for |
| ----- | --------- | --------- |
| `mas-ctl` | `ctl/` | Chat, TUI, compile, compose, validate, `run-mas` — [options](cli/mas-ctl.md) |
| `mas-runtime` | `runtime/` | Library only (no CLI) |
| `mas-lab` | `lab/` | Benchmarks, pipelines, telemetry — [CLI overview](cli/index.md#mas-lab) |

Benchmarking is always **`mas-lab`**, not `mas-ctl`. Full map: [cli/index.md](cli/index.md).

---

## Included libraries

- `mas-runtime`
- `mas-lab`
- `mas-ctl`
- `mas-library-standard`
- `mas-library-lab`

A lab is an experiment pack; a library is reusable code and YAML. When to
create each, including a local library inside a lab:
[labs-and-libraries.md](labs-and-libraries.md). How to reference manifests
(inline, file, `LIBRARY:` id, `name@version`):
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
See `docker/README.md` in the repository.

**Start the web UI** (same compose stack):

```bash
docker compose up --build
# UI http://localhost:8080 · API http://localhost:8090
```

See [Web UI](ui/index.md) for what you can do in the browser.

### From source (developers)

Recommended consumption model:

1. Install versioned OSS packages normally.
2. Use packaged sample apps through their canonical `pkg://` URIs.
3. Only switch to editable multi-package development when you are modifying the OSS code itself.

Typical install from a development checkout:

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
uv pip install -e runtime -e lab -e ctl -e library-standard -e library-lab
```

Typical install from a package index:

```bash
uv pip install mas-lab
```

---

## Sample apps

Canonical sample app references:

- `app: trip-planner` — resolves `mas.yaml` via `mas.apps` (from `mas-library-samples`)
- `samples:apps/trip-planner/mas.yaml` — manifest library scheme ref

When you work from a source checkout, relative paths under `library-samples/` or
tutorial directories also work. Prefer `app:` or library scheme refs in
committed labs so paths stay stable across machines.

---

## Typical workflow

1. Build or run an agent with runtime.
2. Orchestrate multi-agent setup with ctl.
3. Run experiments with lab.
4. Add standard/library-lab extensions as needed.

---

## Referencing apps from labs

When a lab needs to run an app, use one of these three patterns.

### 1. Library app via `app:` or `samples:` scheme (recommended)

```yaml
experiment:
  application:
    app: trip-planner
    configs_dir: ./overlays
```

### 2. Absolute path inside the current workspace

Use for local development when the app lives in the same checkout but is not
packaged yet.

### 3. Symlink through a local library-like folder

Use only for local composition — not for committed OSS labs.

For committed reusable OSS examples, prefer `app:` or `samples:` scheme refs.
Library names and `name:path` refs: [labs-and-libraries.md](labs-and-libraries.md).

---

## Running agents with observability

Use **`mas-ctl chat`** for single-agent runs:

```bash
mas-ctl chat my-agent.yaml -q "Your question here" -v
mas-ctl chat my-agent.yaml -q "Your question here" --trace
```

Example from Tutorial 1:

```bash
cd docs/tutorials/01-building-an-agent
mas-ctl chat agent.yaml -o overlays/tools.yaml \
  -q "What is the capital of France?" -v
```

See [cli/observability.md](cli/observability.md) for `events.jsonl`.
Exchange-log flags (`--trace`, `--trace full`, `--trace-color`): [mas-ctl.md](cli/mas-ctl.md#exchange-log).

## Running tools through MCP

MAS Lab supports two tool deployment patterns without changing the agent-facing contract:

- **local provider** — the tool runs in-process
- **MCP provider** — the tool is exposed by a separate MCP server process

The runtime resolves local tools by default and loads remote MCP servers from
infra. The agent spec remains unchanged when switching a tool from local
execution to an MCP endpoint.

Serve a MAS tool manifest, then point the runtime at that server using an infra
`ToolServerRegistry`:

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

The `mas-mcp serve` process logs `MCP tool call name=web-search` when the agent
uses the tool. MCP tool names are discovered at runtime initialization; local
tools such as Tutorial 1's `calc` remain in-process.

This runs the tool in a dedicated process and exposes it to the MAS runtime via MCP, which is the preferred option when you want infra-owned tooling or a clean separation between the agent process and the tool implementation.

A local tool provider is still the easiest debug path when the tool is part of the same process. The same logical agent interface works in both modes.

See the MCP library docs in [../library-ioa/README.md](../library-ioa/README.md),
[ToolContract](references/tool-contract.md), [kind: Tool](manifests/tool.md), and
infra [`ToolServerRegistry`](references/tool-server-registry.md)
(`library-samples/infra/mcp-localhost.yaml`).

## Running tools through MCP

MAS Lab supports two tool deployment patterns without changing the agent-facing contract:

- **local provider** — the tool runs in-process
- **MCP provider** — the tool is exposed by a separate MCP server process

The runtime resolves both through the provider registry, so the agent still calls by tool name and arguments.

```yaml
providers:
  - name: math-tools
    kind: mcp
    transport: stdio
    command: python
    args:
      - -c
      - |
        from mcp.server import MCPServer
        mcp = MCPServer("math-tools")

        @mcp.tool()
        def add(a: int, b: int) -> int:
            return a + b

        mcp.run("stdio")
```

This starts the tool in a dedicated process and exposes it to the MAS runtime via MCP, which is the preferred option when you want infra-owned tooling or a clean separation between the agent process and the tool implementation.

A local tool provider is still the easiest debug path when the tool is part of the same process. The same logical agent interface works in both modes.

See the MCP library docs in [../library-ioa/README.md](../library-ioa/README.md) and [../library-ioa/docs/tutorials/01-mcp-tools/README.md](../library-ioa/docs/tutorials/01-mcp-tools/README.md).

## Running agents through A2A

A2A is the agent-to-agent and user-to-agent protocol surface. It is independent
from MCP tools and from the LLM's delegation decision.

Expose one manifest-backed agent. The listen URL is the Application
endpoint keyed by `metadata.name` (`usage: deploy`):

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "Hello"
```

`mas-ctl chat` / `tui` / `run-mas` with the same `--infra-ref` bind that URL
in-process for interrogation; `serve` is the dedicated blocking process.

In a MAS, `delegates_to` names the allowed target. The deployment infra
manifest selects local bus or A2A reachability by matching the endpoint name to
the agency agent id. Public exposure is also an infra endpoint concern.
See [A2A quickstart and feature reference](a2a/README.md) and the developer
[A2A architecture reference](a2a/developer.md).

---

## Configuration

Machine-wide paths: [user-config.md](user-config.md).
Workspace YAML fields: [config.yaml reference](references/config.yaml.md).
CLI flags: [mas-ctl.md](cli/mas-ctl.md).

Runtime contributor docs live under [`runtime/docs/`](../runtime/docs/index.md)
(Mealy envelope, contracts, design patterns).

The public repository ships tutorials 0–3, three paper labs under `labs/`, and
the v2 Mealy runtime kernel.

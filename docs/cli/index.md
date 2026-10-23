<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Command line

MAS Lab ships three console scripts and a small set of HTTP and browser
surfaces. This page is the topic index. Flag tables and command indexes live
under [References → CLI](../references/index.md#cli). `COMMAND --help` wins
when a page and the code disagree.

| Surface | Package | Use it for | Docs |
|---------|---------|------------|------|
| [`mas-runtime`](mas-runtime.md) | `runtime/` | None. The script only points at `mas-ctl`. Embed the library in Python. | [mas-runtime](mas-runtime.md) |
| [`mas-ctl`](mas-ctl.md) | `ctl/` | Chat, TUI, validate, compile, compose, `run-mas`, A2A `serve`, control | [mas-ctl](mas-ctl.md) |
| [`mas-lab`](mas-lab.md) | `lab/` | Workspace `init`, benchmarks, pipelines, telemetry, plots, `kg`, `eval` | [mas-lab](mas-lab.md) |
| `mas-mcp` | `library-ioa/` | Serve a tool manifest over MCP, or call an MCP server | [MCP tutorial](../tutorials/04-mcp-tools/README.md) |
| [Web UI](../ui/index.md) | `ui/` + controller | Browser studio: manifests, labs, benchmark jobs | [Web UI](../ui/index.md) |
| [Live dashboard](../ui/live.md) | `demo-ui/` | Watch a running MAS or replay a finished run | [Live dashboard](../ui/live.md) |
| [TUI](../ctl/tui.md) | `ctl/` | Interactive session in the terminal (`mas-ctl tui`) | [TUI](../ctl/tui.md) |
| [Controller API](serve.md) | `mas-lab-controller` | REST backend for the web UI and the live dashboard | [Controller API](serve.md) |

`mas-ctl --help` / `mas-lab --help` / `mas-lab COMMAND --help` are always
current. Installed libraries add `mas-lab` groups (`telemetry`, `kg`, `eval`,
`demo-ui`, `replay`).

---

## `mas-runtime`

The kernel is a library. The `mas-runtime` console script exits 2 and prints
where to go. Use [`mas-ctl`](mas-ctl.md) for sessions and [`mas-lab`](mas-lab.md)
for experiments. [Details](mas-runtime.md).

---

## `mas-ctl`

Runs one agent or one MAS from YAML.

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia"
mas-ctl validate agent.yaml
```

| Command | Purpose |
|---------|---------|
| `chat` | One agent, scripted (`-q`) or interactive (`-i`) |
| `run-mas` | Compose a team and open a session on the entry agent |
| `tui` | Curses UI — [TUI guide](../ctl/tui.md) |
| `serve` | Blocking A2A listener for one agent (not the lab controller) |
| `validate` / `schemas` | Schema-check manifests; list kinds |
| `compile` / `compose` / `plan` | Resolved spec, bind, placement |
| `checkpoint …` | List, show, fork, resume |
| `control …` | Attach, pause, steer a live session |
| `plugin …` / `registry …` / `infra list` | Introspect plugins and bundles |

Flag tables: [mas-ctl.md](mas-ctl.md). Compile walkthrough: [compile.md](compile.md).
`--override` syntax: [overrides.md](overrides.md). `events.jsonl` and the
exchange log: [observability.md](observability.md).

---

## `mas-lab`

Workspace bootstrap, experiments, and analysis. Benchmarking is always
`mas-lab`, never `mas-ctl`.

```bash
mas-lab init
mas-lab config
mas-lab benchmark run experiment.yaml
mas-lab telemetry show traces/events.jsonl
```

| Command | Purpose |
|---------|---------|
| `init` / `config` | User or `--local` project config; print resolved paths |
| `check` / `validate` | Schema-check a MAS or lab YAML |
| `benchmark …` | `run`, `list`, `show`, `follow`, `analyze`, `artifact`, `pipeline`, `step`, `export` / `import`, `clean` |
| `telemetry …` / `otel` | Inspect, convert, verify, push native or OTel traces |
| `plot …` | Trajectory and communication figures |
| `kg …` | Build and check a knowledge graph from a trace |
| `eval` | Score a trace with MCE metrics |
| `run` / `pipe` | Processor and pipeline helpers |
| `serve` / `control` / `worker` | Controller HTTP API — [Controller API](serve.md) |
| `demo-ui` / `replay` | Offline live-dashboard export and replay — [Live dashboard](../ui/live.md) |

Command index (pointers to man pages in the repo): [mas-lab.md](mas-lab.md).

---

## Interfaces

| Surface | How you start it | Talks to |
|---------|------------------|----------|
| [Web UI](../ui/index.md) | `docker compose up` or `task start` → http://localhost:8080 | Controller on :8090 |
| [Live dashboard](../ui/live.md) | `task demo-ui` → http://127.0.0.1:8090/ | Same controller; `mas-lab demo-ui export` writes a static copy |
| [TUI](../ctl/tui.md) | `mas-ctl tui agent.yaml` | The runtime in-process |
| [Controller API](serve.md) | `mas-lab serve --host 127.0.0.1 --port 8090` | Web UI, live dashboard, scripts |

`mas-ctl serve` is a different process: it exposes **one agent** over A2A.
`mas-lab serve` is the **lab controller**. Do not mix them.

---

## Outputs of a run

| Stream | Reader | How you get it |
|--------|--------|----------------|
| stdout | You | Conversation (`You:` / `Agent:`) |
| stderr exchange log | You | `mas-ctl --trace` or `mas_ctl.trace` in `config.yaml` |
| `events.jsonl` | Pipelines, plots, `mas-lab telemetry` | `--events`, overlay, or `spec.observability` |
| Controller REST | Web UI, live dashboard | `mas-lab serve` |

Score experiments from `events.jsonl`, never from the exchange log.
Details: [observability.md](observability.md). Workspace file:
[config.yaml](../references/config.yaml.md).

---

## Related

- [User guide](../user-guide.md)
- [References: CLI](../references/index.md#cli)
- [User configuration](../user-config.md)
- [Tutorial 0](../tutorials/00-environment-setup/README.md)
- [Surfaces](../what-is/surfaces.md)
